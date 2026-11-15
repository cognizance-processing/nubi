"""Shared helpers for the security test suite.

Provides RSA keypair, token minting helpers, and app fixture wiring.
Each test module imports from here rather than duplicating setup.

NOT a conftest.py (to avoid pytest auto-collection conflicts with the
parent conftest); import explicitly from each security test file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import Request


# ---------------------------------------------------------------------------
# Environment bootstrap (must be done before any app imports).
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback"
)
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

# ---------------------------------------------------------------------------
# RSA keypair (generated once per process).
# ---------------------------------------------------------------------------
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
import jwt as pyjwt

_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_PUBLIC_KEY_PEM: str = _PUBLIC_KEY.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

# A *second* keypair for "wrong key" / forgery tests.
_ATTACKER_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)

_JWKS_KEY: dict = json.loads(RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS_KEY["kid"] = "sec-suite-key"
_JWKS_KEY["use"] = "sig"
STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

HOST_ISS = "https://sec-suite-host.example"
HOST_AUD = "nubi"
EMBED_ORIGIN = "https://sec-suite-host.example"
KID = "sec-suite-key"


# ---------------------------------------------------------------------------
# Token minting helpers
# ---------------------------------------------------------------------------

def mint_embed_token(
    *,
    iss: str = HOST_ISS,
    aud: str = HOST_AUD,
    sub: str = "sec-user-1",
    org: str = "sec-org",
    scope: list[str] | None = None,
    policies: dict | None = None,
    embed_origin: str | None = EMBED_ORIGIN,
    exp_delta: int = 300,
    private_key=None,
    algorithm: str = "RS256",
    kid: str | None = KID,
) -> str:
    """Mint a test embed JWT."""
    if scope is None:
        scope = ["read:query"]
    if policies is None:
        policies = {}
    if private_key is None:
        private_key = _PRIVATE_KEY

    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "org": org,
        "roles": ["viewer"],
        "policies": policies,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    if embed_origin is not None:
        payload["embed_origin"] = embed_origin

    headers: dict[str, Any] = {}
    if kid is not None:
        headers["kid"] = kid

    return pyjwt.encode(payload, private_key, algorithm=algorithm, headers=headers)


def mint_access_token(user_id: str = "sec-fp-user", extra_claims: dict | None = None) -> str:
    """Mint a first-party HS256 access token via the real helper."""
    from app.auth.jwt import mint_access_token as _mint
    return _mint(user_id, extra_claims=extra_claims)


# ---------------------------------------------------------------------------
# App + client fixture factories (to be used in each test module's fixtures)
# ---------------------------------------------------------------------------

def make_db_patches():
    """Return a list of mock patches for DB I/O (mirrors conftest.py)."""
    return [
        patch("app.db.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.db.fetch", new=AsyncMock(return_value=[])),
        patch("app.db.execute", new=AsyncMock(return_value="OK")),
        patch("app.db.get_connection", new=AsyncMock()),
        patch("app.routes.auth.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.routes.auth.execute", new=AsyncMock(return_value="OK")),
        patch("app.auth.sessions.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.auth.sessions.execute", new=AsyncMock(return_value="OK")),
        patch("app.auth.deps.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
    ]


# ---------------------------------------------------------------------------
# Multi-tenant route harness
# ---------------------------------------------------------------------------

def make_tenant_app(members: dict[str, tuple[str, str]], *, modules: tuple[str, ...] = ()):
    """Build an app over the in-memory repo with several users in several orgs.

    ``members`` maps a short user key to ``(org_id, role)``.  Requests pick the
    acting user with the ``X-Test-User`` header (the short key).  Returns
    ``(app, repo, users)`` where ``users[key]`` is the generated user id.
    Callers must ``set_repo(None)`` when done.
    """
    import importlib
    import uuid

    from fastapi import FastAPI

    from app.auth.deps import current_user
    from app.errors import AppError, register_handlers
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    for mod in modules:
        importlib.import_module(mod)
    from app.routes import api_router

    repo = InMemoryRepo()
    users: dict[str, str] = {}
    for key, (org_id, role) in members.items():
        users[key] = str(uuid.uuid4())
        repo.seed_org_member(org_id, users[key], role)
    set_repo(repo)

    def _user(request: Request) -> dict[str, Any]:
        key = request.headers.get("x-test-user", "")
        if key not in users:
            raise AppError("unauthorized", "Authentication required.", 401)
        return {"id": users[key], "email": f"{key}@example.com", "name": key}

    app = FastAPI()
    register_handlers(app)
    app.dependency_overrides[current_user] = _user
    app.include_router(api_router, prefix="/api/v1")
    return app, repo, users


def as_user(key: str) -> dict[str, str]:
    """Headers selecting the acting user for :func:`make_tenant_app`."""
    return {"X-Test-User": key}


# ---------------------------------------------------------------------------
# Route table
# ---------------------------------------------------------------------------

def iter_api_routes(app: Any):
    """Yield ``(path, methods, dependant)`` for every API route of *app*.

    Works whether the framework keeps included routers flattened into
    ``app.routes`` or as lazily-resolved wrappers.
    """
    from fastapi.routing import APIRoute

    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route.path, set(route.methods or ()), route.dependant
        elif hasattr(route, "effective_route_contexts"):
            for ctx in route.effective_route_contexts():
                dependant = getattr(ctx, "dependant", None)
                if dependant is not None:
                    yield ctx.path, set(getattr(ctx, "methods", None) or ()), dependant


def route_dependency_calls(dependant: Any) -> set:
    """Every dependency callable reachable from *dependant*."""
    seen: set = set()

    def walk(dep: Any) -> None:
        for sub in dep.dependencies:
            if sub.call not in seen:
                seen.add(sub.call)
                walk(sub)

    walk(dependant)
    return seen


def route_guards(app: Any, path: str, method: str) -> set:
    """Dependency callables of the route serving *method* *path* (KeyError if absent)."""
    for route_path, methods, dependant in iter_api_routes(app):
        if route_path == path and method in methods:
            return route_dependency_calls(dependant)
    raise KeyError(f"{method} {path}")
