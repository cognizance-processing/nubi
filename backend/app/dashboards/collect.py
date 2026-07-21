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
from app.queries.registry import get_query_registry, resolve_registered_query
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


def _execute_and_convert(
    connector: object,
    physical_plan: object,
    cap: int,
    query_id: str = "",
) -> "tuple[list[str], list[list[Any]]]":
    """Execute, slice to *cap*, and convert to (columns, rows) — all off-loop.

    This helper is designed to be called from ``asyncio.to_thread`` so that:
    * The event loop is never blocked by connector.execute.
    * The potentially CPU-intensive ``to_pylist()`` + row comprehension for up
      to *cap* rows (100 k rows × many cols = 100–500 ms of pure Python) also
      runs in a worker thread, not on the event loop.

    The per-connector threading lock is acquired here (via ``_execute_with_lock``
    internals) so concurrent threads never touch the same DuckDB connection.

    Parameters
    ----------
    connector:
        The connector whose ``execute`` method is called.
    physical_plan:
        The plan object passed to ``connector.execute``.
    cap:
        The row cap.  0 = unlimited.  When the result exceeds *cap*, the table
        is sliced and a warning is logged (keyed by *query_id* for operator
        visibility).
    query_id:
        Identifier used only in the truncation warning message.

    Returns
    -------
    (columns, rows)
        *columns* — list of column name strings.
        *rows* — list of rows, each a list of scalar values in column order.
    """
    lock = _get_connector_lock(connector)
    with lock:
        arrow_table: pa.Table = connector.execute(physical_plan)  # type: ignore[attr-defined]

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


# Maximum rows materialised per widget into Python memory.
# Override via NUBI_COLLECT_ROW_CAP env-var (integer).  0 = unlimited.
_DEFAULT_ROW_CAP = 100_000
_ROW_CAP: int = int(os.environ.get("NUBI_COLLECT_ROW_CAP", _DEFAULT_ROW_CAP))

# Maximum concurrent widget queries per board collection run.
# Override via NUBI_WIDGET_CONCURRENCY env-var (integer, >=1).
_DEFAULT_WIDGET_CONCURRENCY = 8
_WIDGET_CONCURRENCY: int = max(
    1,
    int(os.environ.get("NUBI_WIDGET_CONCURRENCY", _DEFAULT_WIDGET_CONCURRENCY)),
)

# Process-global widget concurrency semaphore.
# Without a global cap, N concurrent board loads each fan out up to
# _WIDGET_CONCURRENCY coroutines → N × _WIDGET_CONCURRENCY simultaneous DB
# queries.  This semaphore is shared across ALL board loads in the
# process so total concurrent widget execution is bounded to
# _GLOBAL_WIDGET_CONCURRENCY regardless of how many concurrent loads are in
# flight.
#
# The default is 4 × _WIDGET_CONCURRENCY, giving headroom for a few concurrent
# board loads at full width while still capping the global total.  Set
# NUBI_WIDGET_GLOBAL_CONCURRENCY to tune the limit (must be >=1).
_DEFAULT_GLOBAL_WIDGET_CONCURRENCY_MULTIPLIER = 4
_DEFAULT_GLOBAL_WIDGET_CONCURRENCY: int = (
    _WIDGET_CONCURRENCY * _DEFAULT_GLOBAL_WIDGET_CONCURRENCY_MULTIPLIER
)
_GLOBAL_WIDGET_CONCURRENCY: int = max(
    1,
    int(
        os.environ.get("NUBI_WIDGET_GLOBAL_CONCURRENCY", _DEFAULT_GLOBAL_WIDGET_CONCURRENCY)
    ),
)
# The asyncio.Semaphore must be created inside a running event loop on Python
# 3.10+.  We use a lazy-init pattern: the module-level variable holds None
# until first use, at which point _get_global_widget_sem() creates it under a
# threading.Lock so only one Semaphore object is ever created per process.
_global_widget_sem: "asyncio.Semaphore | None" = None
_global_widget_sem_lock = threading.Lock()


def _get_global_widget_sem() -> "asyncio.Semaphore":
    """Return (creating if needed) the process-global widget semaphore."""
    global _global_widget_sem  # noqa: PLW0603
    if _global_widget_sem is None:
        with _global_widget_sem_lock:
            if _global_widget_sem is None:
                _global_widget_sem = asyncio.Semaphore(_GLOBAL_WIDGET_CONCURRENCY)
    return _global_widget_sem

