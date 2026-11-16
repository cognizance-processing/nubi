"""OAuth 2.1 authorization server state for remote MCP clients.

Browser-hosted MCP clients (claude.ai, ChatGPT) cannot be handed an API key to
paste into a config file; the MCP authorization spec has them discover an
OAuth server, register themselves, and send the user through a consent screen
instead. This module holds that state; :mod:`app.routes.oauth` holds the HTTP
surface.

What a user approves is a *grant* — ``(client, user, org)`` — and every code
and token hangs off one, so revoking the connection kills them all at once.

Security properties
-------------------
* Codes and tokens are opaque 256-bit values; only SHA-256 digests are stored
  (the same discipline as ``api_keys`` and ``sessions``).
* PKCE S256 is mandatory. Codes are single-use and live five minutes; a
  replayed code revokes the grant it was issued for (RFC 9700 §4.2.4).
* Refresh tokens rotate on every use. Presenting an already-rotated refresh
  token means it was copied, so the whole grant is revoked.
* An access token resolves to the same ``{user_id, org_id}`` binding an API
  key does, and only while the user is still a member of that org — removing
  someone from an org disconnects their AI apps on the next request.
* Access tokens are audience-bound to the MCP endpoint; see
  :func:`app.auth.deps._resolve_opaque_credential`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

#: Prefix for access tokens, so the bearer path can route them without a decode.
ACCESS_TOKEN_PREFIX = "nubi_oat_"
REFRESH_TOKEN_PREFIX = "nubi_ort_"
CLIENT_ID_PREFIX = "nubi_oc_"

ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=5)
AUTH_REQUEST_TTL = timedelta(minutes=15)

#: The one scope we issue. It means "act as me, in the org I picked, over MCP" —
#: exactly what an API key means, so there is nothing finer-grained to offer
#: that the user's own role in the org doesn't already decide.
MCP_SCOPE = "mcp"

MAX_REDIRECT_URIS = 10
MAX_CLIENT_NAME = 100

AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def looks_like_oauth_access_token(token: str) -> bool:
    return isinstance(token, str) and token.startswith(ACCESS_TOKEN_PREFIX)


def verify_pkce(verifier: str, challenge: str) -> bool:
    """RFC 7636 S256: ``BASE64URL(SHA256(verifier)) == challenge``."""
    if not verifier or not (43 <= len(verifier) <= 128):
        return False
    digest = hashlib.sha256(verifier.encode("ascii", "ignore")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(computed, challenge)


def _is_loopback(host: str | None) -> bool:
    return host in ("localhost", "127.0.0.1", "::1")


def validate_redirect_uri(uri: str) -> str | None:
    """Return an error message for an unacceptable redirect URI, else None.

    HTTPS only, except loopback HTTP for native apps (RFC 8252 §7.3). No
    fragments (OAuth 2.1 §2.3.1).
    """
    if not isinstance(uri, str) or len(uri) > 2000:
        return "redirect_uri must be a string of at most 2000 characters"
    parts = urlsplit(uri)
    if parts.fragment:
        return "redirect_uri must not contain a fragment"
    if parts.scheme == "https" and parts.hostname:
        return None
    if parts.scheme == "http" and _is_loopback(parts.hostname):
        return None
    return "redirect_uri must use https (or http on localhost)"


def redirect_uri_matches(registered: list[str], presented: str) -> bool:
    """Exact match, except that a loopback URI may use any port (RFC 8252 §7.3)."""
    if presented in registered:
        return True
    p = urlsplit(presented)
    if p.scheme != "http" or not _is_loopback(p.hostname):
        return False
    for uri in registered:
        r = urlsplit(uri)
        if (r.scheme, r.hostname, r.path, r.query) == (p.scheme, p.hostname, p.path, p.query):
            return True
    return False


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class OAuthStore:
    """Interface (structural duck-typing, mirrors :class:`ApiKeyStore`)."""

    async def register_client(
        self, client_name: str, redirect_uris: list[str], auth_method: str
    ) -> tuple[dict[str, Any], str | None]:
        raise NotImplementedError

    async def get_client(self, client_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def create_auth_request(self, **fields: Any) -> str:
        raise NotImplementedError

    async def get_auth_request(self, request_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def delete_auth_request(self, request_id: str) -> None:
        raise NotImplementedError

    async def create_grant_and_code(
        self, *, client_id: str, user_id: str, org_id: str, scope: str,
        redirect_uri: str, code_challenge: str,
    ) -> str:
        raise NotImplementedError

    async def redeem_code(self, raw_code: str) -> dict[str, Any] | None:
        """Consume *raw_code*. Return the grant + code fields, or None.

        A code that was already used revokes its grant before returning None.
        """
        raise NotImplementedError

    async def issue_tokens(self, grant_id: str) -> tuple[str, str]:
        raise NotImplementedError

    async def rotate_refresh(self, raw_refresh: str) -> dict[str, Any] | None:
        """Consume *raw_refresh*; return its live grant, or None.

        A rotated (already-used) refresh token revokes its grant.
        """
        raise NotImplementedError

    async def resolve_access(self, raw_access: str) -> dict[str, Any] | None:
        """Return ``{user_id, org_id, grant_id, client_id}`` for a live token."""
        raise NotImplementedError

    async def revoke_token(self, raw_token: str, client_id: str) -> None:
        raise NotImplementedError

    async def list_grants(self, user_id: str, org_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def revoke_grant(self, grant_id: str, user_id: str, org_id: str) -> bool:
        raise NotImplementedError


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _public_grant(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "client_name": row.get("client_name"),
        "redirect_host": urlsplit((row.get("redirect_uris") or [""])[0]).hostname,
        "scope": row.get("scope"),
        "created_at": _iso(row.get("created_at")),
        "last_used_at": _iso(row.get("last_used_at")),
    }


class PgOAuthStore(OAuthStore):
    async def register_client(self, client_name, redirect_uris, auth_method):
        from app.db import fetchrow  # noqa: PLC0415

        client_id = CLIENT_ID_PREFIX + secrets.token_urlsafe(16)
        secret = secrets.token_urlsafe(32) if auth_method != "none" else None
        row = await fetchrow(
            """
            INSERT INTO oauth_clients
                (id, client_name, redirect_uris, token_endpoint_auth_method, client_secret_hash)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, client_name, redirect_uris, token_endpoint_auth_method, created_at
            """,
            client_id, client_name, redirect_uris, auth_method,
            _hash(secret) if secret else None,
        )
        return dict(row), secret

    async def get_client(self, client_id):
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow("SELECT * FROM oauth_clients WHERE id = $1", client_id)
        return dict(row) if row else None

    async def create_auth_request(self, **fields):
        from app.db import execute  # noqa: PLC0415

        request_id = secrets.token_urlsafe(24)
        await execute(
            """
            INSERT INTO oauth_auth_requests
                (id, client_id, redirect_uri, code_challenge, scope, state, resource, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            request_id, fields["client_id"], fields["redirect_uri"], fields["code_challenge"],
            fields["scope"], fields.get("state"), fields.get("resource"),
            _now() + AUTH_REQUEST_TTL,
        )
        # Opportunistic sweep so abandoned consent screens don't accumulate.
        await execute("DELETE FROM oauth_auth_requests WHERE expires_at < now()")
        return request_id

    async def get_auth_request(self, request_id):
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            "SELECT * FROM oauth_auth_requests WHERE id = $1 AND expires_at > now()",
            request_id,
        )
        return dict(row) if row else None

    async def delete_auth_request(self, request_id):
        from app.db import execute  # noqa: PLC0415

        await execute("DELETE FROM oauth_auth_requests WHERE id = $1", request_id)

    async def create_grant_and_code(self, *, client_id, user_id, org_id, scope,
                                    redirect_uri, code_challenge):
        from app.db import execute, fetchrow  # noqa: PLC0415

        grant = await fetchrow(
            """
            INSERT INTO oauth_grants (client_id, user_id, org_id, scope)
            VALUES ($1, $2::uuid, $3::uuid, $4) RETURNING id
            """,
            client_id, user_id, org_id, scope,
        )
        raw = secrets.token_urlsafe(32)
        await execute(
            """
            INSERT INTO oauth_codes (code_hash, grant_id, redirect_uri, code_challenge, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            _hash(raw), grant["id"], redirect_uri, code_challenge, _now() + CODE_TTL,
        )
        return raw

    async def redeem_code(self, raw_code):
        from app.db import execute, fetchrow  # noqa: PLC0415

        code_hash = _hash(raw_code)
        row = await fetchrow(
            """
            UPDATE oauth_codes SET used_at = now()
            WHERE code_hash = $1 AND used_at IS NULL AND expires_at > now()
            RETURNING grant_id, redirect_uri, code_challenge
            """,
            code_hash,
        )
        if row is None:
            replay = await fetchrow(
                "SELECT grant_id FROM oauth_codes WHERE code_hash = $1 AND used_at IS NOT NULL",
                code_hash,
            )
            if replay is not None:
                await execute(
                    "UPDATE oauth_grants SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL",
                    replay["grant_id"],
                )
            return None
        grant = await fetchrow(
            "SELECT * FROM oauth_grants WHERE id = $1 AND revoked_at IS NULL", row["grant_id"]
        )
        if grant is None:
            return None
        return {**dict(grant), "grant_id": str(grant["id"]),
                "redirect_uri": row["redirect_uri"], "code_challenge": row["code_challenge"]}

    async def issue_tokens(self, grant_id):
        from app.db import execute  # noqa: PLC0415

        access = ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)
        refresh = REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = _now()
        await execute(
            """
            INSERT INTO oauth_tokens (token_hash, grant_id, kind, expires_at)
            VALUES ($1, $3::uuid, 'access', $4), ($2, $3::uuid, 'refresh', $5)
            """,
            _hash(access), _hash(refresh), grant_id,
            now + ACCESS_TOKEN_TTL, now + REFRESH_TOKEN_TTL,
        )
        # Expired tokens are dead weight; sweep this grant's on every issue.
        await execute(
            "DELETE FROM oauth_tokens WHERE grant_id = $1::uuid AND expires_at < now()", grant_id
        )
        return access, refresh

    async def rotate_refresh(self, raw_refresh):
        from app.db import execute, fetchrow  # noqa: PLC0415

        token_hash = _hash(raw_refresh)
        row = await fetchrow(
            """
            UPDATE oauth_tokens SET revoked_at = now()
            WHERE token_hash = $1 AND kind = 'refresh'
              AND revoked_at IS NULL AND expires_at > now()
            RETURNING grant_id
            """,
            token_hash,
        )
        if row is None:
            reused = await fetchrow(
                """
                SELECT grant_id FROM oauth_tokens
                WHERE token_hash = $1 AND kind = 'refresh' AND revoked_at IS NOT NULL
                """,
                token_hash,
            )
            if reused is not None:
                await execute(
                    "UPDATE oauth_grants SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL",
                    reused["grant_id"],
                )
            return None
        grant = await fetchrow(
            "SELECT * FROM oauth_grants WHERE id = $1 AND revoked_at IS NULL", row["grant_id"]
        )
        return {**dict(grant), "grant_id": str(grant["id"])} if grant else None

    async def resolve_access(self, raw_access):
        from app.db import execute, fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            SELECT g.id AS grant_id, g.user_id, g.org_id, g.client_id
            FROM oauth_tokens t
            JOIN oauth_grants g ON g.id = t.grant_id AND g.revoked_at IS NULL
            JOIN org_members m ON m.user_id = g.user_id AND m.org_id = g.org_id
            WHERE t.token_hash = $1 AND t.kind = 'access'
              AND t.revoked_at IS NULL AND t.expires_at > now()
            """,
            _hash(raw_access),
        )
        if row is None:
            return None
        try:
            await execute(
                "UPDATE oauth_grants SET last_used_at = now() WHERE id = $1", row["grant_id"]
            )
        except Exception:  # noqa: BLE001 — never block auth on a usage stamp
            pass
        return {k: str(v) for k, v in dict(row).items()}

    async def revoke_token(self, raw_token, client_id):
        from app.db import execute  # noqa: PLC0415

        # RFC 7009: revoking either token of a pair ends the connection, and a
        # client may only revoke its own tokens.
        await execute(
            """
            UPDATE oauth_grants g SET revoked_at = now()
            FROM oauth_tokens t
            WHERE t.token_hash = $1 AND t.grant_id = g.id
              AND g.client_id = $2 AND g.revoked_at IS NULL
            """,
            _hash(raw_token), client_id,
        )

    async def list_grants(self, user_id, org_id):
        from app.db import fetch  # noqa: PLC0415

        rows = await fetch(
            """
            SELECT g.id, g.scope, g.created_at, g.last_used_at,
                   c.client_name, c.redirect_uris
            FROM oauth_grants g JOIN oauth_clients c ON c.id = g.client_id
            WHERE g.user_id = $1::uuid AND g.org_id = $2::uuid AND g.revoked_at IS NULL
            ORDER BY g.created_at DESC
            """,
            user_id, org_id,
        )
        return [_public_grant(dict(r)) for r in rows]

    async def revoke_grant(self, grant_id, user_id, org_id):
        from app.db import execute  # noqa: PLC0415

        status = await execute(
            """
            UPDATE oauth_grants SET revoked_at = now()
            WHERE id = $1::uuid AND user_id = $2::uuid AND org_id = $3::uuid
              AND revoked_at IS NULL
            """,
            grant_id, user_id, org_id,
        )
        try:
            return int(status.split()[-1]) > 0
        except (IndexError, ValueError, AttributeError):
            return False


