"""Persist a SQL cell's own SELECT result per ``config.materialized``.

A SQL (``query``) cell may carry a ``config.materialized`` block — IDENTICAL in
shape to the standalone ``materialize`` task's :class:`MaterializedConfig`::

    config.materialized = {
        "kind": "view" | "full" | "incremental",   # default "view"
        "target": "<logical/path>",                 # required when kind != "view"
        "time_column": "<col>",                     # required when kind == "incremental"
        "unique_key": ["col", ...],                 # optional; present => upsert
        "lookback": "3 days",                        # optional
        "base_uri": "<override>",                    # optional
    }

Unlike a ``materialize`` task (which has a ``combine_sql`` over upstream
sources), a SQL CELL has no combine step — its OWN ``SELECT`` output (the
``{rows, columns}`` already produced by the query handler) is what gets
persisted.  ``kind == 'view'`` ⇒ no persistence (today's behaviour).

This reuses the EXACT object-storage path of ``materialize_blend``:
``incremental.resolve_target_uri`` + ``incremental.apply_incremental`` +
``materialize._open_storage_connector`` / ``_close_storage_connector``.  The
wiring is kept OUT of the shared ``_handle_query`` handler so connector / bridge
handlers and preview stay untouched — the runtime calls this in the query
success path and merges the returned dict (incl. ``new_watermark``) into the
outcome so the existing ``_persist_watermark`` stores it.

Public API
----------
is_persisted(materialized) -> bool
    ``True`` when ``materialized.kind`` is ``'full'`` or ``'incremental'``.

persist_query_result(rows, columns, materialized, *, env, flow, watermark, now)
        -> dict
    Persist *rows* (with declared *columns*) to the env-scoped target per
    *materialized*.  No-op (returns ``{materialized_kind: 'view', ...}``) when
    kind is ``'view'``.  Returns a manifest dict with ``new_watermark`` so the
    runtime can persist the advanced watermark.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.errors import AppError


def is_persisted(materialized: Any) -> bool:
    """Return ``True`` when *materialized* declares a persisted (non-view) kind."""
    if not isinstance(materialized, dict):
        return False
    return str(materialized.get("kind") or "view").lower() in ("full", "incremental")


def _rows_to_arrow(rows: list[dict[str, Any]], columns: list[str] | None) -> Any:
    """Build a ``pyarrow.Table`` from row dicts, preserving declared column order.

    Mirrors ``materialize._rows_to_arrow`` so a SELECT * keeps source ordering
    even when a row is sparse.
    """
    import pyarrow as pa  # noqa: PLC0415

    rows = rows or []
    if columns:
        data: dict[str, list[Any]] = {c: [] for c in columns}
        for row in rows:
            for c in columns:
                data[c].append(row.get(c) if isinstance(row, dict) else None)
        return pa.table(data)
    return pa.Table.from_pylist(rows)


def persist_query_result(
    rows: list[dict[str, Any]],
    columns: list[str] | None,
    materialized: dict[str, Any] | None,
    *,
    env: str = "prod",
    flow: dict[str, Any] | None = None,
    watermark: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist a SQL cell's SELECT result per *materialized*; return a manifest.

    Parameters
    ----------
    rows:
        The cell's result rows (list of dicts) — the query handler output.
    columns:
        Declared column names (preserves ordering); may be ``None``.
    materialized:
        The cell's ``config.materialized`` block.  ``None`` / ``kind=='view'``
        ⇒ no persistence.
    env:
        Active environment ("dev"/"prod"/custom) — namespaces the target.
    flow:
        The flow dict (for ``runtime_config.materialize_base_uri`` resolution).
    watermark:
        The stored incremental watermark (ISO string), or ``None``.
    now:
        Injected clock datetime; defaults to UTC now.

    Returns
    -------
    dict
        For ``view``: ``{"materialized_kind": "view"}``.  For full/incremental:
        ``{materialized_kind, physical_target, env, rows_written, new_watermark}``.
    """
    mat: dict[str, Any] = dict(materialized or {})
    kind = str(mat.get("kind") or "view").lower()

    if kind == "view":
        return {"materialized_kind": "view"}

    if kind not in ("full", "incremental"):
        raise AppError(
            "invalid_task_config",
            f"Unknown materialized kind {kind!r} (expected view/full/incremental).",
            400,
        )

    # Reuse the EXACT object-storage path from materialize_blend.
    from datetime import timezone  # noqa: PLC0415

    from app.flows.incremental import (  # noqa: PLC0415
        apply_incremental,
        resolve_target_uri,
    )
    from app.flows.materialize import (  # noqa: PLC0415
        _close_storage_connector,
        _get_settings,
        _open_storage_connector,
    )

    if now is None:
        now = datetime.now(timezone.utc)

    combined = _rows_to_arrow(rows, columns)

    settings = _get_settings()
    physical_target = resolve_target_uri(env, mat, flow, settings)
    mat_for_apply = dict(mat)
    mat_for_apply["__physical_target__"] = physical_target

    storage = _open_storage_connector(physical_target)
    try:
        rows_written, new_watermark = apply_incremental(
            storage,
            combined,
            mat_for_apply,
            watermark,
            now,
        )
    finally:
        _close_storage_connector(storage)

    return {
        "materialized_kind": kind,
        "physical_target": physical_target,
        "env": env,
        "rows_written": rows_written,
        "new_watermark": new_watermark,
    }
