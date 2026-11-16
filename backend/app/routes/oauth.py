"""OAuth 2.1 authorization server for remote MCP clients.

This is how claude.ai, ChatGPT and other browser-hosted MCP clients connect to
``POST /api/v1/mcp``. They can't be handed an API key to paste into a config
file, so they follow the MCP authorization spec instead:

1. ``POST /mcp`` without a token → 401 whose ``WWW-Authenticate`` header points
   at the protected-resource metadata (RFC 9728).
2. That names this server as the authorization server; the client reads
   ``/.well-known/oauth-authorization-server`` (RFC 8414) and registers
   itself at ``/oauth/register`` (RFC 7591).
3. The user is sent to ``/oauth/authorize``, which parks the request and
   redirects to the SPA consent page. The user signs in to Nubi if needed,
   picks which of their orgs to connect, and approves.
4. The client swaps the code for tokens at ``/oauth/token`` (PKCE S256).

From then on the access token resolves exactly like an API key — same user,
same org pin, same role and RLS — see :mod:`app.auth.deps`. State and the
security properties live in :mod:`app.auth.oauth`.

Endpoints
---------
Unauthenticated, called by the client:
    GET  /.well-known/oauth-protected-resource[/api/v1/mcp]
    GET  /.well-known/oauth-authorization-server
    POST /api/v1/oauth/register
    GET  /api/v1/oauth/authorize
    POST /api/v1/oauth/token
    POST /api/v1/oauth/revoke
Signed-in user, called by the SPA:
    GET    /api/v1/oauth/requests/{id}            consent-screen details
    POST   /api/v1/oauth/requests/{id}/decision   approve / deny
    GET    /api/v1/oauth/connections              connected apps in this org
    DELETE /api/v1/oauth/connections/{id}         disconnect one
"""

from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.audit import record_audit
from app.auth import oauth as o
from app.auth.deps import current_user
from app.config import get_settings
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id
from app.routes.orgs import _get_user_org_membership, _list_user_orgs

logger = logging.getLogger(__name__)

oauth_router = APIRouter(prefix="/oauth", tags=["oauth"])
#: Mounted at the app root — RFC 8414 / 9728 fix these paths at the origin root.
wellknown_router = APIRouter(tags=["oauth"])

MCP_PATH = "/api/v1/mcp"

#: Redirect hosts of clients we recognise. Dynamic registration lets any client
#: call itself anything, so the consent screen trusts the redirect host — which
#: decides where the code goes — over the self-declared name.
KNOWN_CLIENT_HOSTS = {
    "claude.ai": "Claude",
    "claude.com": "Claude",
    "chatgpt.com": "ChatGPT",
    "chat.openai.com": "ChatGPT",
}


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def public_base(request: Request) -> str:
    """This API's public origin — the OAuth issuer."""
    configured = (get_settings().PUBLIC_API_URL or "").strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


def mcp_resource(request: Request) -> str:
    return public_base(request) + MCP_PATH


def resource_metadata_url(request: Request) -> str:
    return public_base(request) + "/.well-known/oauth-protected-resource" + MCP_PATH


