"""Reusable server-side board-data collector (shared foundation).

This module extracts the board → widget → query → rows pipeline that the
CSV / JSON exports (``app.routes.export_share``) use, so other call sites
(embed snapshots, scheduled deliveries, …) can run the exact same logic
without re-implementing it.

Public API
----------
collect_board_data(board_id, org_id, claims, repo, only_query_id=None)
        -> list[{widget_id, query_id, columns, rows}]
    Resolve an org-scoped board, read its widget ``query_id``s from
    ``config.spec`` and run each registered query server-side, returning one
    entry per data widget.  Best-effort: a widget whose query fails (or whose
    datastore cannot be resolved in this environment) is returned with an
    ``error`` key instead of failing the whole collection.

Security model (unchanged from the exports)
-------------------------------------------
* The board is resolved **org-scoped** via the repo by id — never cross-org.
* The browser never supplies SQL: only the registered ``query_id`` is honoured
  (the registry resolves it to canonical SQL).
* RLS predicates are injected from the verified token's ``policies`` claim
  (passed in via *claims*) at the AST level by the planner — never
  string-concatenated, never sourced from a request body.
* A policy-bearing query is refused on a source that cannot enforce RLS
  server-side (``predicate_rls=False``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import urllib.parse
import weakref
from typing import Any

import pyarrow as pa

from app.errors import AppError
from app.queries.registry import ensure_persisted_query, get_query_registry
from app.repos.provider import Repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-connector threading lock registry
# ---------------------------------------------------------------------------
# DuckDB connections (including the demo singleton) are NOT safe for concurrent
# use from multiple OS threads.  asyncio.to_thread spawns executor threads, so
# two concurrent requests would both call connector.execute on the same shared
# DuckDB connection from different threads — leading to crashes or data
# corruption (the demo DuckDB connector is a process-wide singleton).
#
# Fix: serialise all execute calls that share the same connector instance via a
# per-instance threading.Lock stored in a module-level WeakKeyDictionary so the
# lock is created once per connector object and is automatically collected when
# the connector is GC'd.
#
# This pattern mirrors the fix already applied in board_data.py and keeps the
# asyncio.to_thread offload (the event loop is never blocked) while guaranteeing
# at most one thread executes against a given DuckDB connection at a time.

_connector_locks: "weakref.WeakKeyDictionary[object, threading.Lock]" = (
    weakref.WeakKeyDictionary()
)
_connector_locks_mutex = threading.Lock()  # guards _connector_locks itself


def _get_connector_lock(connector: object) -> threading.Lock:
    """Return the threading.Lock for *connector*, creating it if needed."""
    with _connector_locks_mutex:
        lock = _connector_locks.get(connector)
        if lock is None:
            lock = threading.Lock()
            _connector_locks[connector] = lock
        return lock


def _execute_with_lock(connector: object, physical_plan: object) -> "pa.Table":
    """Execute *physical_plan* on *connector* under the connector's thread lock.

    Called from asyncio.to_thread so the event loop is not blocked, and at most
    one thread may use a given connector simultaneously (DuckDB thread-safety).
    """
    lock = _get_connector_lock(connector)
    with lock:
        return connector.execute(physical_plan)  # type: ignore[attr-defined]

# Maximum rows materialised per widget into Python memory.
# Override via NUBI_COLLECT_ROW_CAP env-var (integer).  0 = unlimited.
_DEFAULT_ROW_CAP = 100_000
_ROW_CAP: int = int(os.environ.get("NUBI_COLLECT_ROW_CAP", _DEFAULT_ROW_CAP))

# Maximum concurrent widget queries per board collection run.
_WIDGET_CONCURRENCY = 8

# Maximum number of Canvas bindings processed per collect call.
# Override via NUBI_MAX_CANVAS_BINDINGS env-var (integer).  0 = unlimited.
_DEFAULT_MAX_CANVAS_BINDINGS = 500
_MAX_CANVAS_BINDINGS: int = int(
    os.environ.get("NUBI_MAX_CANVAS_BINDINGS", _DEFAULT_MAX_CANVAS_BINDINGS)
)

# Maximum number of board widgets processed per collect_board_data call.
# Override via NUBI_MAX_BOARD_WIDGETS env-var (integer).  0 = unlimited.
_DEFAULT_MAX_BOARD_WIDGETS = 500
_MAX_BOARD_WIDGETS: int = int(
    os.environ.get("NUBI_MAX_BOARD_WIDGETS", _DEFAULT_MAX_BOARD_WIDGETS)
)

# Maximum concurrent repo.get("datastores", …) calls issued by _prefetch_datastores.
# The widget path has Semaphore(_WIDGET_CONCURRENCY=8); the prefetch path was
# previously unbounded — up to _MAX_CANVAS_BINDINGS (500) concurrent calls could
# exhaust the DB pool before any widget ran.  Bound it here to avoid that burst.
# Override via NUBI_PREFETCH_CONCURRENCY env-var (integer, positive).
_DEFAULT_PREFETCH_CONCURRENCY = 10
_PREFETCH_CONCURRENCY: int = max(
    1,
    int(os.environ.get("NUBI_PREFETCH_CONCURRENCY", _DEFAULT_PREFETCH_CONCURRENCY)),
)


# ---------------------------------------------------------------------------
# Board / widget helpers
# ---------------------------------------------------------------------------


def spec_from_board(board: dict[str, Any]) -> dict[str, Any]:
    """Return the dashboard spec dict from a board row (``config.spec``).

    Returns an empty spec (no widgets) when the board has no structured spec.
    """
    config = board.get("config") or {}
    spec = config.get("spec")
    if isinstance(spec, dict):
        return spec
    return {"widgets": []}


def widget_query_targets(
    spec: dict[str, Any], only_query_id: str | None
) -> list[dict[str, str]]:
    """Collect ``{widget_id, query_id}`` data targets from a spec.

    Only widgets with a non-empty ``query_id`` are returned (text / pure-filter
    widgets carry no data).  When *only_query_id* is given, the list is filtered
    to that query.  Duplicate (widget_id, query_id) pairs are de-duplicated.
    """
    widgets = spec.get("widgets")
    if not isinstance(widgets, list):
        return []

    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for w in widgets:
        if not isinstance(w, dict):
            continue
        qid = w.get("query_id")
        if not qid:
            continue
        if only_query_id and qid != only_query_id:
            continue
        wid = str(w.get("id") or qid)
        key = (wid, str(qid))
        if key in seen:
            continue
        seen.add(key)
        targets.append({"widget_id": wid, "query_id": str(qid)})
    return targets


# ---------------------------------------------------------------------------
# Server-side query execution (best-effort)
# ---------------------------------------------------------------------------


async def run_query_rows(
    query_id: str,
    org_id: str,
    repo: Repo,
    policies: dict[str, Any],
    *,
    ds_cache: dict[str, Any] | None = None,
    extra_params: dict[str, Any] | None = None,
) -> tuple[list[str], list[list[Any]]]:
    """Run a registered query and return ``(columns, rows)``.

    Reuses the same primitives as ``POST /query``:
      * the query registry resolves ``query_id`` → canonical SQL (the browser
        never supplies SQL here — only the registered id is honoured);
      * the planner injects RLS predicates from *policies* (AST-level, never
        string-concatenated);
      * a datastore-bound query executes against its datastore via the
        connector registry, otherwise the built-in demo DuckDB connector runs.

    Parameters
    ----------
    ds_cache:
        Optional request-scoped ``{ds_id: row}`` mapping.  Forwarded to
        :func:`_resolve_connector` so that a board/canvas collector can
        pre-fetch all referenced datastores once and avoid N identical
        ``repo.get("datastores", …)`` round-trips for N widgets that share a
        single datastore.
    extra_params:
        Optional override values for the query's declared named params (e.g.
        per-recipient locked params on a scheduled canvas send).  These are
        merged ON TOP of the registered param defaults before the ``{{name}}``
        placeholders are resolved to positional ``$N`` binds, so a recipient's
        ``{region: 'X'}`` actually NARROWS that recipient's data via the named
        query binding.  Values are bound positionally (never string-
        concatenated) and CANNOT touch the RLS policy slice — RLS comes from
        *policies* only.  Unknown keys (names not declared by the query) are
        ignored, mirroring the board path's named-param resolution.

    Raises ``AppError`` with a descriptive code on any failure so the caller can
    decide whether to skip the widget or surface the error.
    """
    registry = get_query_registry()
    registered = registry.get(query_id) or await ensure_persisted_query(query_id)
    if registered is None:
        raise AppError("query_not_registered", f"No registered query for id={query_id!r}.", 404)

    # Resolve declared named params, turning {{name}} placeholders into
    # positional $N binds.  Each declared param resolves to its default unless a
    # caller-supplied override is present in *extra_params* (per-recipient
    # locked params).  Overrides NARROW the result via the named binding only;
    # they never reach the RLS policy slice (that is *policies*).
    sql = registered.sql
    params: list[Any] = []
    if registered.params:
        from app.connectors.planner import resolve_named_params

        overrides = extra_params or {}
        resolved = {}
        for p in registered.params:
            if p.name in overrides:
                resolved[p.name] = overrides[p.name]
            else:
                resolved[p.name] = p.default if p.default is not None else None
        sql, params = resolve_named_params(sql, resolved)

    from app.connectors import plan as planner_plan

    # Push _ROW_CAP+1 as a LIMIT into the physical plan so the DB/connector
    # never materialises more than cap+1 rows into memory.  Using cap+1 (not
    # cap) preserves the ability to detect truncation: len(arrow_table) > cap
    # is still detectable when the connector returns exactly cap+1 rows.
    # The post-fetch slice below is kept as a backstop for connectors that
    # ignore the plan's LIMIT node.
    _plan_limit: int | None = (_ROW_CAP + 1) if _ROW_CAP > 0 else None
    physical_plan = planner_plan(
        sql=sql, claims={"policies": policies}, params=params, limit=_plan_limit
    )

    connector, connector_owned = await _resolve_connector(
        registered, org_id, repo, physical_plan, ds_cache=ds_cache
    )

    try:
        # [HIGH concurrency] Use _execute_with_lock so at most one thread
        # executes against a given DuckDB connection at a time.  The demo
        # connector is a process-wide singleton whose connection is NOT
        # thread-safe; asyncio.to_thread would otherwise allow concurrent
        # threads to collide on the same connection.
        arrow_table = await asyncio.to_thread(_execute_with_lock, connector, physical_plan)
        cap = _ROW_CAP
        if cap > 0 and len(arrow_table) > cap:
            logger.warning(
                "collect: widget query_id=%r returned %d rows, truncating to %d "
                "(set NUBI_COLLECT_ROW_CAP to raise limit)",
                query_id,
                len(arrow_table),
                cap,
            )
            arrow_table = arrow_table.slice(0, cap)
        columns = list(arrow_table.schema.names)
        raw_records = arrow_table.to_pylist()
        rows: list[list[Any]] = [[record.get(c) for c in columns] for record in raw_records]
        return columns, rows
    finally:
        # Only close connectors that were freshly created for this query
        # (connector_owned=True).  The demo connector is a module-level
        # singleton and must NOT be closed — it would invalidate the shared
        # in-memory DuckDB connection for all subsequent requests.
        if connector_owned:
            connector.close()


async def _resolve_connector(
    registered: Any,
    org_id: str,
    repo: Repo,
    physical_plan: Any,
    *,
    ds_cache: dict[str, Any] | None = None,
) -> tuple[Any, bool]:
    """Pick the connector for a registered query (datastore-bound or demo).

    Pragmatic subset of the ``POST /query`` connector path: it covers the demo
    connector and a directly-configured duckdb/postgres/http_json datastore.
    Secret injection and network bridges are intentionally out of scope here —
    if a datastore needs them, the connector construction will raise and the
    caller skips that widget (best-effort collection).

    Parameters
    ----------
    ds_cache:
        Optional request-scoped ``{ds_id: row}`` mapping pre-fetched by the
        board/canvas collector.  When provided, a datastore lookup is served
        from the cache without an extra repo round-trip.  The cache is
        populated by :func:`_prefetch_datastores`.

    Returns
    -------
    tuple[connector, owned]
        *connector* — the resolved connector instance.
        *owned* — ``True`` when the connector was freshly created for this call
        and the caller is responsible for closing it.  ``False`` when the
        connector is a shared singleton (e.g. the demo connector) that must
        NOT be closed by the caller.
    """
    datastore_id = getattr(registered, "datastore_id", None)
    if not datastore_id:
        from app.routes.query import _get_demo_connector

        return _get_demo_connector(), False  # singleton — caller must not close

    # Use the pre-fetched cache when available to avoid an extra repo round-trip.
    if ds_cache is not None and datastore_id in ds_cache:
        ds = ds_cache[datastore_id]
    else:
        ds = await repo.get("datastores", org_id, datastore_id)
    if ds is None:
        raise AppError("datastore_not_found", f"Datastore {datastore_id!r} not found.", 404)

    from app.connectors.registry import get_connector_registry

    cfg: dict[str, Any] = dict(ds.get("config") or {})
    ctype = cfg.get("type")
    factory = get_connector_registry().get(ctype)

    if ctype == "duckdb":
        db_path = cfg.get("database") or cfg.get("path")
        if db_path and db_path != ":memory:":
            import duckdb

            from app.connectors.duckdb_conn import harden_connection as _harden

            conn = duckdb.connect(database=db_path, read_only=True)
            _harden(conn, disable_external_access=True)
            return factory(conn), True  # owned: on-disk connection to close
        return factory(), True
    if ctype == "postgres":
        dsn = cfg.get("dsn")
        if dsn is None:
            host = cfg.get("host", "localhost")
            port = cfg.get("port", 5432)
            dbname = cfg.get("dbname") or cfg.get("database") or "postgres"
            user = cfg.get("user") or cfg.get("username") or "postgres"
            password = cfg.get("password", "")
            dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
        return factory(dsn), True

    connector = factory(cfg)

    # Defence-in-depth: never run a policy-bearing query on a source that
    # cannot enforce RLS server-side.
    policies = (getattr(physical_plan, "rls_claims", None) or {}).get("policies") or {}
    if policies and connector.capabilities().get("predicate_rls") is False:
        raise AppError(
            "source_unsupported_rls",
            "Source does not support Row-Level Security (predicate_rls=False).",
            501,
        )
    return connector, True


# ---------------------------------------------------------------------------
# Datastore pre-fetch helper (N+1 elimination)
# ---------------------------------------------------------------------------


async def _prefetch_datastores(
    ds_ids: set[str], org_id: str, repo: Repo
) -> dict[str, Any]:
    """Fetch *ds_ids* concurrently (bounded) and return a cache dict.

    Returns a ``{ds_id: row}`` mapping for all datastore ids that were found.
    Ids that resolve to ``None`` (not found / cross-org) are omitted from the
    cache so that downstream code falls through to the normal
    ``datastore_not_found`` error path inside :func:`_resolve_connector`.

    This is called once at the start of :func:`collect_board_data` and
    :func:`collect_canvas_data` to eliminate N identical ``repo.get`` calls
    when N widgets/bindings share the same datastore.

    Concurrency is capped at ``_PREFETCH_CONCURRENCY`` (env
    ``NUBI_PREFETCH_CONCURRENCY``, default 10) so that a canvas with up to
    ``_MAX_CANVAS_BINDINGS`` (500) distinct datastores cannot fire 500
    simultaneous DB connections before any widget query runs — previously this
    path had no semaphore while the widget path had ``Semaphore(8)``.
    """
    if not ds_ids:
        return {}

    semaphore = asyncio.Semaphore(_PREFETCH_CONCURRENCY)

    async def _fetch_one(ds_id: str) -> tuple[str, Any]:
        async with semaphore:
            row = await repo.get("datastores", org_id, ds_id)
        return ds_id, row

    pairs = await asyncio.gather(*(_fetch_one(ds_id) for ds_id in ds_ids))
    return {ds_id: row for ds_id, row in pairs if row is not None}


# ---------------------------------------------------------------------------
# Public collector
# ---------------------------------------------------------------------------


async def collect_board_data(
    board_id: str,
    org_id: str,
    claims: dict[str, Any],
    repo: Repo,
    only_query_id: str | None = None,
    *,
    board: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect per-widget data for an org-scoped board.

    Resolves the board (org-scoped) from *repo*, reads the widget
    ``query_id``s from its ``config.spec`` and runs each registered query
    server-side, returning one entry per data widget::

        [{"widget_id": str, "query_id": str, "columns": [...], "rows": [...]}]

    Per-widget errors are reported inline via an ``"error"`` key (the code of
    the raised :class:`~app.errors.AppError`, or ``"export_failed: <Type>"``
    for an unexpected exception) instead of aborting the whole collection —
    mirroring the best-effort behaviour of the CSV / JSON exports.

    Parameters
    ----------
    board_id:
        The board id to resolve (org-scoped).
    org_id:
        The caller's resolved org id.  Both the board and any datastore are
        looked up scoped to this org — never cross-org.
    claims:
        The verified token's claims.  Only ``claims["policies"]`` is consulted
        here (the RLS predicate context); it is forwarded to the planner.  RLS
        is sourced from the verified token only, never from a request body.
    repo:
        Active repository implementation.
    only_query_id:
        When given, restrict collection to widgets whose ``query_id`` matches.
    board:
        Optional pre-fetched board row.  When supplied, the repo lookup is
        skipped — callers that already hold the row (e.g.
        ``render_board_svg_from_data``) can pass it in to avoid a redundant
        round-trip.

    Returns
    -------
    list[dict]
        One entry per data widget (see above).  Empty when the board has no
        data widgets.

    Raises
    ------
    AppError("board_not_found", 404)
        When no board with *board_id* exists in *org_id*.
    """
    if board is None:
        board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)

    spec = spec_from_board(board)
    targets = widget_query_targets(spec, only_query_id)

    # Cap the number of widgets to prevent excessive resource consumption.
    max_widgets = _MAX_BOARD_WIDGETS
    if max_widgets > 0 and len(targets) > max_widgets:
        logger.warning(
            "collect_board_data: board_id=%r has %d widgets, truncating to %d "
            "(set NUBI_MAX_BOARD_WIDGETS to raise limit)",
            board_id,
            len(targets),
            max_widgets,
        )
        targets = targets[:max_widgets]

    # RLS comes from the verified token's policies claim only.
    policies: dict[str, Any] = dict((claims or {}).get("policies") or {})

    # Pre-fetch the unique datastores referenced by all widget queries in one
    # asyncio.gather so N widgets sharing one datastore cost exactly 1 lookup,
    # not N.  We resolve query registrations to find their datastore_id first.
    registry = get_query_registry()
    unique_ds_ids: set[str] = set()
    for t in targets:
        reg = registry.get(t["query_id"])
        if reg is not None:
            ds_id = getattr(reg, "datastore_id", None)
            if ds_id:
                unique_ds_ids.add(ds_id)
    ds_cache = await _prefetch_datastores(unique_ds_ids, org_id, repo)

    async def _fetch_one(t: dict[str, str]) -> dict[str, Any]:
        entry: dict[str, Any] = {"widget_id": t["widget_id"], "query_id": t["query_id"]}
        try:
            columns, rows = await run_query_rows(
                t["query_id"], org_id, repo, policies, ds_cache=ds_cache
            )
            entry["columns"] = columns
            entry["rows"] = rows
        except AppError as exc:
            entry["error"] = exc.code
        except Exception as exc:  # noqa: BLE001 — best-effort collection
            entry["error"] = f"export_failed: {exc.__class__.__name__}"
        return entry

    # Run widget queries concurrently (bounded by _WIDGET_CONCURRENCY) while
    # preserving the original target order in the returned list.
    semaphore = asyncio.Semaphore(_WIDGET_CONCURRENCY)

    async def _fetch_guarded(t: dict[str, str]) -> dict[str, Any]:
        async with semaphore:
            return await _fetch_one(t)

    out: list[dict[str, Any]] = list(
        await asyncio.gather(*(_fetch_guarded(t) for t in targets))
    )

    return out


