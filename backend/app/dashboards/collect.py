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
from typing import Any

from app.errors import AppError
from app.queries.registry import ensure_persisted_query, get_query_registry
from app.repos.provider import Repo

logger = logging.getLogger(__name__)

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
) -> tuple[list[str], list[list[Any]]]:
    """Run a registered query and return ``(columns, rows)``.

    Reuses the same primitives as ``POST /query``:
      * the query registry resolves ``query_id`` → canonical SQL (the browser
        never supplies SQL here — only the registered id is honoured);
      * the planner injects RLS predicates from *policies* (AST-level, never
        string-concatenated);
      * a datastore-bound query executes against its datastore via the
        connector registry, otherwise the built-in demo DuckDB connector runs.

    Raises ``AppError`` with a descriptive code on any failure so the caller can
    decide whether to skip the widget or surface the error.
    """
    registry = get_query_registry()
    registered = registry.get(query_id) or await ensure_persisted_query(query_id)
    if registered is None:
        raise AppError("query_not_registered", f"No registered query for id={query_id!r}.", 404)

    # Resolve declared named params to their defaults (collection has no live
    # filter state), turning {{name}} placeholders into positional $N binds.
    sql = registered.sql
    params: list[Any] = []
    if registered.params:
        from app.connectors.planner import resolve_named_params

        resolved = {p.name: (p.default if p.default is not None else None) for p in registered.params}
        sql, params = resolve_named_params(sql, resolved)

    from app.connectors import plan as planner_plan

    physical_plan = planner_plan(sql=sql, claims={"policies": policies}, params=params)

    connector, connector_owned = await _resolve_connector(registered, org_id, repo, physical_plan)

    try:
        arrow_table = await asyncio.to_thread(connector.execute, physical_plan)
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
    registered: Any, org_id: str, repo: Repo, physical_plan: Any
) -> tuple[Any, bool]:
    """Pick the connector for a registered query (datastore-bound or demo).

    Pragmatic subset of the ``POST /query`` connector path: it covers the demo
    connector and a directly-configured duckdb/postgres/http_json datastore.
    Secret injection and network bridges are intentionally out of scope here —
    if a datastore needs them, the connector construction will raise and the
    caller skips that widget (best-effort collection).

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

    # RLS comes from the verified token's policies claim only.
    policies: dict[str, Any] = dict((claims or {}).get("policies") or {})

    async def _fetch_one(t: dict[str, str]) -> dict[str, Any]:
        entry: dict[str, Any] = {"widget_id": t["widget_id"], "query_id": t["query_id"]}
        try:
            columns, rows = await run_query_rows(t["query_id"], org_id, repo, policies)
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

    async def _fetch_binding(el_id: str, binding: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"el_id": el_id}
        kind = binding.get("kind")
        try:
            if kind == "query":
                query_id = binding.get("query_id") or ""
                columns, rows = await run_query_rows(query_id, org_id, repo, policies)
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
                        source_query_id, org_id, repo, policies
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

                # [LOW path-traversal] Validate that binding_path is a safe
                # relative path before joining it onto the connector base URL.
                # Reject: URL schemes (contains "://"), protocol-relative URLs
                # ("//"), and paths containing ".." segments that could escape
                # the base URL's path hierarchy.
                if binding_path:
                    _path_norm = binding_path.strip()
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
                    result_table = await asyncio.to_thread(connector.execute, api_plan)
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
