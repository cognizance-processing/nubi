"""Schema-drift read API.

Routes (all under ``/api/v1/health``)
--------------------------------------
GET  /health/drift
    List recent schema-drift events for the caller's org.
    Optional ``?dataset_key=`` filter to narrow to one dataset.

GET  /health/drift/{dataset_key}
    Drift history + current snapshot for one dataset.
    Returns 404 when the dataset has no snapshot (never been observed).

Security
--------
All routes are org-scoped via ``verified_identity``.  A dataset belonging to a
different org returns 404 (not 403) to avoid information leakage.  Both embed
tokens and first-party tokens are accepted (read-only surface, same as the
existing /health/* routes).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth.deps import verified_identity
from app.auth.verify import VerifiedIdentity
from app.errors import AppError
from app.health.schema_drift import get_drift_store
from app.routes import api_router
from app.routes._org import get_user_org
from app.repos.provider import get_repo

logger = logging.getLogger("nubi.health.drift")

_router = APIRouter(prefix="/health", tags=["health"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _resolve_org(identity: VerifiedIdentity) -> str:
    """Resolve the caller's org_id (mirrors the pattern in routes/health.py)."""
    return await get_user_org(identity.user_id, get_repo())


# ---------------------------------------------------------------------------
# GET /health/drift
# ---------------------------------------------------------------------------


@_router.get("/drift")
async def list_drift_events(
    dataset_key: str | None = Query(None, description="Filter to a single dataset key"),
    limit: int = Query(100, ge=1, le=500),
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict[str, Any]:
    """Return recent schema-drift events for the org.

    Pass ``?dataset_key=raw/orders`` to narrow to one dataset.
    Results are returned newest-first, capped at *limit* (max 500).
    """
    org_id = await _resolve_org(identity)
    store = get_drift_store()

    _list = store.list_events(org_id, dataset_key=dataset_key, limit=limit)
    if hasattr(_list, "__await__"):
        events = await _list
    else:
        events = _list

    return {
        "org_id": org_id,
        "dataset_key": dataset_key,
        "events": events,
    }


# ---------------------------------------------------------------------------
# GET /health/drift/{dataset_key}
# ---------------------------------------------------------------------------


@_router.get("/drift/{dataset_key:path}")
async def get_drift_for_dataset(
    dataset_key: str,
    limit: int = Query(100, ge=1, le=500),
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict[str, Any]:
    """Return drift history + current snapshot for one dataset.

    Returns 404 when the dataset has never been observed (no snapshot).
    """
    org_id = await _resolve_org(identity)
    store = get_drift_store()

    # Snapshot lookup (sync or async store).
    _snap = store.get_snapshot(org_id, dataset_key)
    if hasattr(_snap, "__await__"):
        snapshot = await _snap
    else:
        snapshot = _snap

    if snapshot is None:
        raise AppError(
            "DATASET_NOT_FOUND",
            f"No schema snapshot found for dataset '{dataset_key}'.",
            404,
        )

    # Drift events for this dataset.
    _evts = store.list_events(org_id, dataset_key=dataset_key, limit=limit)
    if hasattr(_evts, "__await__"):
        events = await _evts
    else:
        events = _evts

    return {
        "org_id": org_id,
        "dataset_key": dataset_key,
        "current_snapshot": snapshot,
        "events": events,
    }


# ---------------------------------------------------------------------------
# Register sub-router on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(_router)
