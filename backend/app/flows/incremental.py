"""Incremental materialization helpers for the Flows engine (SQLMesh-style, v1).

A ``materialize`` task may carry a nested ``materialized`` config block that
declares how the combined result is persisted to object storage:

    config.materialized = {
        "kind": "view" | "full" | "incremental",   # default "view"
        "target": "<logical/path>",                 # required when kind != "view"
        "time_column": "<col>",                     # required when kind == "incremental"
        "unique_key": ["col", ...],                 # optional; present => upsert/merge
        "lookback": "3 days",                        # optional; parsed to timedelta
        "base_uri": "<override>",                    # optional base-uri override
    }

Cloud Run is stateless, so materialized/incremental TARGETS live in OBJECT
STORAGE (``s3://`` Parquet via the httpfs path) or a local fallback directory.
The per-(flow, model, env) WATERMARK lives in Postgres (``flow_watermarks``).

Public API
----------
parse_lookback(s) -> timedelta
    Parse a human lookback string (e.g. ``"3 days"``, ``"12h"``) into a
    ``timedelta``.  Returns ``timedelta(0)`` for empty / unparseable input.

resolve_target_uri(env, materialized, flow, settings) -> str
    Join ``base_uri`` / ``env`` / ``target`` into a physical Parquet URI,
    preserving the ``s3://`` scheme.  Env namespaces the target so dev and
    prod never clobber each other.

apply_incremental(connector, combined_table, materialized, watermark, now)
        -> (rows_written, new_watermark)
    Persist *combined_table* according to ``materialized.kind``:
      - ``view``        — no-op (caller handles the view path).
      - ``full``        — overwrite the target Parquet.
      - ``incremental`` — filter ``time_column > watermark - lookback``, then
        append (or delete-then-insert on ``unique_key``) into the target,
        returning the new max(time_column) as the watermark.

Security notes
--------------
- ``combine_sql`` and the materialized config are author-provided (first-party,
  org-scoped); they are NOT end-user input.
- The connector is opened with the engine's standard storage bootstrap; RLS
  preservation is enforced by the caller (``materialize_blend``).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any

from app.errors import AppError

# Default local base directory when no object-storage base_uri is configured.
# Resolved lazily so the module imports cleanly regardless of cwd.
_DEFAULT_LOCAL_SUBDIR = ("seed_data", "materialized")


# ---------------------------------------------------------------------------
# Lookback parsing
# ---------------------------------------------------------------------------

# Accepts "3 days", "3d", "12 hours", "12h", "30m", "45s", "2 weeks", etc.
_LOOKBACK_UNITS: dict[str, str] = {
    "s": "seconds",
    "sec": "seconds",
    "secs": "seconds",
    "second": "seconds",
    "seconds": "seconds",
    "m": "minutes",
    "min": "minutes",
    "mins": "minutes",
    "minute": "minutes",
    "minutes": "minutes",
    "h": "hours",
    "hr": "hours",
    "hrs": "hours",
    "hour": "hours",
    "hours": "hours",
    "d": "days",
    "day": "days",
    "days": "days",
    "w": "weeks",
    "week": "weeks",
    "weeks": "weeks",
}

_LOOKBACK_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)")


def parse_lookback(s: Any) -> timedelta:
    """Parse a lookback string into a :class:`datetime.timedelta`.

    Examples
    --------
    ``"3 days"`` → ``timedelta(days=3)``; ``"12h"`` → ``timedelta(hours=12)``;
    ``"1 week 2 days"`` → ``timedelta(weeks=1, days=2)``.

    Empty / ``None`` / unparseable input returns ``timedelta(0)`` so callers
    can always add it to a watermark safely.
    """
    if not s:
        return timedelta(0)
    if isinstance(s, timedelta):
        return s
    text = str(s).strip().lower()
    if not text:
        return timedelta(0)

    total = timedelta(0)
    matched = False
    for value, unit in _LOOKBACK_RE.findall(text):
        canonical = _LOOKBACK_UNITS.get(unit)
        if canonical is None:
            continue
        matched = True
        total += timedelta(**{canonical: float(value)})
    return total if matched else timedelta(0)


# ---------------------------------------------------------------------------
# Target URI resolution
# ---------------------------------------------------------------------------


def _default_local_base() -> str:
    """Return the absolute local fallback base directory for materializations."""
    here = os.path.dirname(os.path.abspath(__file__))
    backend = os.path.dirname(os.path.dirname(here))  # backend/app/flows -> backend
    return os.path.join(backend, *_DEFAULT_LOCAL_SUBDIR)


def _base_uri(
    materialized: dict[str, Any],
    flow: dict[str, Any] | None,
    settings: Any,
) -> str:
    """Resolve the base URI precedence chain.

    Precedence: ``materialized.base_uri`` → ``flow.runtime_config
    ['materialize_base_uri']`` → ``settings.FLOWS_MATERIALIZE_BASE_URI`` →
    local ``<backend>/seed_data/materialized``.
    """
    explicit = (materialized or {}).get("base_uri")
    if explicit:
        return str(explicit).rstrip("/")

    if flow:
        rc = flow.get("runtime_config")
        if not isinstance(rc, dict):
            # Flow spec may nest runtime_config under spec.
            spec = flow.get("spec")
            rc = spec.get("runtime_config") if isinstance(spec, dict) else None
        if isinstance(rc, dict):
            from_rc = rc.get("materialize_base_uri")
            if from_rc:
                return str(from_rc).rstrip("/")

    if settings is not None:
        from_settings = getattr(settings, "FLOWS_MATERIALIZE_BASE_URI", "") or ""
        if from_settings:
            return str(from_settings).rstrip("/")

    return _default_local_base()


def _is_remote(uri: str) -> bool:
    """Return ``True`` for object-storage schemes (s3://, gs://, az://...)."""
    return bool(re.match(r"^(s3|s3a|gs|gcs|az|abfss?|http|https)://", uri, re.IGNORECASE))


def resolve_target_uri(
    env: str,
    materialized: dict[str, Any],
    flow: dict[str, Any] | None = None,
    settings: Any = None,
) -> str:
    """Resolve the env-scoped physical target URI for a materialization.

    The logical ``materialized.target`` is joined under ``<base_uri>/<env>/``
    so dev and prod never clobber each other.  A ``.parquet`` suffix is added
    when the target has no recognised extension.

    Parameters
    ----------
    env:
        The active environment ("dev", "prod", custom).  Falls back to "prod".
    materialized:
        The ``materialized`` config block (must include ``target``).
    flow:
        The flow dict (for ``runtime_config.materialize_base_uri``).
    settings:
        The app settings object (for ``FLOWS_MATERIALIZE_BASE_URI``).

    Returns
    -------
    str
        The physical URI (``s3://...parquet`` or a local absolute path).
    """
    target = str((materialized or {}).get("target") or "").strip()
    if not target:
        raise AppError(
            "invalid_task_config",
            "materialized config requires 'target' for non-view kinds.",
            400,
        )
    env = (env or "prod").strip() or "prod"

    base = _base_uri(materialized, flow, settings)
    # Strip any leading slashes on target so it composes as a relative segment.
    rel_target = target.lstrip("/")

    if _is_remote(base):
        joined = f"{base}/{env}/{rel_target}"
    else:
        # Local filesystem path.
        joined = os.path.join(base, env, *rel_target.split("/"))

    # Ensure a .parquet extension (v1 targets are Parquet).
    root, ext = os.path.splitext(joined)
    if ext.lower() not in (".parquet", ".pq"):
        joined = joined + ".parquet"
    return joined


# ---------------------------------------------------------------------------
# Incremental apply
# ---------------------------------------------------------------------------


def _raw_conn(connector: Any) -> Any:
    """Return the raw DuckDB connection from a (storage) connector."""
    inner = getattr(connector, "_inner", None)
    if inner is not None and getattr(inner, "_conn", None) is not None:
        return inner._conn
    conn = getattr(connector, "_conn", None)
    if conn is not None:
        return conn
    raise AppError(
        "materialize_engine_error",
        "Connector does not expose a raw DuckDB connection for write-back.",
        500,
    )


def _target_exists(conn: Any, target_uri: str) -> bool:
    """Return ``True`` when the target Parquet already exists / is readable."""
    effective = target_uri[len("file://"):] if target_uri.startswith("file://") else target_uri
    if not _is_remote(effective):
        return os.path.exists(effective)
    # Remote: probe via a cheap read; treat read failure as "does not exist".
    try:
        conn.execute(f"SELECT 1 FROM read_parquet('{effective}') LIMIT 0")
        return True
    except Exception:  # noqa: BLE001
        return False


def _write_parquet(conn: Any, select_sql: str, target_uri: str) -> None:
    """Overwrite *target_uri* with the result of *select_sql* (Parquet)."""
    effective = target_uri[len("file://"):] if target_uri.startswith("file://") else target_uri
    if not _is_remote(effective):
        parent = os.path.dirname(effective)
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn.execute(f"COPY ({select_sql}) TO '{effective}' (FORMAT parquet)")


def _max_time(conn: Any, table: str, time_column: str) -> str | None:
    """Return the max(time_column) over *table* as an ISO string (or None)."""
    try:
        row = conn.execute(
            f'SELECT max("{time_column}") FROM {table}'
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row or row[0] is None:
        return None
    val = row[0]
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def apply_incremental(
    connector: Any,
    combined_table: Any,
    materialized: dict[str, Any],
    watermark: str | None,
    now: datetime,
) -> tuple[int, str | None]:
    """Persist *combined_table* per ``materialized.kind``; return write stats.

    Parameters
    ----------
    connector:
        A storage connector exposing a raw DuckDB connection (``_inner._conn``)
        with any httpfs/S3 bootstrap already applied for remote targets.
    combined_table:
        The merged result as a ``pyarrow.Table`` (output of ``combine_sql``).
    materialized:
        The ``materialized`` config block (kind/target/time_column/unique_key/
        lookback/base_uri).  ``target`` MUST already be the resolved physical
        URI under the key ``__physical_target__`` (injected by the caller).
    watermark:
        The stored watermark (ISO string) for this (flow, model, env), or
        ``None`` on the first incremental run.
    now:
        Injected clock datetime (unused for v1 but kept for determinism / future
        lookback-from-now semantics).

    Returns
    -------
    tuple[int, str | None]
        ``(rows_written, new_watermark)``.  ``new_watermark`` is the max
        ``time_column`` value across the rows now present in the target (ISO
        string), or the prior watermark when nothing new was written.
    """
    kind = str((materialized or {}).get("kind") or "view").lower()
    target_uri = str((materialized or {}).get("__physical_target__") or "")

    if kind == "view":
        # No persistence — caller keeps the existing view path.
        return (0, watermark)

    if not target_uri:
        raise AppError(
            "invalid_task_config",
            "apply_incremental requires a resolved '__physical_target__'.",
            500,
        )

    conn = _raw_conn(connector)
    # Register the combined arrow table for SQL access.
    conn.register("__combined_src__", combined_table)

    try:
        if kind == "full":
            _write_parquet(conn, "SELECT * FROM __combined_src__", target_uri)
            rows = conn.execute(
                "SELECT count(*) FROM __combined_src__"
            ).fetchone()[0]
            new_wm = watermark
            time_column = (materialized or {}).get("time_column")
            if time_column:
                new_wm = _max_time(conn, "__combined_src__", time_column) or watermark
            return (int(rows), new_wm)

        if kind != "incremental":
            raise AppError(
                "invalid_task_config",
                f"Unknown materialized kind {kind!r} (expected view/full/incremental).",
                400,
            )

        # ── Incremental ──────────────────────────────────────────────────────
        time_column = str((materialized or {}).get("time_column") or "")
        if not time_column:
            raise AppError(
                "invalid_task_config",
                "incremental materialization requires 'time_column'.",
                400,
            )
        unique_key: list[str] = list((materialized or {}).get("unique_key") or [])
        lookback = parse_lookback((materialized or {}).get("lookback"))

        # Filter the new rows: time_column > (watermark - lookback).
        # On first run (no watermark) everything qualifies.
        if watermark:
            # Effective cutoff = watermark - lookback (re-process the tail).
            # Cast both sides to TIMESTAMP so string/timestamp time_columns
            # compare correctly (rows often arrive as ISO strings).
            cutoff_sql = (
                f"CAST('{watermark}' AS TIMESTAMP) - "
                f"INTERVAL '{int(lookback.total_seconds())} second'"
            )
            filtered_sql = (
                f'SELECT * FROM __combined_src__ '
                f'WHERE CAST("{time_column}" AS TIMESTAMP) > {cutoff_sql}'
            )
        else:
            filtered_sql = "SELECT * FROM __combined_src__"

        # Materialise the filtered new rows into a temp table.
        conn.execute("DROP TABLE IF EXISTS __new_rows__")
        conn.execute(f"CREATE TEMP TABLE __new_rows__ AS {filtered_sql}")
        new_count = conn.execute("SELECT count(*) FROM __new_rows__").fetchone()[0]

        target_present = _target_exists(conn, target_uri)
        effective = (
            target_uri[len("file://"):]
            if target_uri.startswith("file://")
            else target_uri
        )

        if not target_present:
            # First write — the target is just the filtered new rows.
            _write_parquet(conn, "SELECT * FROM __new_rows__", target_uri)
        else:
            # Load the existing target into a temp table for merge.
            conn.execute("DROP TABLE IF EXISTS __existing__")
            conn.execute(
                f"CREATE TEMP TABLE __existing__ AS "
                f"SELECT * FROM read_parquet('{effective}')"
            )
            if unique_key:
                # delete-then-insert (upsert): drop existing rows whose unique
                # key matches an incoming row, then union the new rows.
                key_join = " AND ".join(
                    f'__existing__."{k}" = __new_rows__."{k}"' for k in unique_key
                )
                conn.execute(
                    f"DELETE FROM __existing__ "
                    f"WHERE EXISTS (SELECT 1 FROM __new_rows__ WHERE {key_join})"
                )
            merged_sql = (
                "SELECT * FROM __existing__ UNION ALL SELECT * FROM __new_rows__"
            )
            _write_parquet(conn, merged_sql, target_uri)

        # New watermark = max(time_column) over the rows just written, but never
        # regress below the prior watermark.
        new_max = _max_time(conn, "__new_rows__", time_column)
        new_wm = watermark
        if new_max is not None:
            if watermark is None or new_max > watermark:
                new_wm = new_max
        return (int(new_count), new_wm)
    finally:
        try:
            conn.unregister("__combined_src__")
        except Exception:  # noqa: BLE001
            pass
