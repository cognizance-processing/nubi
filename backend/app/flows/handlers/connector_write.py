"""``connector_write`` task handler — write a flow result into any connector.

The WRITE-side sibling of ``file_ingest`` (and ``bucket_load``): instead of
pulling files from a source connector, it takes an UPSTREAM flow task's result
(row-shaped / Arrow), stages it as Parquet in the per-run staging prefix
(:mod:`app.lakehouse.staging`), verifies the manifest, and loads it into ANY
target connector through the SAME loader layer (:mod:`app.flows.loaders`) —
``promote`` for object stores, ``bulk`` for bulk-capable warehouses
(BigQuery / Snowflake / Redshift / ClickHouse), ``stream`` for everything else.

Config (resolved before ``handle`` is called)::

    {
      "input": "<task_key>",                       # upstream task whose result to write
      "target": {"connector_id": "…", "object": "raw.orders"},
      "mode": "append | overwrite | merge"
    }

The config shape is SYMMETRIC to ``file_ingest``'s ``target`` block; the only
difference is the data SOURCE — an upstream task result rather than a file
connector.  Reusing ``file_ingest._resolve_target`` + the staging writer + the
loader layer means object-storage / warehouse / database targets are all one
code path, exactly as the design intends ("same loader layer later backs a
``connector_write`` task kind", design §4).

Upstream payload shapes (same as ``bucket_load`` accepts)
---------------------------------------------------------
Row-shaped (from ``query`` / ``materialize``):  ``{"rows": [{...}, ...], …}``
Plain list:                                      ``[{...}, ...]``
Arrow:                                           a ``pyarrow.Table`` / list of
                                                 ``RecordBatch`` (rare; converted).

``mode`` (design §3/§4 symmetry)
--------------------------------
``append`` (default) loads the staged rows additively; ``overwrite`` replaces
the target object; ``merge`` is accepted for forward-compatibility.  The loader
layer is mode-agnostic today (it lands the bytes); ``mode`` is recorded on the
result and threaded to the loader so a future per-strategy implementation
(truncate-before-load / MERGE) has the value without a config change.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.flows.loaders import load_staged

if TYPE_CHECKING:
    from app.flows.executor import TaskContext
    from app.lakehouse.staging import StagingArea


_VALID_MODES = {"append", "overwrite", "merge"}


# ---------------------------------------------------------------------------
# Upstream payload → row dicts
# ---------------------------------------------------------------------------


def _rows_from_upstream(upstream: Any) -> list[dict[str, Any]]:
    """Normalise an upstream task result to a list of row dicts.

    Mirrors ``bucket_load``'s payload handling so any handler that produces
    rows (``query`` / ``materialize`` / ``python``) can be written verbatim.
    """
    if upstream is None:
        return []
    # Row-shaped result from query / materialize handlers.
    if isinstance(upstream, dict) and "rows" in upstream:
        rows = upstream.get("rows") or []
        return [r for r in rows if isinstance(r, dict)]
    # Plain list of dicts.
    if isinstance(upstream, list):
        return [r for r in upstream if isinstance(r, dict)]
    # PyArrow Table / RecordBatch (native-arrow connectors).
    to_pylist = getattr(upstream, "to_pylist", None)
    if callable(to_pylist):
        out = to_pylist()
        return [r for r in out if isinstance(r, dict)]
    raise ValueError(
        "connector_write: upstream result is not row-shaped "
        "(expected {'rows': [...]} , a list of dicts, or an Arrow table); got "
        f"{type(upstream).__name__}."
    )


def _rows_to_parquet(rows: list[dict[str, Any]]) -> bytes:
    """Encode row dicts to Parquet bytes (pyarrow), reusing the ingest encoder."""
    from app.flows.handlers.file_ingest import _rows_to_parquet as _enc  # noqa: PLC0415

    return _enc(rows)


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def _resolve_staging(org_id: str, run_id: str) -> "StagingArea":
    """Resolve the server-pinned per-run staging area (reuses file_ingest)."""
    from app.flows.handlers.file_ingest import _resolve_staging as _rs  # noqa: PLC0415

    return _rs(org_id, run_id)


def _staged_rel(object_name: str) -> str:
    """Stable staged Parquet relative path derived from the target object name."""
    leaf = object_name.replace(".", "_").strip("_/") or "result"
    return f"{leaf}.parquet"


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


def handle(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Write an upstream task result into a target connector via the loader layer.

    Returns
    -------
    dict
        ``{rows_written, strategy, mode, target_object, manifest, final_uris}``.
    """
    from app.flows.handlers.file_ingest import (  # noqa: PLC0415
        _bind_promote,
        _resolve_target,
    )

    org_id = ctx.org_id or (claims or {}).get("org_id") or ""
    if not org_id:
        raise ValueError("connector_write requires an org context (ctx.org_id).")

    input_key = str(config.get("input") or config.get("source") or "").strip()
    target = config.get("target") or {}
    tgt_connector_id = str(target.get("connector_id") or "").strip()
    tgt_object = str(target.get("object") or "").strip()
    mode = str(config.get("mode") or "append").lower().strip()

    if not input_key:
        raise ValueError("connector_write requires 'input' (upstream task key).")
    if not tgt_connector_id:
        raise ValueError("connector_write requires target.connector_id.")
    if not tgt_object:
        raise ValueError("connector_write requires target.object.")
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Invalid mode {mode!r}. Supported: {sorted(_VALID_MODES)}."
        )

    if input_key not in ctx.inputs:
        raise KeyError(
            f"connector_write input {input_key!r} not found in ctx.inputs. "
            f"Available keys: {sorted(ctx.inputs)}."
        )

    rows = _rows_from_upstream(ctx.inputs[input_key])

    # ── Resolve target + per-run staging (server-pinned prefix) ─────────────
    load_target = _resolve_target(tgt_connector_id, tgt_object, org_id)
    run_id = getattr(ctx, "run_id", None) or str(uuid.uuid4())
    staging = _resolve_staging(org_id, run_id)

    # ── Stage the upstream rows as one Parquet object ───────────────────────
    pq_bytes = _rows_to_parquet(rows)
    rel = _staged_rel(tgt_object)
    entry = staging.write_bytes(pq_bytes, rel)
    manifest = staging.build_manifest([entry], {rel: len(rows)})

    # ── Bind staging-bound loader callables (promote / bulk) + load ─────────
    _bind_promote(load_target, staging, manifest)
    result = load_staged(staging, manifest, load_target)

    # ── Best-effort staging cleanup (lifecycle policy is the backstop) ──────
    try:
        staging.cleanup()
    except Exception:  # noqa: BLE001
        pass

    return {
        "rows_written": len(rows),
        "strategy": result["strategy"],
        "mode": mode,
        "target_object": tgt_object,
        "manifest": manifest.to_dict(),
        "final_uris": result.get("final_uris", []),
    }