# Maximum number of board widgets processed per collect_board_data call.
# Override via NUBI_MAX_BOARD_WIDGETS env-var (integer).  0 = unlimited.
_DEFAULT_MAX_BOARD_WIDGETS = 500
_MAX_BOARD_WIDGETS: int = int(
    os.environ.get("NUBI_MAX_BOARD_WIDGETS", _DEFAULT_MAX_BOARD_WIDGETS)
)

# Maximum concurrent repo.get("datastores", …) calls issued by _prefetch_datastores.
# The widget path has Semaphore(_WIDGET_CONCURRENCY=8); the prefetch path was
# previously unbounded — up to _MAX_BOARD_WIDGETS (500) concurrent calls could
# exhaust the DB pool before any widget ran.  Bound it here to avoid that burst.
# Override via NUBI_PREFETCH_CONCURRENCY env-var (integer, positive).
_DEFAULT_PREFETCH_CONCURRENCY = 10
_PREFETCH_CONCURRENCY: int = max(
    1,
    int(os.environ.get("NUBI_PREFETCH_CONCURRENCY", _DEFAULT_PREFETCH_CONCURRENCY)),
)

# Process-global prefetch semaphore.
# Without a global cap, N concurrent board loads each call _prefetch_datastores
# with a fresh per-call Semaphore(_PREFETCH_CONCURRENCY) → N × _PREFETCH_CONCURRENCY
# simultaneous repo.get('datastores') calls, exhausting the DB pool before any widget
# query runs.  This semaphore is shared across ALL board loads in the process so
# the total concurrent prefetch repo.get calls are bounded to
# _GLOBAL_PREFETCH_CONCURRENCY regardless of how many concurrent loads are in flight.
#
# Default: 2 × _PREFETCH_CONCURRENCY — gives headroom for a couple of concurrent loads
# while capping the global total.  Override via NUBI_PREFETCH_GLOBAL_CONCURRENCY.
_DEFAULT_GLOBAL_PREFETCH_CONCURRENCY: int = _PREFETCH_CONCURRENCY * 2
_GLOBAL_PREFETCH_CONCURRENCY: int = max(
    1,
    int(
        os.environ.get("NUBI_PREFETCH_GLOBAL_CONCURRENCY", _DEFAULT_GLOBAL_PREFETCH_CONCURRENCY)
    ),
)
# Lazy-init under threading.Lock — same pattern as the widget semaphore — to avoid
# creating an asyncio.Semaphore at import time (Python 3.10+ requires a running loop).
_global_prefetch_sem: "asyncio.Semaphore | None" = None
_global_prefetch_sem_lock = threading.Lock()