def _resource_ok(request: Request, resource: str | None) -> bool:
    """RFC 8707: a requested resource must be our MCP endpoint."""
    if not resource:
        return True
    want, got = urlsplit(mcp_resource(request)), urlsplit(resource)
    return (got.scheme, got.netloc.lower(), got.path.rstrip("/")) == (
        want.scheme, want.netloc.lower(), want.path.rstrip("/")
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _protected_resource(request: Request) -> dict[str, Any]:
    return {
        "resource": mcp_resource(request),
        "authorization_servers": [public_base(request)],
        "scopes_supported": [o.MCP_SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Nubi",
    }


@wellknown_router.get("/.well-known/oauth-protected-resource")
@wellknown_router.get("/.well-known/oauth-protected-resource/api/v1/mcp")
async def protected_resource_metadata(request: Request) -> dict[str, Any]:
    return _protected_resource(request)


@wellknown_router.get("/.well-known/oauth-authorization-server")
@wellknown_router.get("/.well-known/openid-configuration")
async def authorization_server_metadata(request: Request) -> dict[str, Any]:
    base = public_base(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/api/v1/oauth/authorize",
        "token_endpoint": f"{base}/api/v1/oauth/token",
        "registration_endpoint": f"{base}/api/v1/oauth/register",
        "revocation_endpoint": f"{base}/api/v1/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": list(o.AUTH_METHODS),
        "revocation_endpoint_auth_methods_supported": list(o.AUTH_METHODS),
        "scopes_supported": [o.MCP_SCOPE],
    }


# ---------------------------------------------------------------------------
# Dynamic client registration (RFC 7591)
# ---------------------------------------------------------------------------


def _oauth_error(error: str, description: str, status: int = 400,
                 headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers={"Cache-Control": "no-store", **(headers or {})},
    )


@oauth_router.post("/register")
async def register_client(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _oauth_error("invalid_client_metadata", "Body must be JSON.")
    if not isinstance(body, dict):
        return _oauth_error("invalid_client_metadata", "Body must be a JSON object.")

    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > o.MAX_REDIRECT_URIS:
        return _oauth_error(
            "invalid_redirect_uri",
            f"redirect_uris must list 1-{o.MAX_REDIRECT_URIS} URIs.",
        )
    for uri in uris:
        problem = o.validate_redirect_uri(uri)
        if problem:
            return _oauth_error("invalid_redirect_uri", problem)

    method = body.get("token_endpoint_auth_method") or "none"
    if method not in o.AUTH_METHODS:
        return _oauth_error(
            "invalid_client_metadata",
            f"token_endpoint_auth_method must be one of {list(o.AUTH_METHODS)}.",
        )
    grant_types = body.get("grant_types") or ["authorization_code", "refresh_token"]
    if not set(grant_types) <= {"authorization_code", "refresh_token"}:
        return _oauth_error("invalid_client_metadata", "Unsupported grant_types.")
    response_types = body.get("response_types") or ["code"]
    if response_types != ["code"]:
        return _oauth_error("invalid_client_metadata", "Only response_type 'code' is supported.")

    name = str(body.get("client_name") or "").strip()[: o.MAX_CLIENT_NAME]
    if not name:
        name = urlsplit(uris[0]).hostname or "AI app"

    client, secret = await o.get_oauth_store().register_client(name, uris, method)
    created = client.get("created_at")
    out: dict[str, Any] = {
        "client_id": client["id"],
        "client_id_issued_at": int(created.timestamp()) if hasattr(created, "timestamp") else None,
        "client_name": name,
        "redirect_uris": uris,
        "grant_types": grant_types,
        "response_types": ["code"],
        "token_endpoint_auth_method": method,
        "scope": o.MCP_SCOPE,
    }
    if secret:
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0
    return JSONResponse(out, status_code=201, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Authorization endpoint
# ---------------------------------------------------------------------------


def _redirect_with(redirect_uri: str, params: dict[str, str | None]) -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    sep = "&" if urlsplit(redirect_uri).query else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{query}", status_code=302)


@oauth_router.get("/authorize")
async def authorize(request: Request):
    q = request.query_params
    client_id = q.get("client_id") or ""
    redirect_uri = q.get("redirect_uri") or ""
    store = o.get_oauth_store()

    # Until client and redirect_uri check out, an error must NOT redirect —
    # that would make us an open redirector (OAuth 2.1 §4.1.2.1).
    client = await store.get_client(client_id) if client_id else None
    if client is None:
        return _oauth_error("invalid_client", "Unknown client_id.")
    if not redirect_uri and len(client["redirect_uris"]) == 1:
        redirect_uri = client["redirect_uris"][0]
    if not o.redirect_uri_matches(list(client["redirect_uris"]), redirect_uri):
        return _oauth_error("invalid_request", "redirect_uri is not registered for this client.")

    state = q.get("state")

    def fail(error: str, description: str) -> RedirectResponse:
        return _redirect_with(redirect_uri, {
            "error": error, "error_description": description, "state": state,
            "iss": public_base(request),
        })

    if q.get("response_type") != "code":
        return fail("unsupported_response_type", "response_type must be 'code'.")
    challenge = q.get("code_challenge") or ""
    if not challenge or q.get("code_challenge_method") != "S256":
        return fail("invalid_request", "PKCE with code_challenge_method=S256 is required.")
    if not (43 <= len(challenge) <= 128):
        return fail("invalid_request", "code_challenge is malformed.")
    # Whatever scope was asked for, the grant is MCP_SCOPE: clients commonly
    # send generic ones ("openid", "offline_access") and there is only one
    # thing to grant. The token response reports the scope actually issued.
    resource = q.get("resource")
    if not _resource_ok(request, resource):
        return fail("invalid_target", "resource must be this server's MCP endpoint.")

    request_id = await store.create_auth_request(
        client_id=client_id, redirect_uri=redirect_uri, code_challenge=challenge,
        scope=o.MCP_SCOPE, state=state, resource=resource,
    )
    frontend = get_settings().FRONTEND_URL.rstrip("/")
    return RedirectResponse(f"{frontend}/oauth/consent?request={request_id}", status_code=302)


# ---------------------------------------------------------------------------
# Consent (SPA, signed-in user)
# ---------------------------------------------------------------------------


def _require_interactive_session(user: dict[str, Any]) -> None:
    """Only a signed-in person may approve a connection.

    An API key or another app's OAuth token approving a NEW grant would let
    one credential quietly mint others, so those are refused here.
    """
    if user.get("_api_key_org_id"):
        raise AppError("forbidden", "Sign in to Nubi to approve a connection.", 403)


async def _load_request(request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    store = o.get_oauth_store()
    req = await store.get_auth_request(request_id)
    if req is None:
        raise AppError(
            "oauth_request_expired",
            "This connection request has expired. Start again from your AI app.",
            404,
        )
    client = await store.get_client(req["client_id"])
    if client is None:
        raise AppError("oauth_request_expired", "This connection request is no longer valid.", 404)
    return req, client


@oauth_router.get("/requests/{request_id}")
async def get_consent_request(
    request_id: str, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    _require_interactive_session(user)
    req, client = await _load_request(request_id)
    host = urlsplit(req["redirect_uri"]).hostname or ""
    known = KNOWN_CLIENT_HOSTS.get(host)
    return {
        "client_name": client["client_name"],
        "redirect_host": host,
        "verified_as": known,
        # Claude Code, Cursor and other desktop tools receive the code on a
        # loopback address: it never leaves this computer, so the consent
        # screen says so instead of warning about an unknown website.
        "local_app": o._is_loopback(host),
        "scope": req["scope"],
        "orgs": await _list_user_orgs(str(user["id"])),
    }


class ConsentDecision(BaseModel):
    approve: bool
    org_id: str | None = None


@oauth_router.post("/requests/{request_id}/decision")
async def decide_consent_request(
    request_id: str,
    body: ConsentDecision,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_interactive_session(user)
    req, client = await _load_request(request_id)
    store = o.get_oauth_store()
    iss = public_base(request)

    if not body.approve:
        await store.delete_auth_request(request_id)
        resp = _redirect_with(req["redirect_uri"], {
            "error": "access_denied", "error_description": "The user declined.",
            "state": req.get("state"), "iss": iss,
        })
        return {"redirect_to": resp.headers["location"]}

    if not body.org_id:
        raise AppError("invalid_request", "Choose an organisation to connect.", 400)
    membership = await _get_user_org_membership(str(user["id"]), body.org_id)
    if membership is None:
        raise AppError("forbidden", "You are not a member of that organisation.", 403)

    # Single-use: delete before issuing so a double-submit can't mint two codes.
    await store.delete_auth_request(request_id)
    code = await store.create_grant_and_code(
        client_id=client["id"], user_id=str(user["id"]), org_id=body.org_id,
        scope=req["scope"], redirect_uri=req["redirect_uri"],
        code_challenge=req["code_challenge"],
    )
    await record_audit(
        org_id=body.org_id,
        actor_user_id=str(user["id"]),
        actor_kind="access",
        action="oauth.connect",
        resource_type="oauth_client",
        resource_id=client["id"],
        summary={"client_name": client["client_name"],
                 "redirect_host": urlsplit(req["redirect_uri"]).hostname},
    )
    request.state.audit_logged = True
    resp = _redirect_with(req["redirect_uri"], {"code": code, "state": req.get("state"), "iss": iss})
    return {"redirect_to": resp.headers["location"]}


# ---------------------------------------------------------------------------
# Token + revocation endpoints
# ---------------------------------------------------------------------------


async def _authenticate_client(request: Request, form: Any) -> dict[str, Any] | None:
    """Identify the client from Basic auth or the form; verify its secret."""
    client_id, secret = form.get("client_id"), form.get("client_secret")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:].strip()).decode()
            basic_id, _, basic_secret = decoded.partition(":")
            client_id, secret = unquote(basic_id), unquote(basic_secret)
        except Exception:  # noqa: BLE001
            return None
    if not client_id:
        return None
    client = await o.get_oauth_store().get_client(str(client_id))
    if client is None or not o.client_secret_ok(client, secret):
        return None
    return client


def _token_response(access: str, refresh: str) -> JSONResponse:
    return JSONResponse(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": int(o.ACCESS_TOKEN_TTL.total_seconds()),
            "refresh_token": refresh,
            "scope": o.MCP_SCOPE,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@oauth_router.post("/token")
async def token(request: Request) -> JSONResponse:
    form = await request.form()
    client = await _authenticate_client(request, form)
    if client is None:
        return _oauth_error("invalid_client", "Client authentication failed.", 401)
    store = o.get_oauth_store()
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code":
        redeemed = await store.redeem_code(str(form.get("code") or ""))
        if redeemed is None:
            return _oauth_error("invalid_grant", "The code is invalid, expired or already used.")
        if redeemed["client_id"] != client["id"]:
            # A code presented by a client it wasn't issued to is a stolen code.
            await store.revoke_grant(redeemed["grant_id"], redeemed["user_id"], redeemed["org_id"])
            return _oauth_error("invalid_grant", "The code was not issued to this client.")
        if form.get("redirect_uri") and form.get("redirect_uri") != redeemed["redirect_uri"]:
            return _oauth_error("invalid_grant", "redirect_uri does not match the authorization request.")
        if not o.verify_pkce(str(form.get("code_verifier") or ""), redeemed["code_challenge"]):
            return _oauth_error("invalid_grant", "PKCE verification failed.")
        access, refresh = await store.issue_tokens(redeemed["grant_id"])
        return _token_response(access, refresh)

    if grant_type == "refresh_token":
        grant = await store.rotate_refresh(str(form.get("refresh_token") or ""))
        if grant is None:
            return _oauth_error("invalid_grant", "The refresh token is invalid, expired or revoked.")
        if grant["client_id"] != client["id"]:
            await store.revoke_grant(grant["grant_id"], grant["user_id"], grant["org_id"])
            return _oauth_error("invalid_grant", "The refresh token was not issued to this client.")
        access, refresh = await store.issue_tokens(grant["grant_id"])
        return _token_response(access, refresh)

    return _oauth_error("unsupported_grant_type", "grant_type must be authorization_code or refresh_token.")


@oauth_router.post("/revoke")
async def revoke(request: Request) -> JSONResponse:
    form = await request.form()
    client = await _authenticate_client(request, form)
    if client is None:
        return _oauth_error("invalid_client", "Client authentication failed.", 401)
    raw = str(form.get("token") or "")
    if raw:
        await o.get_oauth_store().revoke_token(raw, client["id"])
    # RFC 7009 §2.2: 200 whether or not the token existed.
    return JSONResponse({}, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Connected apps (Settings)
# ---------------------------------------------------------------------------


@oauth_router.get("/connections")
async def list_connections(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    return {"connections": await o.get_oauth_store().list_grants(str(user["id"]), org_id)}


@oauth_router.delete("/connections/{grant_id}", status_code=204)
async def delete_connection(
    grant_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    if not await o.get_oauth_store().revoke_grant(grant_id, str(user["id"]), org_id):
        raise AppError("not_found", "Connection not found.", 404)
    await record_audit(
        org_id=org_id, actor_user_id=str(user["id"]), actor_kind="access",
        action="oauth.disconnect", resource_type="oauth_grant", resource_id=grant_id,
        summary={},
    )
    request.state.audit_logged = True


api_router.include_router(oauth_router)
