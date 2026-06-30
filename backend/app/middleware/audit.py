"""Audit-log backstop middleware — guaranteed mutation coverage.

Every successful mutating request (POST / PUT / PATCH / DELETE) on any
``/api/v1/*`` path that returns 2xx is recorded to the audit log.  This is a
*backstop*: explicit ``record_audit`` call-sites in individual routes continue
to work unchanged and remain the preferred, richer source of audit data.
The middleware catches anything those call-sites miss (new routes, routes that
never got an explicit call, etc.).

Deduplicated via request-state flag
-------------------------------------
When a route already called ``record_audit`` it sets
``request.state.audit_logged = True`` before the response is sent.  The
middleware checks this flag and skips its own write — so NO double-logging
occurs even for routes that have explicit audit calls.

To opt in to the convention from a route handler::

    request.state.audit_logged = True   # suppress middleware backstop

Alternatively, the middleware can be the *sole* audit source and explicit
call-sites removed — but that is a larger refactor.  The flag-check approach
lets the two coexist safely.

Identity extraction
-------------------
The middleware verifies the Bearer token synchronously (same path as the rate
limiter) to derive:

- ``actor_user_id`` — the ``sub`` claim from the verified token.
- ``actor_kind``    — ``"access"`` (first-party) or ``"embed"`` (embed JWT).
- ``org_id``        — the ``org`` claim from the verified token.

If the token is absent or invalid the middleware records with
``actor_user_id=None``, ``actor_kind="system"``, and skips the audit write
entirely (unauthenticated requests → 401 from auth middleware anyway, so
there is nothing useful to log).

POPIA / privacy contract
------------------------
- NO request body is captured.
- NO query parameters beyond the URL path are stored.
- The ``summary`` dict contains only: ``method``, ``path``, ``status``.
- Resource type / ID is inferred from the URL path segments (no PII).

Skipped paths
-------------
- Non-mutating methods: GET, HEAD, OPTIONS.
- Auth/health/embed/static paths (see ``_SKIP_PREFIXES``).
- Non-2xx responses (client errors, server errors, etc.).
- Requests where ``request.state.audit_logged`` is truthy (already logged).

Fail-open
---------
The audit write is ``record_audit`` (which itself is fire-and-forget and never
raises).  Even if ``record_audit`` is unavailable, the middleware catches all
exceptions and re-raises the ORIGINAL response unmodified.  The request is
NEVER broken by a failed audit write.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# HTTP methods that can mutate state — these are audited on 2xx.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Path prefixes that are NEVER audited (liveness probes, static bundles,
# embed static files, auth/health sub-paths, API docs).
_SKIP_PREFIXES = (
    "/health",
    "/api/v1/health",
    "/api/v1/auth/",          # auth (login/register) — dedicated audit paths elsewhere
    "/embed/",
    "/assets/",
    "/docs",
    "/redoc",
    "/openapi",
    "/ops/health",
)

# Only audit paths under our API prefix.
_API_PREFIX = "/api/v1/"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _should_audit(method: str, path: str) -> bool:
    """Return True when this request should be audited by the middleware."""
    if method not in _MUTATING_METHODS:
        return False

    # Must be under the API prefix.
    if not path.startswith(_API_PREFIX):
        return False

    # Skip explicitly listed prefixes.
    for pfx in _SKIP_PREFIXES:
        if path == pfx or path.startswith(pfx):
            return False

    return True


def _extract_resource(path: str) -> tuple[str, str | None]:
    """Derive ``(resource_type, resource_id)`` from a URL path.

    Strips the ``/api/v1/`` prefix, then takes the first segment as the
    resource type and the second (if present) as the resource ID.  No PII
    is captured — only structural path segments.

    Examples
    --------
    ``/api/v1/boards``           → ``("boards", None)``
    ``/api/v1/boards/abc-123``   → ``("boards", "abc-123")``
    ``/api/v1/boards/abc/runs``  → ``("boards", "abc")``
    """
    # Strip /api/v1/ and split on "/"
    relative = path[len(_API_PREFIX):].lstrip("/")
    parts = [p for p in relative.split("/") if p]
    resource_type = parts[0] if parts else "unknown"
    resource_id = parts[1] if len(parts) > 1 else None
    return resource_type, resource_id


def _extract_identity(
    request: Request,
) -> tuple[str | None, str | None, str]:
    """Extract (actor_user_id, org_id, actor_kind) from the Bearer token.

    Returns ``(None, None, "system")`` when no valid token is present.
    Never raises.
    """
    try:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return None, None, "system"

        token = auth_header[7:].strip()
        if not token:
            return None, None, "system"

        from app.auth.verify import verify_token  # noqa: PLC0415

        identity = verify_token(token, expected_origin=None)
        actor_kind = "embed" if identity.kind == "embed" else "access"
        return identity.user_id or None, identity.org or None, actor_kind

    except Exception:  # noqa: BLE001 — never break a request
        return None, None, "system"


# ---------------------------------------------------------------------------
# Middleware class
# ---------------------------------------------------------------------------


class AuditMiddleware(BaseHTTPMiddleware):
    """Record every successful mutating /api/v1/* request to the audit log.

    This is a *backstop*: routes that already call ``record_audit`` explicitly
    set ``request.state.audit_logged = True`` and this middleware skips them.
    All other successful mutations are covered here.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        method = request.method.upper()
        path = request.url.path

        # Fast-path: skip if not a candidate (non-mutating or excluded path).
        if not _should_audit(method, path):
            return await call_next(request)

        # Pre-extract identity before the request runs (headers are available
        # before dispatch; the response status is only available after).
        actor_user_id, org_id, actor_kind = _extract_identity(request)

        # Skip unauthenticated requests — they will hit a 401 anyway, and
        # there is no org or actor to record.
        if actor_user_id is None and actor_kind != "system":
            return await call_next(request)

        # Let the route handler run.
        response: Response = await call_next(request)

        # Only audit successful responses.
        if not (200 <= response.status_code < 300):
            return response

        # Skip if the route already logged (explicit record_audit call-site).
        already_logged: bool = getattr(request.state, "audit_logged", False)
        if already_logged:
            return response

        # Skip if we have no org_id — cannot scope the audit row.
        if not org_id:
            return response

        # Derive resource type and ID from the URL path (no PII).
        resource_type, resource_id = _extract_resource(path)

        # Determine action verb from HTTP method.
        _verb_map: dict[str, str] = {
            "POST": "create",
            "PUT": "update",
            "PATCH": "update",
            "DELETE": "delete",
        }
        verb = _verb_map.get(method, method.lower())
        action = f"{resource_type}.{verb}"

        # Fire-and-forget audit write.  record_audit itself never raises.
        try:
            from app.audit import record_audit  # noqa: PLC0415

            await record_audit(
                org_id=org_id,
                actor_user_id=actor_user_id,
                actor_kind=actor_kind,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                summary={
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                },
            )
        except Exception:  # noqa: BLE001 — must never break the response
            logger.debug("audit middleware: record_audit call failed", exc_info=True)

        return response


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_audit_middleware(app: FastAPI) -> None:
    """Attach :class:`AuditMiddleware` to *app*.

    Call once from ``main.py:create_app()`` — mirrors ``register_ratelimit``
    and ``register_latency``.  The middleware is unconditional; its per-request
    cost is a couple of string comparisons plus an async ``record_audit`` call
    on mutating paths.
    """
    app.add_middleware(AuditMiddleware)
    logger.debug("audit: backstop middleware registered")
