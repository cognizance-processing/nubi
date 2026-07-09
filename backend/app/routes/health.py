"""Data health & freshness endpoints (B.1 / B.2 / B.3).

Routes (all under ``/api/v1``)
------------------------------
GET  /health/freshness
    Return the pre-computed freshness row for every dataset in the caller's org.
    Single indexed scan on (org_id) — no live computation.

GET  /health/freshness/{dataset_key}
    Return the pre-computed freshness row for one dataset.
    Single (org_id, dataset_key) primary-key lookup — O(1), suitable for
    low-latency UX gates.

    Read SLO target: < 5 ms p99 (single indexed-row lookup; no live computation).

GET  /health/score
    Compute weighted health scores for all datasets in the org (or a single
    dataset when ``?dataset_key=`` is provided).  Returns 0-100 scores with
    weighted dimensions (freshness, completeness, availability) and
    human-readable ``reasons[]``.

GET  /health/estate
    Return a source→raw→model→feature flow map annotated with each node's
    health/freshness status.  Composed from flows + datasets in the org.

Security
--------
All routes are org-scoped via ``verified_identity``.  A dataset belonging to a
different org returns 404 (not 403) to avoid information leakage.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth.deps import verified_identity
from app.auth.verify import VerifiedIdentity
from app.errors import AppError
from app.health.scoring import DEFAULT_WEIGHTS, compute_health_score
from app.health.store import get_freshness_store
from app.repos.provider import get_repo
from app.routes import api_router

logger = logging.getLogger("nubi.health")

# Dedicated sub-router — registered with prefix=/health before the
# /{resource} catch-all in resources.py.
_router = APIRouter(prefix="/health", tags=["health"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _org_id(identity: VerifiedIdentity) -> str:
    """Resolve the caller's org_id via the repo (mirrors watches/metrics pattern)."""
    from app.routes._org import get_user_org  # noqa: PLC0415
    return await get_user_org(identity.user_id, get_repo())


# ---------------------------------------------------------------------------
# B.3 — Freshness registry (low-latency reads)
# ---------------------------------------------------------------------------