# ---------------------------------------------------------------------------
# Canvas data collector (Canvas Wave 1)
# ---------------------------------------------------------------------------


async def collect_canvas_data(
    canvas_id: str,
    org_id: str,
    claims: dict[str, Any],
    repo: Repo,
    *,
    canvas: dict[str, Any] | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Collect per-binding data for an org-scoped Canvas document.

    Resolves the canvas (org-scoped) from *repo*, iterates the ``bindings``
    map in ``config.doc``, and runs each binding against the appropriate
    backend (query registry, metric compiler, HTTP_JSON connector), returning a
    dict keyed by ``el_id``::

        {
            "el_1": {"columns": [...], "rows": [...]},
            "el_2": {"columns": [...], "rows": [...]},
            "el_3": {"error": "query_not_registered"},
        }

    Security model (identical to collect_board_data)
    -------------------------------------------------
    * The canvas is resolved **org-scoped** — never cross-org.
    * RLS predicates come from ``claims["policies"]`` (the verified token) only.
    * Query execution reuses ``run_query_rows``, which enforces RLS at the AST
      level via the planner — never from the request body.
    * API (HTTP_JSON) connector resolution reuses ``_resolve_connector`` — the
      ``source_unsupported_rls`` guard applies unchanged.

    Parameters
    ----------
    canvas_id:
        The canvas id to resolve (org-scoped).
    org_id:
        The caller's org id.
    claims:
        The verified token's claims.  Only ``claims["policies"]`` is used here.
    repo:
        Active repository implementation.
    canvas:
        Optional pre-fetched canvas row.  When supplied, the repo lookup is
        skipped.
    extra_params:
        Optional per-recipient named-param overrides (e.g. a scheduled canvas
        send's ``locked_params[recipient]``).  Forwarded to
        :func:`run_query_rows` for ``query`` / ``metric`` bindings so the
        recipient's ``{region: 'X'}`` actually NARROWS that recipient's binding
        data via the named query binding — the per-recipient render no longer
        collapses to the unfiltered owner view.  These bind named ``{{param}}``
        placeholders ONLY; they NEVER alter the RLS policy slice, which comes
        from ``claims["policies"]`` (the owner's verified snapshot) exclusively.

    Returns
    -------
    dict[str, list[dict]]
        Mapping from ``el_id`` → ``{columns, rows}`` or ``{error: code}``.

    Raises
    ------
    AppError("canvas_not_found", 404)
        When no canvas with *canvas_id* exists in *org_id*.
    """
    if canvas is None:
        canvas = await repo.get("canvases", org_id, canvas_id)
    if canvas is None:
        raise AppError("canvas_not_found", f"Canvas {canvas_id!r} not found.", 404)

    config = canvas.get("config") or {}
    doc_raw = config.get("doc") or {}
    bindings_raw = doc_raw.get("bindings") or {}

    # RLS comes from the verified token's policies claim only.
    policies: dict[str, Any] = dict((claims or {}).get("policies") or {})

    # Pre-fetch the unique datastores referenced by all bindings in one
    # asyncio.gather so N bindings sharing one datastore cost exactly 1 lookup.
    # "api" bindings carry the datastore id directly (connector_id); "query"
    # bindings need a registry lookup to find their datastore_id.
    registry = get_query_registry()
    unique_ds_ids: set[str] = set()
    for _el_id, binding in bindings_raw.items():
        kind = binding.get("kind")
        if kind == "api":
            cid = binding.get("connector_id") or ""
            if cid:
                unique_ds_ids.add(cid)
        elif kind in ("query", "metric"):
            qid = binding.get("query_id") or (
                # metric bindings back onto queries via source_query_id
                binding.get("metric_id") or ""
            )
            reg = registry.get(qid) if qid else None
            if reg is not None:
                ds_id = getattr(reg, "datastore_id", None)
                if ds_id:
                    unique_ds_ids.add(ds_id)
    ds_cache = await _prefetch_datastores(unique_ds_ids, org_id, repo)

    async def _fetch_binding(el_id: str, binding: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"el_id": el_id}
        kind = binding.get("kind")
        try:
            if kind == "query":
                query_id = binding.get("query_id") or ""
                columns, rows = await run_query_rows(
                    query_id, org_id, repo, policies,
                    ds_cache=ds_cache, extra_params=extra_params,
                )
                entry["columns"] = columns
                entry["rows"] = rows

            elif kind == "metric":
                # Reuse query execution via the metric registry.
                metric_id = binding.get("metric_id") or ""
                try:
                    from app.metrics.registry import get_metric_registry  # noqa: PLC0415
                    mreg = get_metric_registry()
                    mdef = mreg.get(metric_id)
                    if mdef is None:
                        raise AppError(
                            "metric_not_registered",
                            f"No registered metric for id={metric_id!r}.",
                            404,
                        )
                    # Metrics back onto queries — resolve via the query_id on
                    # the metric definition (the source query_id).
                    source_query_id = getattr(mdef, "query_id", None) or metric_id
                    columns, rows = await run_query_rows(
                        source_query_id, org_id, repo, policies,
                        ds_cache=ds_cache, extra_params=extra_params,
                    )
                    entry["columns"] = columns
                    entry["rows"] = rows
                except ImportError:
                    raise AppError(
                        "metric_not_supported",
                        "Metric registry is not available in this build.",
                        501,
                    )

            elif kind == "api":
                # HTTP_JSON connector — resolve the datastore config, merge the
                # binding's 'path' onto the base URL, and use 'select' as
                # record_path so the connector fetches the correct endpoint.
                connector_id = binding.get("connector_id") or ""
                from app.connectors import plan as planner_plan  # noqa: PLC0415
                from app.connectors.http_json import HttpJsonConnector  # noqa: PLC0415

                # Use the pre-fetched cache to avoid a per-binding repo round-trip.
                if ds_cache is not None and connector_id in ds_cache:
                    ds = ds_cache[connector_id]
                else:
                    ds = await repo.get("datastores", org_id, connector_id)
                if ds is None:
                    raise AppError(
                        "datastore_not_found",
                        f"API datastore {connector_id!r} not found.",
                        404,
                    )
                base_cfg: dict[str, Any] = dict(ds.get("config") or {})

                # Merge binding's path onto the base URL (strip trailing slash
                # from the base URL and leading slash from the path so we always
                # get exactly one slash at the join point).
                binding_path: str = binding.get("path") or ""
                binding_select: str | None = binding.get("select") or None

                # [MED path-traversal] Validate that binding_path is a safe
                # relative path before joining it onto the connector base URL.
                # URL-decode the path first so %2e%2e, .%2e, %2e. bypass attempts
                # are normalised to '..' before the segment check.
                # Reject: URL schemes (contains "://"), protocol-relative URLs
                # ("//"), and paths containing ".." segments that could escape
                # the base URL's path hierarchy.
                if binding_path:
                    # Decode percent-encoding to a fixpoint so double-encoded
                    # sequences like %252e%252e are fully expanded before the
                    # safety checks.  Cap iterations to prevent pathological
                    # inputs from looping forever.
                    _path_norm = binding_path
                    for _ in range(16):
                        _next = urllib.parse.unquote(_path_norm)
                        if _next == _path_norm:
                            break
                        _path_norm = _next
                    _path_norm = _path_norm.strip()
                    if (
                        "://" in _path_norm
                        or _path_norm.startswith("//")
                        or ".." in _path_norm.split("/")
                    ):
                        raise AppError(
                            "invalid_binding_path",
                            f"Canvas API binding el_id={el_id!r} has an unsafe path: "
                            f"{binding_path!r}. Paths must be relative, must not contain "
                            f"'..' segments, and must not include a URL scheme or '//'. "
                            "They are joined under the connector's base URL only.",
                            400,
                        )

                merged_cfg: dict[str, Any] = dict(base_cfg)
                if binding_path:
                    base_url: str = str(merged_cfg.get("url") or "").rstrip("/")
                    path_suffix: str = binding_path.lstrip("/")
                    merged_cfg["url"] = f"{base_url}/{path_suffix}" if path_suffix else base_url

                # 'select' is a JSONPath-style selector; the HTTP_JSON connector
                # exposes this as 'record_path' (dot-separated key traversal).
                if binding_select is not None:
                    # Strip the leading "$." prefix that JSONPath uses, since
                    # record_path expects bare dot-separated keys (e.g. "data.items").
                    record_path_val = binding_select.lstrip("$").lstrip(".")
                    if record_path_val:
                        merged_cfg["record_path"] = record_path_val

                connector = HttpJsonConnector(merged_cfg)
                connector_owned = True
                try:
                    api_plan = planner_plan(
                        sql="SELECT *", claims={"policies": policies}, params=[]
                    )
                    # [HIGH concurrency] Also lock the api connector execute.
                    # HttpJsonConnector is typically not shared (each binding gets its
                    # own instance), but applying the same lock pattern is consistent
                    # and safe (a no-contention lock adds negligible overhead).
                    result_table = await asyncio.to_thread(_execute_with_lock, connector, api_plan)
                    cap = _ROW_CAP
                    if cap > 0 and len(result_table) > cap:
                        logger.warning(
                            "collect_canvas: api binding el_id=%r returned %d rows, "
                            "truncating to %d (set NUBI_COLLECT_ROW_CAP to raise limit)",
                            el_id,
                            len(result_table),
                            cap,
                        )
                        result_table = result_table.slice(0, cap)
                    columns = list(result_table.schema.names)
                    rows_raw = result_table.to_pylist()
                    rows = [[r.get(c) for c in columns] for r in rows_raw]
                    entry["columns"] = columns
                    entry["rows"] = rows
                finally:
                    if connector_owned:
                        connector.close()
            else:
                raise AppError(
                    "unknown_binding_kind",
                    f"Canvas binding el_id={el_id!r} has unknown kind={kind!r}.",
                    400,
                )

        except AppError as exc:
            entry["error"] = exc.code
        except Exception as exc:  # noqa: BLE001 — best-effort collection
            entry["error"] = f"collect_failed: {exc.__class__.__name__}"

        return entry

    # Cap the number of bindings to prevent excessive resource consumption.
    bindings_items = list(bindings_raw.items())
    max_bindings = _MAX_CANVAS_BINDINGS
    if max_bindings > 0 and len(bindings_items) > max_bindings:
        logger.warning(
            "collect_canvas: canvas_id=%r has %d bindings, truncating to %d "
            "(set NUBI_MAX_CANVAS_BINDINGS to raise limit)",
            canvas_id,
            len(bindings_items),
            max_bindings,
        )
        bindings_items = bindings_items[:max_bindings]

    # Run binding fetches concurrently (bounded by the same semaphore as boards).
    semaphore = asyncio.Semaphore(_WIDGET_CONCURRENCY)

    async def _fetch_guarded(el_id: str, binding: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _fetch_binding(el_id, binding)

    results = list(
        await asyncio.gather(
            *(_fetch_guarded(el_id, binding) for el_id, binding in bindings_items)
        )
    )

    # Return as a dict keyed by el_id (matching the bindings map shape).
    return {entry["el_id"]: {k: v for k, v in entry.items() if k != "el_id"} for entry in results}
