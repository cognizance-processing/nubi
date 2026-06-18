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

from typing import Any

from app.errors import AppError
from app.queries.registry import ensure_persisted_query, get_query_registry
from app.repos.provider import Repo


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

    connector = await _resolve_connector(registered, org_id, repo, physical_plan)

    arrow_table = connector.execute(physical_plan)
    columns = list(arrow_table.schema.names)
    rows: list[list[Any]] = []
    for record in arrow_table.to_pylist():
        rows.append([record.get(c) for c in columns])
    return columns, rows


async def _resolve_connector(registered: Any, org_id: str, repo: Repo, physical_plan: Any) -> Any:
    """Pick the connector for a registered query (datastore-bound or demo).

    Pragmatic subset of the ``POST /query`` connector path: it covers the demo
    connector and a directly-configured duckdb/postgres/http_json datastore.
    Secret injection and network bridges are intentionally out of scope here —
    if a datastore needs them, the connector construction will raise and the
    caller skips that widget (best-effort collection).
    """
    datastore_id = getattr(registered, "datastore_id", None)
    if not datastore_id:
        from app.routes.query import _get_demo_connector

        return _get_demo_connector()

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

            conn = duckdb.connect(database=db_path, read_only=True)
            return factory(conn)
        return factory()
    if ctype == "postgres":
        dsn = cfg.get("dsn")
        if dsn is None:
            host = cfg.get("host", "localhost")
            port = cfg.get("port", 5432)
            dbname = cfg.get("dbname") or cfg.get("database") or "postgres"
            user = cfg.get("user") or cfg.get("username") or "postgres"
            password = cfg.get("password", "")
            dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
        return factory(dsn)

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
    return connector


# ---------------------------------------------------------------------------
# Public collector
# ---------------------------------------------------------------------------


async def collect_board_data(
    board_id: str,
    org_id: str,
    claims: dict[str, Any],
    repo: Repo,
    only_query_id: str | None = None,
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
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)

    spec = spec_from_board(board)
    targets = widget_query_targets(spec, only_query_id)

    # RLS comes from the verified token's policies claim only.
    policies: dict[str, Any] = dict((claims or {}).get("policies") or {})

    out: list[dict[str, Any]] = []
    for t in targets:
        entry: dict[str, Any] = {"widget_id": t["widget_id"], "query_id": t["query_id"]}
        try:
            columns, rows = await run_query_rows(t["query_id"], org_id, repo, policies)
            entry["columns"] = columns
            entry["rows"] = rows
        except AppError as exc:
            entry["error"] = exc.code
        except Exception as exc:  # noqa: BLE001 — best-effort collection
            entry["error"] = f"export_failed: {exc.__class__.__name__}"
        out.append(entry)

    return out