def _get_global_prefetch_sem() -> "asyncio.Semaphore":
    """Return (creating if needed) the process-global prefetch semaphore."""
    global _global_prefetch_sem  # noqa: PLW0603
    if _global_prefetch_sem is None:
        with _global_prefetch_sem_lock:
            if _global_prefetch_sem is None:
                _global_prefetch_sem = asyncio.Semaphore(_GLOBAL_PREFETCH_CONCURRENCY)
    return _global_prefetch_sem


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
        :func:`_resolve_connector` so that a board collector can
        pre-fetch all referenced datastores once and avoid N identical
        ``repo.get("datastores", …)`` round-trips for N widgets that share a
        single datastore.
    extra_params:
        Optional override values for the query's declared named params (e.g.
        per-recipient locked params on a scheduled report send).  These are
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
    # SECURITY (CRITICAL 1): resolve query_id through the org-scoped choke
    # point — a bare ``registry.get()`` hit is a process-global dict keyed
    # only by query_id, so it must be re-verified against *org_id* before use
    # (see app.queries.registry.resolve_registered_query).
    registered = await resolve_registered_query(query_id, org_id)
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

    # Plan for the TARGET engine's dialect, exactly as POST /query does (see
    # routes/query.py's target-dialect resolution → `dialect_for(connector_type)`).
    # Planning everything as the default postgres dialect makes sqlglot reject
    # warehouse-native SQL — MySQL boards raised INVALID_SQL on every widget
    # whose query used MySQL-only syntax, even after credentials started working.
    # Fail-safe: any lookup problem falls back to the default dialect rather than
    # breaking collection, matching the query route's behaviour.
    target_dialect = await _dialect_for_registered(registered, org_id, repo, ds_cache)

    # Push _ROW_CAP+1 as a LIMIT into the physical plan so the DB/connector
    # never materialises more than cap+1 rows into memory.  Using cap+1 (not
    # cap) preserves the ability to detect truncation: len(arrow_table) > cap
    # is still detectable when the connector returns exactly cap+1 rows.
    # The post-fetch slice below is kept as a backstop for connectors that
    # ignore the plan's LIMIT node.
    _plan_limit: int | None = (_ROW_CAP + 1) if _ROW_CAP > 0 else None
    # [LOW event-loop] planner_plan() is pure-Python (sqlglot parse + RLS AST
    # rewrite) and can block the loop for non-trivial queries.  Run it on a
    # worker thread; parse_sql_cached's lru_cache is GIL-protected.
    physical_plan = await asyncio.to_thread(
        planner_plan,
        sql=sql,
        claims={"policies": policies},
        params=params,
        limit=_plan_limit,
        dialect=target_dialect,
    )

    connector, connector_owned, net_cleanup = await _resolve_connector(
        registered, org_id, repo, physical_plan, ds_cache=ds_cache
    )

    try:
        # [MED event-loop] _execute_and_convert acquires the per-connector lock,
        # executes the plan, slices to cap, runs to_pylist() + the row
        # comprehension — all inside the worker thread so the event loop is
        # never blocked by execute *or* by the CPU-intensive Python conversion
        # (up to 100 k rows × many cols = 100–500 ms for large results).
        cap = _ROW_CAP
        columns, rows = await asyncio.to_thread(
            _execute_and_convert, connector, physical_plan, cap, query_id
        )
        return columns, rows
    finally:
        # Only close connectors that were freshly created for this query
        # (connector_owned=True).  The demo connector is a module-level
        # singleton and must NOT be closed — it would invalidate the shared
        # in-memory DuckDB connection for all subsequent requests.
        if connector_owned:
            connector.close()
        # Then tear down any ephemeral VPC-bridge tunnel the resolver opened.
        # Ordering matters: close the connection FIRST, or the tunnel is pulled
        # out from under a live socket. Never let a teardown failure mask the
        # real error (or a successful result) — a leaked tunnel is reported by
        # the bridge broker, an exception here would not be.
        try:
            net_cleanup()
        except Exception:  # noqa: BLE001 — best-effort teardown
            logger.warning("net_cleanup failed for query %s", query_id, exc_info=True)


