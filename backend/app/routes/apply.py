"""Declarative resource-bundle apply endpoint (files-as-code provisioning).

Endpoint (mounted under ``/api/v1`` via ``api_router``)::

    POST /apply
        body = {
            "org": "<org_id or slug>",          # optional; resolved from token
            "version": "1",                      # bundle format version
            "resources": [<envelope>, …],        # portability envelopes
            "dry_run": false                     # optional, default false
        }
        → {
            "results": [{kind, id, name, action, error?}, …],
            "summary": {created, updated, unchanged, failed},
            "dry_run": bool
          }

Design
------
The ``/apply`` endpoint is the HTTP surface for ``nubi apply``.  It accepts a
flat list of portability envelopes (the same Kubernetes-style shape that
``POST /import`` and ``POST /projects/{id}/import`` use) and upserts each one
idempotently:

- Metrics are handled by the metrics route's ``_persist_metric`` helper
  (upserted by ``(org, config.metric.slug)``).
- Dashboards, queries, flows, and connectors are handled by
  ``portability.upsert_envelope`` (same as ``POST /import``).

Idempotency
-----------
A second ``POST /apply`` with the SAME payload is a no-op: every resource kind
has a stable key (metric slug, dashboard id, connector id) that drives
create-or-update.  When a resource already matches the server state the action
is ``"unchanged"`` and no write is issued.  The caller can detect true no-ops
by checking ``summary.created == 0 and summary.updated == 0``.

Partial failure
---------------
Application is best-effort: a single envelope that fails validation or upsert
is recorded with its ``error`` and the rest still apply.  The response always
has HTTP 200; callers inspect ``summary.failed`` and per-resource ``error``
fields to determine whether any resource needs remediation.

Auth
----
First-party Bearer token required (``current_user``); write scope enforced
(``require_writer``).  The effective org is resolved via ``resolve_org_id``
(honours ``X-Org-Id``); the request body's ``org`` field is informational and
used only when ``X-Org-Id`` is absent and the token cannot resolve an org
uniquely (same semantics as the portability routes).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id
from app.routes.portability import upsert_envelope

logger = logging.getLogger(__name__)

router = APIRouter(tags=["provisioning"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ApplyRequest(BaseModel):
    """Body for ``POST /apply``."""

    #: Portability envelopes (Kubernetes-style) to apply.
    resources: list[dict[str, Any]]

    #: Bundle format version (informational; currently always "1").
    version: str = "1"

    #: Optional org identifier hint (slug or UUID).  Informational when the token
    #: already resolves a unique org; acts as the ``X-Org-Id`` substitute when
    #: no header is present.
    org: str | None = None

    #: When True the bundle is validated but no writes are issued.  The response
    #: shows what WOULD change, identical to ``nubi plan``.
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resource_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregate result actions into a summary dict."""
    summary: dict[str, int] = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    for r in results:
        action = r.get("action", "failed")
        if action in summary:
            summary[action] += 1
        else:
            summary["failed"] += 1
    return summary


async def _apply_envelope(
    env: dict[str, Any],
    *,
    org_id: str,
    user: dict[str, Any],
    repo: Repo,
    project_id: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Apply a single envelope; return a result dict with action / error."""
    kind = env.get("kind", "")
    meta = env.get("metadata") or {}
    name = meta.get("name")
    env_id = meta.get("id")

    if dry_run:
        # Validate only — never write.
        from app.portability import get_handler, validate_spec_for_kind  # noqa: PLC0415

        try:
            get_handler(kind)
        except AppError as exc:
            return {
                "kind": kind,
                "id": str(env_id) if env_id else None,
                "name": name,
                "action": "failed",
                "error": exc.message,
            }
        spec = env.get("spec") or {}
        issues = validate_spec_for_kind(kind, spec)
        if issues:
            return {
                "kind": kind,
                "id": str(env_id) if env_id else None,
                "name": name,
                "action": "failed",
                "error": "Spec validation failed: " + "; ".join(issues),
            }
        # No id → would create; id present → check server state.
        if not env_id:
            return {
                "kind": kind,
                "id": None,
                "name": name,
                "action": "create",
            }
        # Try to load existing row to determine create vs update.
        from app.portability import get_handler  # noqa: PLC0415

        handler = get_handler(kind)
        existing = None
        try:
            if kind == "flow":
                from app.flows.store import get_flow_store  # noqa: PLC0415

                existing = await get_flow_store().get_flow(str(env_id))
                if existing and str(existing.get("org_id")) != str(org_id):
                    existing = None
            else:
                existing = await repo.get(handler.resource, org_id, str(env_id))
        except Exception:  # noqa: BLE001
            pass
        action = "update" if existing else "create"
        return {
            "kind": kind,
            "id": str(env_id) if env_id else None,
            "name": name,
            "action": action,
        }

    # Live apply.
    try:
        row, action = await upsert_envelope(
            env,
            org_id=org_id,
            user=user,
            repo=repo,
            project_id=project_id,
        )
        return {
            "kind": kind,
            "id": str(row.get("id")) if row.get("id") is not None else None,
            "name": row.get("name", name),
            "action": action,
        }
    except AppError as exc:
        logger.warning(
            "apply: envelope kind=%r name=%r failed: %s", kind, name, exc.message
        )
        return {
            "kind": kind,
            "id": str(env_id) if env_id else None,
            "name": name,
            "action": "failed",
            "error": exc.message,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("apply: unexpected error for kind=%r name=%r", kind, name)
        return {
            "kind": kind,
            "id": str(env_id) if env_id else None,
            "name": name,
            "action": "failed",
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# POST /apply
# ---------------------------------------------------------------------------


@router.post("/apply", status_code=200, dependencies=[Depends(require_writer)])
async def apply_bundle(
    body: ApplyRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Apply a declarative resource bundle idempotently.

    Each envelope in ``resources`` is upserted (create-or-update) using the
    same per-kind logic as ``POST /import``.  The operation is best-effort:
    failures are recorded per-resource and never abort remaining envelopes.

    When ``dry_run=true`` the request is validated but no writes are made;
    the ``action`` fields show what WOULD happen (``"create"`` / ``"update"``).

    Returns
    -------
    dict
        ``{results, summary, dry_run}``.  HTTP 200 always; inspect
        ``summary.failed`` to detect partial failures.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)

    results: list[dict[str, Any]] = []
    for env in body.resources:
        result = await _apply_envelope(
            env,
            org_id=org_id,
            user=user,
            repo=repo,
            project_id=None,
            dry_run=body.dry_run,
        )
        results.append(result)

    summary = _resource_summary(results)

    return {
        "results": results,
        "summary": summary,
        "dry_run": body.dry_run,
    }


# ---------------------------------------------------------------------------
# Self-register on the shared api_router (before the /{resource} catch-all)
# ---------------------------------------------------------------------------

_before = len(api_router.routes)
api_router.include_router(router)
# Move newly added routes to the front so /apply wins over the catch-all.
_new_routes = api_router.routes[_before:]
_old_routes = api_router.routes[:_before]
api_router.routes[:] = _new_routes + _old_routes
