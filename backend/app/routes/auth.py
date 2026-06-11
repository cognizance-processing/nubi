"""Auth routes — all endpoints under /auth.

Endpoints
---------
POST   /auth/register          Register a new user (email+password).
POST   /auth/login             Authenticate with email+password.
POST   /auth/refresh           Rotate the refresh cookie → new access token.
POST   /auth/logout            Revoke session family, clear cookie.
GET    /auth/me                Return the current user (Bearer required).
GET    /auth/me/invites        Pending org invites for the current user's email.
GET    /auth/google/start      Initiate Google OAuth (redirect).
GET    /auth/google/callback   Handle Google OAuth callback (redirect).

This module attaches itself to the shared ``api_router`` at import time so
that ``main.py``'s ``include_router(api_router, prefix="/api/v1")`` picks it
up automatically.
"""

from __future__ import annotations

import hmac
import uuid
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, field_validator

from app.auth.api_keys import get_api_key_store
from app.auth.cookies import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.auth.denylist import get_token_denylist
from app.auth.deps import current_user
from app.auth.google import (
    build_authorize_url,
    exchange_code,
    generate_pkce_pair,
    generate_state,
)
from app.auth.jwt import decode_access_token, mint_access_token
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import issue_refresh, revoke_by_token, rotate_refresh
from app.config import get_settings
from app.db import execute, fetch, fetchrow
from app.errors import AppError
from app.repos import projects as projects_repo
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import get_user_org

# A pre-computed argon2id hash of an arbitrary dummy string.  Used so that
# the login path always performs an argon2 verification regardless of whether
# the email exists or has a password — preventing timing-based user enumeration.
_DUMMY_HASH: str = hash_password("nubi-dummy-constant-timing-sentinel")

# ── Sub-router ────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/auth", tags=["auth"])

# ── Cookie names for PKCE state ───────────────────────────────────────────────
_OAUTH_STATE_COOKIE = "nubi_oauth_state"
_OAUTH_VERIFIER_COOKIE = "nubi_oauth_verifier"
_OAUTH_COOKIE_PATH = "/api/v1/auth/google"
_OAUTH_COOKIE_MAX_AGE = 600  # 10 minutes; enough time to complete the flow


# ── Pydantic request schemas ──────────────────────────────────────────────────