@_router.get("/freshness")
async def list_freshness(
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict[str, Any]:
    """Return pre-computed freshness for every dataset in the caller's org.

    O(index scan on org_id) — no live computation.
    """
    org_id = await _org_id(identity)
    store = get_freshness_store()
    records = await store.list_for_org(org_id)
    return {
        "org_id": org_id,
        "datasets": [r.to_dict() for r in records],
    }


@_router.get("/freshness/{dataset_key:path}")
async def get_freshness(
    dataset_key: str,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict[str, Any]:
    """Return the pre-computed freshness row for one dataset.

    Single primary-key lookup: O(1).
    Read SLO target: < 5 ms p99 (single indexed row, no live compute).
    """
    org_id = await _org_id(identity)
    store = get_freshness_store()
    rec = await store.get(org_id, dataset_key)
    if rec is None:
        raise AppError(
            "DATASET_NOT_FOUND",
            f"No freshness record found for dataset '{dataset_key}'.",
            404,
        )
    return rec.to_dict()


# ---------------------------------------------------------------------------
# B.1 — Health scoring
# ---------------------------------------------------------------------------


@_router.get("/score")
async def get_health_score(
    dataset_key: str | None = Query(None, description="Filter to a single dataset key"),
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict[str, Any]:
    """Compute weighted health scores (0-100) for org datasets.

    Dimensions: freshness (default weight 0.50), completeness (0.30),
    availability (0.20).  Weights are configurable per-org via the
    ``health_score_config`` table.  Unknown dimensions are excluded from
    the weighted sum and their weight redistributed to known dimensions.

    Default weights:
        freshness=0.50, completeness=0.30, availability=0.20.
    """
    org_id = await _org_id(identity)
    store = get_freshness_store()

    # Resolve the dataset(s) to score.
    if dataset_key is not None:
        rec = await store.get(org_id, dataset_key)
        if rec is None:
            raise AppError(
                "DATASET_NOT_FOUND",
                f"No freshness record for dataset '{dataset_key}'.",
                404,
            )
        records = [rec]
    else:
        records = await store.list_for_org(org_id)

    # Fetch run history for each dataset (best-effort from flow store).
    results = []
    for rec in records:
        run_history = await _run_history_for_dataset(org_id, rec.dataset_key)

        # Resolve per-dataset (or org-default) weights.
        weights = await _get_weights(store, org_id, rec.dataset_key)

        score_result = compute_health_score(
            dataset_key=rec.dataset_key,
            freshness_status=rec.status,
            run_history=run_history,
            weights=weights,
        )
        results.append({
            "dataset_key": score_result.dataset_key,
            "score": score_result.score,
            "grade": score_result.grade,
            "dimensions": [
                {
                    "name": d.name,
                    "score": d.score,
                    "status": d.status,
                    "reason": d.reason,
                    "weight": score_result.weights_used.get(d.name),
                }
                for d in score_result.dimensions
            ],
            "reasons": score_result.reasons,
            "weights_used": score_result.weights_used,
        })

    if dataset_key is not None and results:
        return results[0]

    return {
        "org_id": org_id,
        "datasets": results,
        "default_weights": DEFAULT_WEIGHTS,
    }


# ---------------------------------------------------------------------------
# B.2 — Health estate graph
# ---------------------------------------------------------------------------


@_router.get("/estate")
async def get_health_estate(
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict[str, Any]:
    """Return a source→raw→model→feature flow map annotated with health.

    Node types
    ----------
    ``source``   — external origin inferred from connector/datastore info.
    ``raw``      — raw ingestion dataset (flow output with dataset_key).
    ``model``    — transformed/modelled dataset (downstream flow outputs).
    ``feature``  — final metric/query target datasets.

    When no flows or datasets exist the graph is empty (``nodes: []``,
    ``edges: []``).
    """
    org_id = await _org_id(identity)
    store = get_freshness_store()

    # Build freshness index for annotation.
    freshness_records = await store.list_for_org(org_id)
    freshness_by_key = {r.dataset_key: r for r in freshness_records}

    # Load flow-level dataset nodes.
    nodes, edges = await _build_estate_nodes(org_id, freshness_by_key)

    return {
        "org_id": org_id,
        "nodes": nodes,
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# Internal helpers (no I/O except flow store)
# ---------------------------------------------------------------------------

async def _run_history_for_dataset(
    org_id: str,
    dataset_key: str,
) -> list[dict[str, Any]]:
    """Best-effort: fetch recent flow_run records that match the dataset key."""
    try:
        from app.flows.store import get_flow_store

        fs = get_flow_store()
        # list_flows scans the org's flows; match by name/dataset_key heuristic.
        flows = await fs.list_flows(org_id, limit=200)
        matching_flow_ids = [
            str(f["id"]) for f in flows
            if _flow_matches_dataset(f, dataset_key)
        ]
        if not matching_flow_ids:
            return []

        # Gather run history from all matching flows (cap at 20 per flow).
        runs: list[dict[str, Any]] = []
        for fid in matching_flow_ids[:5]:  # cap to 5 flows to bound latency
            try:
                flow_runs = await fs.list_flow_runs(fid, limit=20)
                runs.extend(flow_runs)
            except Exception:  # noqa: BLE001
                pass
        return runs
    except Exception:  # noqa: BLE001
        return []


def _flow_matches_dataset(flow: dict[str, Any], dataset_key: str) -> bool:
    """Heuristic: does this flow correspond to the given dataset_key?"""
    name = str(flow.get("name", "")).lower()
    key = dataset_key.lower().replace("/", "_").replace(".", "_")
    return name == key or name == dataset_key.lower() or dataset_key.lower() in name


async def _build_estate_nodes(
    org_id: str,
    freshness_by_key: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build estate nodes from flows and freshness registry."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    # Add a node for every freshness record (these are the known datasets).
    for dk, rec in freshness_by_key.items():
        node_type = _infer_node_type(dk)
        nodes.append({
            "key": dk,
            "type": node_type,
            "status": rec.status,
            "last_success_at": rec.last_success_at.isoformat() if rec.last_success_at else None,
            "expected_interval_s": rec.expected_interval_s,
        })
        seen_keys.add(dk)

    # Enrich from flow definitions (best-effort).
    try:
        from app.flows.store import get_flow_store

        fs = get_flow_store()
        flows = await fs.list_flows(org_id, limit=200)
        for flow in flows:
            fkey = str(flow.get("name", flow.get("id", "")))
            if fkey not in seen_keys:
                status = "unknown"
                last_success = None
                exp_interval = None
                if fkey in freshness_by_key:
                    r = freshness_by_key[fkey]
                    status = r.status
                    last_success = r.last_success_at.isoformat() if r.last_success_at else None
                    exp_interval = r.expected_interval_s
                nodes.append({
                    "key": fkey,
                    "type": _infer_node_type(fkey),
                    "status": status,
                    "last_success_at": last_success,
                    "expected_interval_s": exp_interval,
                })
                seen_keys.add(fkey)

            # Infer edges from flow task ordering (source → sink heuristic).
            spec = flow.get("spec") or {}
            tasks = spec.get("tasks", []) if isinstance(spec, dict) else []
            task_keys = [t.get("key", "") for t in tasks if isinstance(t, dict)]
            for i in range(len(task_keys) - 1):
                src, tgt = task_keys[i], task_keys[i + 1]
                if src and tgt:
                    edges.append({
                        "source_key": src,
                        "target_key": tgt,
                        "flow_id": str(flow.get("id", "")),
                    })
    except Exception:  # noqa: BLE001
        pass

    # Add metric/query layer from query registry (best-effort).
    try:
        from app.queries.registry import get_query_registry

        queries = get_query_registry().list()
        for q in queries:
            qkey = f"metric/{q.get('id', '')}"
            if qkey not in seen_keys:
                nodes.append({
                    "key": qkey,
                    "type": "feature",
                    "status": "unknown",
                    "last_success_at": None,
                    "expected_interval_s": None,
                })
                seen_keys.add(qkey)
    except Exception:  # noqa: BLE001
        pass

    return nodes, edges


def _infer_node_type(key: str) -> str:
    """Infer node type from naming convention."""
    lk = key.lower()
    if any(lk.startswith(p) for p in ("source/", "raw/", "ingest/")):
        return "raw"
    if any(lk.startswith(p) for p in ("model/", "transform/")):
        return "model"
    if any(lk.startswith(p) for p in ("metric/", "feature/", "agg/")):
        return "feature"
    return "source"


async def _get_weights(
    store: Any,
    org_id: str,
    dataset_key: str,
) -> dict[str, float]:
    """Retrieve weight config from the store (sync or async get_weights)."""
    try:
        result = store.get_weights(org_id, dataset_key)
        if hasattr(result, "__await__"):
            return await result
        return result
    except Exception:  # noqa: BLE001
        return dict(DEFAULT_WEIGHTS)


# ---------------------------------------------------------------------------
# Register sub-router on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(_router)