async def _dialect_for_registered(
    registered: Any,
    org_id: str,
    repo: Repo,
    ds_cache: dict[str, Any] | None = None,
) -> str:
    """Resolve the sqlglot dialect for *registered*'s target datastore.

    Mirrors the query route's target-dialect resolution: a BYO datastore's
    ``connector_type`` maps to its native dialect via ``dialect_for`` so
    warehouse-native SQL survives to the engine. The demo/no-datastore path and
    any lookup failure fall back to the historical default (postgres).

    Reads through ``ds_cache`` when the board collector has already pre-fetched
    the datastore rows, so this adds no queries in the common path.
    """
    from app.connectors.dialects import DEFAULT_DIALECT, dialect_for  # noqa: PLC0415

    datastore_id = getattr(registered, "datastore_id", None)
    if not datastore_id:
        return DEFAULT_DIALECT
    try:
        ds = ds_cache.get(datastore_id) if ds_cache is not None else None
        if ds is None:
            ds = await repo.get("datastores", org_id, datastore_id)
        if ds is None:
            return DEFAULT_DIALECT
        cfg: dict[str, Any] = dict(ds.get("config") or {})
        return dialect_for(cfg.get("connector_type") or cfg.get("type"))
    except Exception:  # noqa: BLE001 — fail-safe to the historical default.
        return DEFAULT_DIALECT


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
        board collector.  When provided, a datastore lookup is served
        from the cache without an extra repo round-trip.  The cache is
        populated by :func:`_prefetch_datastores`.

    Returns
    -------
    tuple[connector, owned, net_cleanup]
        *connector* — the resolved connector instance.
        *owned* — ``True`` when the connector was freshly created for this call
        and the caller is responsible for closing it.  ``False`` when the
        connector is a shared singleton (e.g. the demo connector) that must
        NOT be closed by the caller.
        *net_cleanup* — tears down any ephemeral VPC-bridge tunnel opened while
        resolving the datastore's network_mode.  Always callable (no-op when no
        tunnel was opened) and MUST be invoked by the caller in a ``finally``,
        AFTER closing the connector.
    """
    datastore_id = getattr(registered, "datastore_id", None)
    if not datastore_id:
        from app.routes.query import _get_demo_connector

        # Singleton — caller must not close it, and there is no tunnel to tear down.
        return _get_demo_connector(), False, (lambda: None)

    # Use the pre-fetched cache when available to avoid an extra repo round-trip
    # (see _prefetch_datastores — N widgets commonly share one datastore).
    ds = ds_cache.get(datastore_id) if ds_cache is not None else None

    # Everything else — connector_type resolution, per-tenant template fields,
    # SECRET INJECTION, VPC-bridge network resolution, per-ctype construction and
    # the capability-gated RLS refusal — is delegated to the one shared resolver.
    #
    # This function used to hand-roll a simplified version of all of that, and
    # the simplification was the bug: it never injected credentials, so every
    # server-side board export (pdf/csv/json/thumbnail) died with
    # "Access denied ... (using password: NO)" against any real datastore, while
    # the identical query succeeded through POST /query. Do not reintroduce a
    # local copy here — extend app/connectors/resolve.py instead.
    from app.connectors.resolve import resolve_datastore_connector  # noqa: PLC0415

    connector, _kind, net_cleanup = await resolve_datastore_connector(
        physical_plan, datastore_id, org_id, repo, ds=ds
    )
    return connector, True, net_cleanup


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

    This is called once at the start of :func:`collect_board_data`
    to eliminate N identical ``repo.get`` calls
    when N widgets share the same datastore.

    Concurrency is capped by two semaphores held simultaneously:

    1. **per-call semaphore** (``_PREFETCH_CONCURRENCY``, env
       ``NUBI_PREFETCH_CONCURRENCY``, default 10) — limits concurrent prefetch
       fetches *within* a single board load so one load cannot issue 500
       simultaneous DB calls on its own.
    2. **process-global semaphore** (``_GLOBAL_PREFETCH_CONCURRENCY``, env
       ``NUBI_PREFETCH_GLOBAL_CONCURRENCY``, default ``_PREFETCH_CONCURRENCY*2=20``)
       — limits the *total* concurrent prefetch fetches across ALL simultaneous
       board loads in the process.  Without this, N concurrent loads each
       create a fresh per-call semaphore and can still issue N × 10 simultaneous
       DB calls.

    Acquisition order: per-call semaphore first, then global semaphore.  This
    mirrors the widget path's order (per-call → global) and avoids starvation or
    deadlock (a load holding per-call slots cannot be indefinitely blocked from
    acquiring global slots when the global cap is sized >= per-call cap).
    """
    if not ds_ids:
        return {}

    per_call_sem = asyncio.Semaphore(_PREFETCH_CONCURRENCY)
    global_sem = _get_global_prefetch_sem()

    async def _fetch_one(ds_id: str) -> tuple[str, Any]:
        async with per_call_sem:
            async with global_sem:
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

    # Run widget queries concurrently bounded by two semaphores:
    # 1. per-call semaphore (_WIDGET_CONCURRENCY) — limits widgets within this
    #    single board load so one load cannot monopolise all slots.
    # 2. process-global semaphore (_GLOBAL_WIDGET_CONCURRENCY) — limits total
    #    concurrent widget execution across ALL simultaneous board loads
    #    so N concurrent loads do not fan out to N × _WIDGET_CONCURRENCY DB
    #    queries.  Both must be held before a widget query runs.
    per_call_sem = asyncio.Semaphore(_WIDGET_CONCURRENCY)
    global_sem = _get_global_widget_sem()

    async def _fetch_guarded(t: dict[str, str]) -> dict[str, Any]:
        async with per_call_sem:
            async with global_sem:
                return await _fetch_one(t)

    out: list[dict[str, Any]] = list(
        await asyncio.gather(*(_fetch_guarded(t) for t in targets))
    )

    return out