class RegisterIn(BaseModel):
    """Request body for POST /auth/register."""

    email: EmailStr
    password: str
    name: str | None = None
    # Supabase-style optional naming at signup. When omitted we fall back to the
    # default org naming and a "Default" project, respectively.
    org_name: str | None = None
    project_name: str | None = None
    # When true, seed the removable demo bundle INTO the org's single default
    # project (no separate "Demo" project). Best-effort.
    demo_project: bool = False

    @field_validator("password")
    @classmethod
    def _password_min_length(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return value


class LoginIn(BaseModel):
    """Request body for POST /auth/login."""

    email: EmailStr
    password: str


class ApiKeyCreateIn(BaseModel):
    """Request body for POST /auth/api-keys."""

    name: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize_user(row: Any) -> dict[str, Any]:
    """Convert a DB row (or dict) into the API user shape.

    ``created_at`` is serialized to ISO 8601 string so it survives JSON
    encoding regardless of the asyncpg datetime type.
    """
    created_at = row["created_at"]
    if hasattr(created_at, "isoformat"):
        created_at = created_at.isoformat()

    return {
        "id": str(row["id"]),
        "email": str(row["email"]),
        "name": row["name"],
        "avatar_url": row["avatar_url"],
        "email_verified": bool(row["email_verified"]),
        "created_at": created_at,
    }


async def _create_personal_org(
    user_id: str,
    name: str | None,
    email: str,
    org_name: str | None = None,
    project_name: str | None = None,
) -> str:
    """Create a personal org, owner membership, and a default project.

    The org slug is clean-first and immutable: derived from the chosen org
    name (or the email local-part), suffixed ONLY on collision — see
    ``app.onboarding.insert_org_with_unique_slug``.

    A "Default" project (or *project_name* when supplied) is created for the
    new org so resource creation is frictionless — every org owns at least one
    project from the moment it exists.

    Parameters
    ----------
    org_name:
        Optional explicit org name (Supabase-style signup). Falls back to the
        existing ``"<name>'s workspace"`` convention.
    project_name:
        Optional first-project name. Falls back to ``"Default"``.

    Returns
    -------
    str
        The new org's id.
    """
    org_id = str(uuid.uuid4())
    final_org_name = (org_name or "").strip() or f"{name or email.split('@')[0]}'s workspace"

    # Clean-first immutable slug: prefer the org name the user chose, else the
    # email local-part; a suffix is appended only on collision.
    from app.onboarding import insert_org_with_unique_slug  # noqa: PLC0415

    slug_base = (org_name or "").strip() or email.split("@")[0]
    await insert_org_with_unique_slug(org_id, final_org_name, slug_base)
    await execute(
        """
        INSERT INTO org_members (org_id, user_id, role)
        VALUES ($1, $2, 'owner')
        """,
        org_id,
        user_id,
    )

    # Default project — keeps the org → project → resources model frictionless.
    # The project starts EMPTY; demo content is seeded INTO this same project
    # only when the caller opts in (the register `demo_project` flag below).
    await projects_repo.create_project(
        org_id=org_id,
        name=(project_name or "").strip() or "Default",
        created_by=user_id,
    )

    return org_id


async def get_default_project(org_id: str) -> dict[str, Any] | None:
    """Return the org's default (oldest) project, or ``None``.

    Convenience re-export of ``app.repos.projects.get_default_project`` so other
    modules can import it from the auth layer alongside ``_create_personal_org``.
    """
    return await projects_repo.get_default_project(org_id)


def _client_ip(request: Request) -> str | None:
    """Extract the client IP, respecting X-Forwarded-For if set."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
async def register(
    body: RegisterIn,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Register a new user with email and password.

    - Hashes the password with argon2id.
    - Creates a personal org + owner membership.
    - Issues an access JWT and sets a refresh cookie.

    Returns
    -------
    201 {user, access_token}
    """
    email = str(body.email).lower()

    # Check for existing account (same generic error to prevent user enumeration).
    existing = await fetchrow("SELECT id FROM users WHERE email = $1", email)
    if existing is not None:
        raise AppError("email_taken", "An account with that email already exists.", 409)

    user_id = str(uuid.uuid4())
    pw_hash = hash_password(body.password)

    await execute(
        """
        INSERT INTO users (id, email, password_hash, name, email_verified)
        VALUES ($1, $2, $3, $4, false)
        """,
        user_id,
        email,
        pw_hash,
        body.name,
    )

    # Create personal org + membership + default project (Supabase-style names).
    org_id = await _create_personal_org(
        user_id,
        body.name,
        email,
        org_name=body.org_name,
        project_name=body.project_name,
    )

    # Optionally seed the removable demo bundle INTO the org's single default
    # project (no separate "Demo" project). Best-effort — demo content must
    # never break signup.
    if body.demo_project:
        try:
            from app.sample import (  # noqa: PLC0415
                checkpoint_and_promote_bundle,
                seed_sample_bundle,
            )

            default_pid = await projects_repo.get_default_project_id(org_id)
            if default_pid is not None:
                seed = await seed_sample_bundle(org_id, default_pid, user_id)
                if "skipped" not in seed:
                    await checkpoint_and_promote_bundle(org_id, default_pid, user_id)
        except Exception:  # noqa: BLE001
            pass

    # Fetch the full user row to build the response.
    user_row = await fetchrow(
        "SELECT id, email, name, avatar_url, email_verified, created_at FROM users WHERE id = $1::uuid",
        user_id,
    )
    if user_row is None:
        raise AppError("internal_error", "User creation failed.", 500)

    access_token = mint_access_token(user_id)
    raw_refresh, expires_at = await issue_refresh(
        user_id,
        user_agent=request.headers.get("user-agent"),
        ip=_client_ip(request),
    )
    set_refresh_cookie(response, raw_refresh, expires_at)

    # Login analytics — best-effort, never blocks registration.
    from app.login_events import record_login_event  # noqa: PLC0415

    await record_login_event(user_id, request)

    return {"user": _serialize_user(user_row), "access_token": access_token}


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Authenticate with email and password.

    Uses the same generic error for both unknown-user and wrong-password to
    prevent user enumeration.

    Returns
    -------
    200 {user, access_token}
    """
    email = str(body.email).lower()

    user_row = await fetchrow(
        "SELECT id, email, password_hash, name, avatar_url, email_verified, created_at FROM users WHERE email = $1",
        email,
    )

    # Always call verify_password regardless of whether the user exists or has a
    # password hash.  This ensures a constant-time response that prevents:
    #   - User-existence enumeration (unknown email vs wrong password timing).
    #   - Account-type enumeration (OAuth-only accounts have no password_hash
    #     and previously short-circuited the argon2 work, leaking the difference).
    # _DUMMY_HASH is a valid argon2id hash — verify_password always does full work.
    stored_hash: str = (
        user_row["password_hash"]
        if (user_row is not None and user_row["password_hash"] is not None)
        else _DUMMY_HASH
    )
    password_ok = verify_password(stored_hash, body.password)

    if user_row is None or user_row["password_hash"] is None or not password_ok:
        raise AppError("invalid_credentials", "Invalid email or password.", 401)

    user_id = str(user_row["id"])
    access_token = mint_access_token(user_id)
    raw_refresh, expires_at = await issue_refresh(
        user_id,
        user_agent=request.headers.get("user-agent"),
        ip=_client_ip(request),
    )
    set_refresh_cookie(response, raw_refresh, expires_at)

    # Login analytics — best-effort, never blocks login.
    from app.login_events import record_login_event  # noqa: PLC0415

    await record_login_event(user_id, request)

    return {"user": _serialize_user(user_row), "access_token": access_token}


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    nubi_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> dict[str, Any]:
    """Rotate the refresh cookie and return a new access token.

    On reuse of a consumed token the entire session family is revoked and
    the cookie is cleared.

    Returns
    -------
    200 {access_token}
    """
    if not nubi_refresh:
        raise AppError("unauthorized", "No refresh token.", 401)

    try:
        new_raw, user_id, new_expires = await rotate_refresh(
            nubi_refresh,
            user_agent=request.headers.get("user-agent"),
            ip=_client_ip(request),
        )
    except AppError:
        # On reuse or invalid token, clear the cookie before re-raising.
        clear_refresh_cookie(response)
        raise

    set_refresh_cookie(response, new_raw, new_expires)
    access_token = mint_access_token(user_id)

    return {"access_token": access_token}


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    nubi_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> None:
    """Revoke the session family, denylist the access token, and clear the cookie.

    In addition to revoking the refresh-token family (existing behaviour),
    we now extract the caller's access-token ``jti`` from the ``Authorization``
    header (if present) and add it to the denylist so it is rejected immediately
    on every subsequent authenticated request — closing the stateless-JWT gap.

    Returns 204 No Content regardless of whether a cookie or token was present.
    """
    # ── Revoke refresh-token family (existing behaviour) ─────────────────────
    if nubi_refresh:
        await revoke_by_token(nubi_refresh)
    clear_refresh_cookie(response)

    # ── Denylist the access token so it cannot be reused post-logout ──────────
    auth_header: str | None = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        raw_token = auth_header[7:].strip()
        try:
            claims = decode_access_token(raw_token)
            jti: str = claims["jti"]
            from datetime import datetime, timezone
            # exp from PyJWT is an integer (epoch seconds) or datetime; normalise.
            raw_exp = claims["exp"]
            if isinstance(raw_exp, datetime):
                exp_dt = raw_exp if raw_exp.tzinfo else raw_exp.replace(tzinfo=timezone.utc)
            else:
                exp_dt = datetime.fromtimestamp(int(raw_exp), tz=timezone.utc)
            await get_token_denylist().revoke(jti, exp_dt)
        except Exception:
            # Never fail a logout because of denylist issues; the refresh
            # revocation already limits damage.
            pass


@router.get("/me")
async def me(
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the currently authenticated user.

    ``is_superadmin`` is read fresh from the user's DB row (never from JWT
    claims) — it is informational for the frontend only; the /admin/* routes
    re-check it server-side on every request.

    Returns
    -------
    200 {user}
    """
    payload = _serialize_user(user)

    is_superadmin = False
    try:
        row = await fetchrow(
            "SELECT is_superadmin FROM users WHERE id = $1::uuid",
            str(user["id"]),
        )
        if row is not None:
            is_superadmin = bool(dict(row).get("is_superadmin"))
    except Exception:  # noqa: BLE001 — pre-migration DBs simply report False
        pass
    payload["is_superadmin"] = is_superadmin

    return {"user": payload}


@router.get("/me/invites")
async def my_invites(
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return pending, non-expired org invites addressed to the caller's email.

    Email matching is case-insensitive. No org membership is required — this
    is exactly what an org-less (freshly OAuth-signed-up) user calls during
    onboarding to discover invitations.

    Returns
    -------
    200 {invites: [{id, org_id, org_name, role, token, created_at, expires_at}]}
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    email = str(user["email"]).strip().lower()
    rows = await fetch(
        """
        SELECT i.id, i.org_id, i.role, i.token, i.created_at, i.expires_at,
               o.name AS org_name
        FROM org_invites i
        JOIN orgs o ON o.id = i.org_id
        WHERE lower(i.email) = $1
          AND i.status = 'pending'
          AND i.expires_at > now()
        ORDER BY i.created_at DESC
        """,
        email,
    )

    now = datetime.now(tz=timezone.utc)
    invites: list[dict[str, Any]] = []
    for r in rows:
        # Defensive expiry filter (test doubles may not evaluate the SQL).
        expires_at = r["expires_at"]
        expires_dt = (
            datetime.fromisoformat(str(expires_at))
            if not isinstance(expires_at, datetime)
            else expires_at
        )
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        if expires_dt <= now:
            continue

        created_at = r["created_at"]
        invites.append(
            {
                "id": str(r["id"]),
                "org_id": str(r["org_id"]),
                "org_name": r["org_name"],
                "role": r["role"],
                "token": r["token"],
                "created_at": (
                    created_at.isoformat()
                    if hasattr(created_at, "isoformat")
                    else str(created_at)
                ),
                "expires_at": expires_dt.isoformat(),
            }
        )
    return {"invites": invites}


# ── API keys (long-lived CLI / CI tokens — files-as-code F-6) ────────────────
#
# An API key is an opaque, non-expiring bearer credential a user mints for the
# CLI or CI. It authenticates Bearer requests exactly like a login access token
# (resolved in app/auth/deps.py::current_user) but is scoped to the org it was
# minted for and is independently listable + revocable. Only the SHA-256 digest
# is stored; the raw key is returned EXACTLY ONCE at mint time.


@router.post("/api-keys", status_code=201)
async def create_api_key(
    body: ApiKeyCreateIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Mint a long-lived API key scoped to the caller's (default) org.

    The raw key is returned ONCE in ``key`` — it is never retrievable again.
    Store it as ``NUBI_TOKEN`` for the CLI / CI.

    Returns
    -------
    201 {key, api_key: {id, name, last_four, created_at, ...}}
    """
    user_id = str(user["id"])
    org_id = await get_user_org(user_id, repo)

    raw, row = await get_api_key_store().create(
        user_id, org_id, (body.name or "CLI token")
    )
    from app.auth.api_keys import _public_row  # noqa: PLC0415

    return {"key": raw, "api_key": _public_row(row)}


@router.get("/api-keys")
async def list_api_keys(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List the caller's API keys in their (default) org — never any secret.

    Returns
    -------
    200 {api_keys: [{id, name, last_four, created_at, last_used_at, revoked_at}]}
    """
    user_id = str(user["id"])
    org_id = await get_user_org(user_id, repo)
    keys = await get_api_key_store().list_for_org(user_id, org_id)
    return {"api_keys": keys}


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    """Revoke one of the caller's API keys (idempotent-ish: 404 if not found).

    Cross-user / cross-org keys are invisible (404, never 403) so no key's
    existence leaks across tenants.
    """
    user_id = str(user["id"])
    org_id = await get_user_org(user_id, repo)
    revoked = await get_api_key_store().revoke(key_id, user_id, org_id)
    if not revoked:
        raise AppError("not_found", "API key not found.", 404)


@router.get("/google/start")
async def google_start(response: Response) -> RedirectResponse:
    """Initiate the Google OAuth flow.

    - Generates a PKCE code_verifier + S256 code_challenge.
    - Generates a random state value.
    - Stores both in short-lived HttpOnly cookies (10 min).
    - Redirects the browser to Google's authorization endpoint.

    Returns
    -------
    302 → Google authorization URL
    """
    settings = get_settings()
    state = generate_state()
    code_verifier, code_challenge = generate_pkce_pair()
    authorize_url = build_authorize_url(state, code_challenge)

    redirect = RedirectResponse(url=authorize_url, status_code=302)

    # Store state and verifier in HttpOnly cookies so the callback can verify them.
    _set_oauth_cookie(redirect, _OAUTH_STATE_COOKIE, state, settings.COOKIE_SECURE)
    _set_oauth_cookie(redirect, _OAUTH_VERIFIER_COOKIE, code_verifier, settings.COOKIE_SECURE)

    return redirect


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    nubi_oauth_state: str | None = Cookie(default=None, alias=_OAUTH_STATE_COOKIE),
    nubi_oauth_verifier: str | None = Cookie(default=None, alias=_OAUTH_VERIFIER_COOKIE),
) -> RedirectResponse:
    """Handle the Google OAuth callback.

    - Verifies state cookie (CSRF protection).
    - Exchanges the authorization code + PKCE verifier for user info.
    - Finds the user by email or creates a new one (OAuth-only account).
    - Upserts the ``oauth_accounts`` row.
    - Issues a refresh cookie and redirects to the frontend.

    The SPA receives the refresh cookie and immediately calls ``POST /refresh``
    to obtain an access token (the token-in-memory pattern).

    Returns
    -------
    302 → settings.FRONTEND_URL
    """
    settings = get_settings()

    # ── Error from Google (user denied, etc.) ─────────────────────────────────
    if error:
        return _oauth_error_redirect(settings.FRONTEND_URL, "oauth_denied")

    # ── Validate required parameters ──────────────────────────────────────────
    if not code or not state:
        return _oauth_error_redirect(settings.FRONTEND_URL, "oauth_failed")

    # ── CSRF state verification ───────────────────────────────────────────────
    if not nubi_oauth_state or not nubi_oauth_verifier:
        return _oauth_error_redirect(settings.FRONTEND_URL, "oauth_state_missing")

    # Use constant-time comparison to prevent a timing oracle on the state value.
    if not hmac.compare_digest(state, nubi_oauth_state):
        return _oauth_error_redirect(settings.FRONTEND_URL, "oauth_state_mismatch")

    # ── Exchange code for profile ─────────────────────────────────────────────
    try:
        profile = await exchange_code(code, nubi_oauth_verifier)
    except AppError:
        return _oauth_error_redirect(settings.FRONTEND_URL, "oauth_failed")

    email: str = profile["email"].lower()
    provider_account_id: str = profile["provider_account_id"]
    email_verified: bool = profile["email_verified"]
    name: str | None = profile["name"]
    picture: str | None = profile["picture"]

    # ── Enforce email_verified before any account access or linking ───────────
    # If Google reports the email is not verified, we must not link this OAuth
    # identity to an existing account — doing so would let an attacker who
    # controls an unverified Google account for victim@example.com log in as
    # the victim's Nubi account.  New-user creation is also blocked: we only
    # accept Google identities with a verified email address.
    if not email_verified:
        return _oauth_error_redirect(settings.FRONTEND_URL, "oauth_email_unverified")

    # ── Find or create user by email ──────────────────────────────────────────
    user_row = await fetchrow(
        "SELECT id, email, name, avatar_url, email_verified, created_at FROM users WHERE email = $1",
        email,
    )

    if user_row is None:
        # New user — create account (password_hash = NULL for OAuth-only).
        user_id = str(uuid.uuid4())
        await execute(
            """
            INSERT INTO users (id, email, password_hash, name, avatar_url, email_verified)
            VALUES ($1, $2, NULL, $3, $4, $5)
            """,
            user_id,
            email,
            name,
            picture,
            email_verified,
        )
        # NOTE: no org/project is auto-created for OAuth signups. The frontend
        # onboarding flow walks the new user through creating their first org
        # (POST /orgs) and project (POST /projects) — Supabase-style.

        # Best-effort: ingest the Google avatar into our own storage so the
        # avatar is served from our domain.  Never fails the login flow.
        if picture:
            try:
                from app.assets import ingest_avatar_from_url  # noqa: PLC0415

                served_url = await ingest_avatar_from_url(picture, "user", user_id)
                if served_url and served_url != picture:
                    await execute(
                        "UPDATE users SET avatar_url = $1, updated_at = now() WHERE id = $2::uuid",
                        served_url,
                        user_id,
                    )
            except Exception:  # noqa: BLE001 — best-effort, never break login
                pass

        user_row = await fetchrow(
            "SELECT id, email, name, avatar_url, email_verified, created_at FROM users WHERE id = $1::uuid",
            user_id,
        )
        if user_row is None:
            return _oauth_error_redirect(settings.FRONTEND_URL, "oauth_failed")
    else:
        user_id = str(user_row["id"])
        # Update avatar/name if Google provides newer info and user has none.
        if picture and not user_row["avatar_url"]:
            # Best-effort: ingest the Google avatar into our own storage.
            served_url: str | None = None
            try:
                from app.assets import ingest_avatar_from_url  # noqa: PLC0415

                served_url = await ingest_avatar_from_url(picture, "user", user_id)
            except Exception:  # noqa: BLE001 — best-effort, never break login
                pass
            await execute(
                "UPDATE users SET avatar_url = $1, updated_at = now() WHERE id = $2::uuid",
                served_url or picture,
                user_id,
            )

    # ── Upsert oauth_accounts ─────────────────────────────────────────────────
    oauth_id = str(uuid.uuid4())
    await execute(
        """
        INSERT INTO oauth_accounts (id, user_id, provider, provider_account_id)
        VALUES ($1, $2, 'google', $3)
        ON CONFLICT (provider, provider_account_id)
        DO UPDATE SET user_id = EXCLUDED.user_id
        """,
        oauth_id,
        user_id,
        provider_account_id,
    )

    # ── Issue refresh cookie ──────────────────────────────────────────────────
    raw_refresh, expires_at = await issue_refresh(
        user_id,
        user_agent=request.headers.get("user-agent"),
        ip=_client_ip(request),
    )

    redirect = RedirectResponse(url=settings.FRONTEND_URL, status_code=302)
    set_refresh_cookie(redirect, raw_refresh, expires_at)

    # Clear the ephemeral PKCE/state cookies.
    settings_obj = get_settings()
    _clear_oauth_cookie(redirect, _OAUTH_STATE_COOKIE, settings_obj.COOKIE_SECURE)
    _clear_oauth_cookie(redirect, _OAUTH_VERIFIER_COOKIE, settings_obj.COOKIE_SECURE)

    return redirect


# ── Private OAuth cookie helpers ──────────────────────────────────────────────

def _set_oauth_cookie(
    response: Response,
    name: str,
    value: str,
    secure: bool,
) -> None:
    """Set a short-lived HttpOnly cookie for PKCE/state storage."""
    response.set_cookie(
        key=name,
        value=value,
        max_age=_OAUTH_COOKIE_MAX_AGE,
        path=_OAUTH_COOKIE_PATH,
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def _clear_oauth_cookie(response: Response, name: str, secure: bool) -> None:
    """Expire an OAuth PKCE/state cookie."""
    response.set_cookie(
        key=name,
        value="",
        max_age=0,
        path=_OAUTH_COOKIE_PATH,
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def _oauth_error_redirect(frontend_url: str, error_code: str) -> RedirectResponse:
    """Redirect to the frontend with an error query parameter."""
    return RedirectResponse(
        url=f"{frontend_url.rstrip('/')}?auth_error={error_code}",
        status_code=302,
    )


# ── Attach to the shared api_router ──────────────────────────────────────────
# This runs at import time.  ``main.py`` imports ``api_router`` after this
# module is loaded (via ``app.routes.auth``), so the routes are present when
# the application starts.
api_router.include_router(router)