class InMemoryOAuthStore(OAuthStore):
    """Dict-backed store for tests. ``members`` stands in for ``org_members``."""

    def __init__(self) -> None:
        self.clients: dict[str, dict[str, Any]] = {}
        self.requests: dict[str, dict[str, Any]] = {}
        self.grants: dict[str, dict[str, Any]] = {}
        self.codes: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, dict[str, Any]] = {}
        self.members: set[tuple[str, str]] = set()

    async def register_client(self, client_name, redirect_uris, auth_method):
        client_id = CLIENT_ID_PREFIX + secrets.token_urlsafe(16)
        secret = secrets.token_urlsafe(32) if auth_method != "none" else None
        row = {
            "id": client_id, "client_name": client_name, "redirect_uris": list(redirect_uris),
            "token_endpoint_auth_method": auth_method,
            "client_secret_hash": _hash(secret) if secret else None, "created_at": _now(),
        }
        self.clients[client_id] = row
        return {k: v for k, v in row.items() if k != "client_secret_hash"}, secret

    async def get_client(self, client_id):
        row = self.clients.get(client_id)
        return dict(row) if row else None

    async def create_auth_request(self, **fields):
        request_id = secrets.token_urlsafe(24)
        self.requests[request_id] = {**fields, "id": request_id,
                                     "expires_at": _now() + AUTH_REQUEST_TTL}
        return request_id

    async def get_auth_request(self, request_id):
        row = self.requests.get(request_id)
        return dict(row) if row and row["expires_at"] > _now() else None

    async def delete_auth_request(self, request_id):
        self.requests.pop(request_id, None)

    async def create_grant_and_code(self, *, client_id, user_id, org_id, scope,
                                    redirect_uri, code_challenge):
        grant_id = str(uuid.uuid4())
        self.grants[grant_id] = {
            "id": grant_id, "client_id": client_id, "user_id": str(user_id),
            "org_id": str(org_id), "scope": scope, "created_at": _now(),
            "last_used_at": None, "revoked_at": None,
        }
        raw = secrets.token_urlsafe(32)
        self.codes[_hash(raw)] = {
            "grant_id": grant_id, "redirect_uri": redirect_uri,
            "code_challenge": code_challenge, "expires_at": _now() + CODE_TTL, "used_at": None,
        }
        return raw

    def _live_grant(self, grant_id):
        g = self.grants.get(grant_id)
        return g if g and g["revoked_at"] is None else None

    async def redeem_code(self, raw_code):
        code = self.codes.get(_hash(raw_code))
        if code is None:
            return None
        if code["used_at"] is not None:
            self.grants[code["grant_id"]]["revoked_at"] = _now()
            return None
        if code["expires_at"] <= _now():
            return None
        code["used_at"] = _now()
        grant = self._live_grant(code["grant_id"])
        if grant is None:
            return None
        return {**grant, "grant_id": grant["id"], "redirect_uri": code["redirect_uri"],
                "code_challenge": code["code_challenge"]}

    async def issue_tokens(self, grant_id):
        access = ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)
        refresh = REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = _now()
        self.tokens[_hash(access)] = {"grant_id": grant_id, "kind": "access",
                                      "expires_at": now + ACCESS_TOKEN_TTL, "revoked_at": None}
        self.tokens[_hash(refresh)] = {"grant_id": grant_id, "kind": "refresh",
                                       "expires_at": now + REFRESH_TOKEN_TTL, "revoked_at": None}
        return access, refresh

    async def rotate_refresh(self, raw_refresh):
        tok = self.tokens.get(_hash(raw_refresh))
        if tok is None or tok["kind"] != "refresh":
            return None
        if tok["revoked_at"] is not None:
            self.grants[tok["grant_id"]]["revoked_at"] = _now()
            return None
        if tok["expires_at"] <= _now():
            return None
        tok["revoked_at"] = _now()
        grant = self._live_grant(tok["grant_id"])
        return {**grant, "grant_id": grant["id"]} if grant else None

    async def resolve_access(self, raw_access):
        tok = self.tokens.get(_hash(raw_access))
        if (tok is None or tok["kind"] != "access" or tok["revoked_at"] is not None
                or tok["expires_at"] <= _now()):
            return None
        grant = self._live_grant(tok["grant_id"])
        if grant is None or (grant["user_id"], grant["org_id"]) not in self.members:
            return None
        grant["last_used_at"] = _now()
        return {"grant_id": grant["id"], "user_id": grant["user_id"],
                "org_id": grant["org_id"], "client_id": grant["client_id"]}

    async def revoke_token(self, raw_token, client_id):
        tok = self.tokens.get(_hash(raw_token))
        if tok is None:
            return
        grant = self.grants.get(tok["grant_id"])
        if grant and grant["client_id"] == client_id and grant["revoked_at"] is None:
            grant["revoked_at"] = _now()

    async def list_grants(self, user_id, org_id):
        out = []
        for g in self.grants.values():
            if g["user_id"] == str(user_id) and g["org_id"] == str(org_id) and g["revoked_at"] is None:
                c = self.clients.get(g["client_id"], {})
                out.append(_public_grant({**g, "client_name": c.get("client_name"),
                                          "redirect_uris": c.get("redirect_uris")}))
        return sorted(out, key=lambda r: r["created_at"], reverse=True)

    async def revoke_grant(self, grant_id, user_id, org_id):
        g = self.grants.get(str(grant_id))
        if (g is None or g["revoked_at"] is not None or g["user_id"] != str(user_id)
                or g["org_id"] != str(org_id)):
            return False
        g["revoked_at"] = _now()
        return True


def client_secret_ok(client: dict[str, Any], presented: str | None) -> bool:
    """Constant-time check of a confidential client's secret."""
    stored = client.get("client_secret_hash")
    if not stored:
        return client.get("token_endpoint_auth_method") == "none"
    return bool(presented) and hmac.compare_digest(stored, _hash(presented))


_store: Optional[OAuthStore] = None


def set_oauth_store_for_tests(store: OAuthStore | None) -> None:
    global _store
    _store = store


def get_oauth_store() -> OAuthStore:
    global _store
    if _store is None:
        _store = PgOAuthStore()
    return _store
