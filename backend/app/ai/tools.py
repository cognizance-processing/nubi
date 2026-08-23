"""Tool registry for the Nubi AI agent (M21-A).

Each tool is a ``ToolDef`` dataclass with:
  - ``name``        — stable identifier used in agent messages.
  - ``json_schema`` — JSON Schema dict describing the tool's input parameters.
  - ``fn``          — the callable that runs the tool.

Every tool callable accepts a ``claims`` keyword argument so it can enforce
the caller's auth scope.  Tools that touch data (``run_query``) NEVER exceed
the caller's scope — they pass claims through to the planner, which injects
RLS predicates.

Public API
----------
get_tool(name) -> ToolDef | None
    Return the registered ToolDef for *name*, or None if unknown.

all_tools() -> list[ToolDef]
    Return all registered tools.

tool_schemas() -> list[dict]
    Return the JSON Schema block for every tool (for injecting into LLM prompts).

execute_tool(name, arguments, claims) -> dict
    Validate *arguments* against the tool's schema and call its ``fn``.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# Sync→async bridge (mirror of app/ai/flow_tools._run_sync)
# ---------------------------------------------------------------------------
#
# The AI agent loop (app/ai/agent.py → app/ai/tools.execute_tool) is fully
# SYNCHRONOUS, but some governance checks (e.g. the org-ownership gate on a
# metric slug) are ``async def`` and touch the shared asyncpg pool.  ``_run_sync``
# runs such a coroutine to completion from sync code:
#  - If the shared DB pool's owning loop is known and alive (the normal FastAPI
#    case — ``execute_tool`` runs on a worker thread via ``asyncio.to_thread``),
#    marshal the coroutine onto THAT loop via ``run_coroutine_threadsafe``.
#    asyncpg Pools/Connections are bound to the loop they were created on;
#    awaiting one from a different, throwaway loop corrupts the connection it
#    acquires for every LATER caller, surfacing much later as
#    ``another operation is in progress`` / ``ConnectionDoesNotExistError`` —
#    this bit both ``run_query``'s new datastore-connector path and the
#    pre-existing ``metric_belongs_to_org`` gate, which silently failed closed
#    on the corruption instead of raising it.
#  - Otherwise (pool not initialised — e.g. unit tests with no DB), fall back
#    to running the coroutine in its own loop: on a fresh worker thread if a
#    loop is already running in this thread, else via ``asyncio.run`` directly.


def _run_sync(coro: Awaitable[_T]) -> _T:
    """Run *coro* to completion from synchronous code and return its result."""
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    try:
        from app.db import get_pool_loop  # noqa: PLC0415

        pool_loop = get_pool_loop()
    except Exception:  # noqa: BLE001 — db module unavailable in some test doubles
        pool_loop = None

    if pool_loop is not None and pool_loop.is_running():
        # SAFETY: run_coroutine_threadsafe()'s future.result() blocks the
        # calling thread until the loop processes the callback — which
        # deadlocks forever if the calling thread IS the pool loop's own
        # thread (nothing else can ever advance that loop to run it). This
        # bridge exists for sync code on a DIFFERENT thread (the normal case:
        # execute_tool() invoked via asyncio.to_thread from the FastAPI
        # handler) — fail fast with a clear diagnostic instead of hanging.
        if current_loop is pool_loop:
            raise RuntimeError(
                "_run_sync() was called synchronously from the DB pool's own "
                "event loop thread — this would deadlock. Callers already "
                "running on that loop must `await` the coroutine directly "
                "instead of routing it through _run_sync()."
            )
        future = asyncio.run_coroutine_threadsafe(coro, pool_loop)  # type: ignore[arg-type]
        return future.result()

    if current_loop is not None and current_loop.is_running():
        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)  # type: ignore[arg-type]
            return future.result()

    return asyncio.run(coro)  # type: ignore[arg-type]


def _json_safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce a connector's ``to_pylist()`` output to JSON-serialisable values.

    ``arrow_table.to_pylist()`` converts Arrow scalars to native Python
    objects, but "native" still includes types ``json.dumps`` (used by the
    MCP JSON-RPC transport) chokes on — most commonly ``decimal.Decimal``
    from a MySQL SUM/ROUND on a DECIMAL column (a `/query` REST caller never
    hits this: that path streams Arrow IPC bytes and the client's own Arrow
    library decodes Decimal natively). ``date``/``datetime``/``time`` are
    included for the same reason. Recurses into list/dict values so a nested
    struct or array column is covered too.
    """
    import datetime as _dt
    from decimal import Decimal as _Decimal

    def _coerce(value: Any) -> Any:
        if isinstance(value, _Decimal):
            return float(value)
        if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: _coerce(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_coerce(v) for v in value]
        return value

    return [{k: _coerce(v) for k, v in row.items()} for row in rows]


# ---------------------------------------------------------------------------
# ToolDef
# ---------------------------------------------------------------------------


@dataclass
class ToolDef:
    """A single registered tool.

    Attributes
    ----------
    name:
        Stable, underscore-separated identifier (e.g. ``"run_query"``).
    description:
        One-sentence human / LLM description.
    json_schema:
        JSON Schema ``object`` describing the tool's ``arguments``.  MUST be a
        valid JSON Schema ``{"type": "object", "properties": {...}, ...}``.
    fn:
        Callable ``fn(claims, **kwargs) -> dict`` that executes the tool and
        returns a JSON-serialisable dict result.
    """

    name: str
    description: str
    json_schema: dict[str, Any]
    fn: Callable[..., dict[str, Any]]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


#: Default cap on queries returned by a narrowed get_schema call.
_GET_SCHEMA_DEFAULT_LIMIT = 30
#: Hard response-size ceiling (chars of the JSON body) — narrowed results can
#: still be large if a table_pattern matches many queries with big
#: output_schema/params lists; this is the last-resort backstop.
_GET_SCHEMA_MAX_CHARS = 60_000


def _tool_get_schema(
    claims: dict[str, Any],
    table_pattern: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return the catalog schema (tables + columns) visible to the caller.

    The catalog is built from the live query registry and the query graph.
    No filtering by claims is applied here — the catalog only exposes
    registered (already allowlisted) metadata, not raw data.

    With no *table_pattern*, returns a COMPACT summary — table names and
    column counts only, no per-query detail — so an agent can list the
    catalog without spending its whole context on one call (the full catalog
    on a busy org can be several hundred KB: every registered query's
    params/output_schema, for every query in the process, not just the ones
    relevant to the question). Pass *table_pattern* (a case-insensitive
    substring of the table name) to get full columns for matching tables
    plus the queries that touch them, capped at *limit* (default 30) with a
    truncation notice if there are more.
    """
    from app.ai.grounding import build_catalog  # noqa: PLC0415

    catalog = build_catalog()
    tables: dict[str, list[str]] = catalog["tables"]
    queries: list[dict[str, Any]] = catalog["queries"]

    if not table_pattern:
        return {
            "tables": {name: len(cols) for name, cols in tables.items()},
            "table_count": len(tables),
            "query_count": len(queries),
            "note": (
                "Compact summary (table name -> column count). Call again "
                "with table_pattern set to a table name (or a substring of "
                "one) to get its full columns and the queries that use it."
            ),
        }

    needle = table_pattern.strip().lower()
    matched_tables = {name: cols for name, cols in tables.items() if needle in name.lower()}
    matched_names = set(matched_tables)
    matched_queries = [q for q in queries if matched_names & set(q.get("tables") or [])]

    cap = limit if isinstance(limit, int) and limit > 0 else _GET_SCHEMA_DEFAULT_LIMIT
    result: dict[str, Any] = {
        "tables": matched_tables,
        "queries": matched_queries[:cap],
        "matched_query_count": len(matched_queries),
    }
    if len(matched_queries) > cap:
        result["truncated"] = True
        result["note"] = (
            f"Showing {cap} of {len(matched_queries)} matching queries. "
            "Narrow table_pattern further, or pass a higher limit."
        )

    # Last-resort size backstop: a narrow-but-verbose match (many queries with
    # large output_schema/params) can still blow the budget. Drop queries
    # until the body fits rather than returning something too big to read.
    import json  # noqa: PLC0415

    while len(json.dumps(result)) > _GET_SCHEMA_MAX_CHARS and result["queries"]:
        result["queries"] = result["queries"][: max(1, len(result["queries"]) // 2)]
        result["truncated"] = True
        result["note"] = (
            f"Response was too large and was cut down to {len(result['queries'])} "
            f"of {len(matched_queries)} matching queries. Narrow table_pattern further."
        )

    return result


def _tool_list_queries(claims: dict[str, Any]) -> dict[str, Any]:
    """Return all registered queries (id, name, sql summary, params)."""
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    registry = get_query_registry()
    queries = []
    for rq in registry.all():
        queries.append(
            {
                "id": rq.id,
                "name": rq.name,
                "required_scope": rq.required_scope,
                "params": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "required": p.required,
                        "default": p.default,
                    }
                    for p in rq.params
                ],
            }
        )
    return {"queries": queries}


def _tool_generate_sql(
    question: str,
    claims: dict[str, Any],
    datastore_id: str | None = None,
) -> dict[str, Any]:
    """Generate a grounded SQL SELECT for a natural-language *question*.

    Reuses ``app.ai.sql.generate_sql`` (M18).  With NullProvider (the default
    when no API key is set) the result is fully deterministic.

    Returns ``{sql, valid, issues}``.
    """
    from app.ai.grounding import build_catalog  # noqa: PLC0415
    from app.ai.provider import get_provider  # noqa: PLC0415
    from app.ai.sql import generate_sql  # noqa: PLC0415

    catalog = build_catalog()
    provider = get_provider()
    return generate_sql(
        question=question,
        catalog=catalog,
        provider=provider,
        datastore_id=datastore_id,
    )


def _tool_create_query(
    sql: str,
    claims: dict[str, Any],
    id: str | None = None,
    name: str | None = None,
    params: list[dict[str, Any]] | None = None,
    datastore_id: str | None = None,
) -> dict[str, Any]:
    """Register a query in the query registry, mirroring ``POST
    /query/registry``'s id/persistence contract (previously this tool
    reimplemented only the in-memory half of that contract, and never
    accepted a datastore binding at all — every MCP-registered query silently
    ran against the demo dataset no matter what real tables the SQL named).

    Parameters
    ----------
    sql:
        The SELECT SQL string to register.
    id:
        Optional stable, URL-safe identifier. Row primary keys in the
        ``queries`` table are real UUIDs, so a caller-chosen slug id can only
        ever be a registry-only (in-memory, this-process-lifetime)
        registration — it is NOT persisted, matching ``POST
        /query/registry``'s documented behaviour for a non-UUID explicit id.
        Omit *id* to get BOTH a generated UUID id AND real persistence (the
        query survives a backend restart and can be referenced by a saved
        dashboard widget) — best-effort: falls back to the same
        registry-only registration if persistence is unavailable.
    params:
        Optional list of ``{name, type, required?, default?}`` param descriptors.
    datastore_id:
        Optional datastore/connector id this query runs against. Omitted =
        the built-in demo dataset. Required to target a real (BYO) connector.

    Returns
    -------
    dict
        ``{id, name, registered: True}``
    """
    from app.queries.registry import QueryParam, get_query_registry  # noqa: PLC0415

    param_objs: list[QueryParam] = []
    if params:
        for p in params:
            param_objs.append(
                QueryParam(
                    name=p["name"],
                    type=p.get("type", "text"),  # type: ignore[arg-type]
                    default=p.get("default"),
                    required=bool(p.get("required", False)),
                    options_query_id=p.get("options_query_id"),
                )
            )

    org_id = claims.get("org")
    explicit_id = (id or "").strip() or None
    display_name = (name or "").strip() or (explicit_id or "query").replace("_", " ").replace("-", " ").strip().title()

    registry_id = explicit_id
    if registry_id is None and org_id:
        try:
            from app.repos.provider import get_repo  # noqa: PLC0415
            from app.routes._org import resolve_org_default_project_id  # noqa: PLC0415

            repo = get_repo()
            project_id = _run_sync(resolve_org_default_project_id(str(org_id)))
            config = {
                "sql": sql,
                "name": display_name,
                "datastore_id": datastore_id,
                "params": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "default": p.default,
                        "required": p.required,
                        "options_query_id": p.options_query_id,
                    }
                    for p in param_objs
                ],
            }
            row = _run_sync(
                repo.create(
                    "queries",
                    str(org_id),
                    str(claims.get("sub") or ""),
                    display_name,
                    config,
                    project_id=project_id,
                )
            )
            registry_id = row["id"]
        except Exception:  # noqa: BLE001 — best-effort, same contract as POST /query/registry
            registry_id = None

    if registry_id is None:
        # Persistence unavailable (or no org) — fall back to a name-derived
        # slug, matching the legacy memory-only registration.
        import re as _re  # noqa: PLC0415

        registry_id = _re.sub(r"[^a-z0-9_]", "", display_name.lower().replace(" ", "_")) or "query"

    registry = get_query_registry()
    rq = registry.register(
        id=registry_id,
        sql=sql,
        name=display_name,
        params=param_objs if param_objs else None,
        datastore_id=datastore_id,
        owner_org_id=str(org_id) if org_id else None,
    )
    return {"id": rq.id, "name": rq.name, "registered": True}


def _resolve_query_row(query_id: str, claims: dict[str, Any]) -> tuple[dict, str]:
    """Fetch a persisted query row, org-scoped. Raises AppError when absent."""
    from app.errors import AppError  # noqa: PLC0415
    from app.repos.provider import get_repo  # noqa: PLC0415

    org_id = claims.get("org")
    if not org_id:
        raise AppError("forbidden", "No organisation resolved for this caller.", 403)
    row = _run_sync(get_repo().get("queries", str(org_id), query_id))
    if row is None:
        raise AppError("query_not_found", f"No query found for id={query_id!r}.", 404)
    return row, str(org_id)


def _tool_list_filterable_columns(query_id: str, claims: dict[str, Any]) -> dict[str, Any]:
    """Columns of *query_id* a dashboard filter could attach to.

    Includes columns that live only inside subqueries — a query that rolls up
    ``region`` before its output still has ``region`` available deep down,
    which is precisely where a filter has to go for it to mean anything.
    """
    from app.queries.parameterize import filterable_columns  # noqa: PLC0415

    row, _ = _resolve_query_row(query_id, claims)
    cfg = row.get("config") or {}
    dialect = _dialect_for_datastore(cfg.get("datastore_id"), claims)
    return {"columns": filterable_columns(cfg.get("sql") or "", dialect=dialect)}


def _dialect_for_datastore(datastore_id: str | None, claims: dict[str, Any]) -> str:
    """Best-effort sqlglot dialect for a datastore id (defaults to mysql).

    The rewrite only needs the dialect to parse and to render the predicate's
    column expression, so an imperfect guess degrades to a refusal (unparsed
    SQL) rather than a bad rewrite.
    """
    from app.routes.connectors import DEMO_CONNECTOR_ID  # noqa: PLC0415

    # No datastore, or the demo sentinel → the built-in DuckDB dataset.
    if not datastore_id or datastore_id == DEMO_CONNECTOR_ID:
        return "duckdb"
    try:
        from app.connectors.dialects import dialect_for  # noqa: PLC0415
        from app.repos.provider import get_repo  # noqa: PLC0415

        org_id = claims.get("org")
        if not org_id:
            return "mysql"
        ds = _run_sync(get_repo().get("datastores", str(org_id), datastore_id))
        cfg = (ds or {}).get("config") or {}
        return dialect_for(cfg.get("connector_type") or cfg.get("type") or "")
    except Exception:  # noqa: BLE001
        return "mysql"


def _tool_add_filter_param(
    query_id: str,
    param: str,
    column: str,
    claims: dict[str, Any],
    subtype: str = "multiselect",
    apply: bool = False,
) -> dict[str, Any]:
    """Make a registered query filterable by *param* on *column*.

    Mirrors ``POST /queries/{id}/parameterize`` — same rewrite, same
    verification gate — so the MCP path and the dashboard editor cannot drift
    into different behaviour. The rewrite is executed with the filter unset
    and compared against the original result; anything other than an exact
    match is refused rather than saved, because the failure this guards
    against is a query that still runs but quietly returns different numbers.
    """
    from app.connectors.planner import plan as _plan, resolve_named_params  # noqa: PLC0415
    from app.queries.parameterize import parameterize_sql  # noqa: PLC0415
    from app.repos.provider import get_repo  # noqa: PLC0415

    from app.routes.connectors import DEMO_CONNECTOR_ID  # noqa: PLC0415

    row, org_id = _resolve_query_row(query_id, claims)
    cfg = dict(row.get("config") or {})
    original_sql = cfg.get("sql") or ""
    existing = list(cfg.get("params") or [])
    # See the REST parameterize route: the demo sentinel must collapse to None
    # before any code treats it as a UUID to look up.
    _raw_ds = cfg.get("datastore_id")
    datastore_id = None if _raw_ds == DEMO_CONNECTOR_ID else _raw_ds

    if any((p or {}).get("name") == param for p in existing):
        return {"ok": False, "applied": False, "reason": f"This query already declares a {param!r} parameter."}

    dialect = _dialect_for_datastore(datastore_id, claims)
    result = parameterize_sql(original_sql, param=param, column=column, dialect=dialect, subtype=subtype)
    if not result.ok or not result.sql:
        return {"ok": False, "applied": False, "verified": False, "reason": result.reason}

    def _exec(sql_text: str, named: dict[str, Any]):
        rendered, positional = resolve_named_params(sql_text, {**named, "vars": {}})
        physical = _plan(rendered, claims=claims, params=positional)
        if datastore_id:
            from app.connectors.resolve import resolve_datastore_connector  # noqa: PLC0415

            connector, _kind, cleanup = _run_sync(
                resolve_datastore_connector(physical, datastore_id, org_id, get_repo())
            )
            try:
                return connector.execute(physical)
            finally:
                cleanup()
        from app.routes.query import _get_demo_connector  # noqa: PLC0415

        return _get_demo_connector().execute(physical)

    unset: Any = [] if subtype in ("multiselect", "list") else None
    try:
        before = _exec(original_sql, {})
        after = _exec(result.sql, {param: unset})
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "applied": False,
            "verified": False,
            "sql": result.sql,
            "reason": f"The modified query could not be executed, so it was not saved: {exc}",
        }

    from app.routes.query import _same_result  # noqa: PLC0415

    if not _same_result(before, after):
        return {
            "ok": False,
            "applied": False,
            "verified": False,
            "sql": result.sql,
            "reason": (
                "Adding this filter changed the query's results even with the filter "
                "unset, so it was NOT saved."
            ),
        }

    if not apply:
        return {
            "ok": True,
            "verified": True,
            "applied": False,
            "sql": result.sql,
            "column_expr": result.column_expr,
        }

    cfg["sql"] = result.sql
    cfg["params"] = existing + [
        {
            "name": param,
            "type": subtype,
            "default": [] if subtype in ("multiselect", "list") else None,
            "required": False,
            "options_query_id": None,
        }
    ]
    _run_sync(get_repo().update("queries", org_id, query_id, {"config": cfg}))
    from app.queries.registry import (  # noqa: PLC0415
        ensure_persisted_query,
        get_query_registry,
    )

    # Evict AND reload — see the REST parameterize route. A bare unregister
    # drops the query out of GET /query/registry (which lists the in-memory
    # registry), and the dashboard editor builds its param index from that
    # listing, so every other widget on this query would report "no query
    # bound yet" until something re-executed it.
    get_query_registry().unregister(query_id)
    try:
        _run_sync(ensure_persisted_query(query_id, org_id))
    except Exception:  # noqa: BLE001 — the row is saved; a miss self-heals on next use
        pass
    return {
        "ok": True,
        "verified": True,
        "applied": True,
        "sql": result.sql,
        "column_expr": result.column_expr,
    }


def _tool_run_query(
    claims: dict[str, Any],
    query_id: str | None = None,
    sql: str | None = None,
    named_params: dict[str, Any] | None = None,
    datastore_id: str | None = None,
) -> dict[str, Any]:
    """Execute a query and return the result rows.

    Either *query_id* (a registered query) or *sql* (an ad-hoc SELECT) must be
    provided.  The caller's *claims* are passed to the planner — RLS policies
    in ``claims["policies"]`` are injected as AST-level WHERE predicates.  This
    ensures the tool NEVER returns data outside the caller's scope.

    The EFFECTIVE datastore is resolved exactly like ``POST /query``
    (``app.routes.query._resolve_effective_datastore_id``): an explicit
    *datastore_id* argument wins, else a registered query's own binding is
    used. Only when neither is present does this fall back to the built-in
    demo DuckDB dataset — previously this tool ALWAYS used the demo dataset,
    silently ignoring every real (BYO-connector) datastore, including a
    registered query's own ``datastore_id`` binding.

    Parameters
    ----------
    query_id:
        Id of a registered query (takes precedence over *sql*).
    sql:
        Ad-hoc SELECT SQL (used only if *query_id* is None).
    named_params:
        Named parameter values for ``{{name}}`` placeholders in registry SQL.
    datastore_id:
        Optional explicit datastore id, for ad-hoc SQL or to override a
        registered query's default binding (mirrors ``QueryIn.datastore_id``).
    claims:
        Caller's auth claims (RLS enforced via the planner).

    Returns
    -------
    dict
        ``{rows: list[dict], row_count: int, columns: list[str]}``
    """
    from app.connectors.planner import plan, resolve_named_params  # noqa: PLC0415
    from app.errors import AppError  # noqa: PLC0415
    from app.queries.registry import resolve_registered_query  # noqa: PLC0415

    # ── Resolve SQL ──────────────────────────────────────────────────────────
    resolved_sql: str
    positional_params: list[Any] = []
    registered_datastore_id: str | None = None

    if query_id is not None:
        # Use the org-scoped choke point, NOT a bare registry.get(): the
        # in-memory registry is populated at startup and evicted on every
        # query update, so a direct lookup misses any query created since
        # boot or modified since it was last read (e.g. by add_filter_param,
        # which unregisters to force a reload) and reports it as
        # "not registered". resolve_registered_query lazily reloads the row
        # from the DB and re-checks org ownership on a cache hit.
        rq = _run_sync(resolve_registered_query(query_id, claims.get("org")))
        if rq is None:
            raise AppError("query_not_found", f"No registered query with id {query_id!r}.", 404)
        resolved_sql = rq.sql
        registered_datastore_id = rq.datastore_id
        # Resolve named params → positional if the query declares ANY params
        # — not gated on named_params being non-empty. A query's {% if %}
        # guards need rendering even when every param is left at its default
        # (e.g. an unfiltered "give me everything" call, or a dashboard
        # widget's first render before any filter is touched) — otherwise
        # the literal Jinja text reaches the SQL parser and fails outright.
        # Mirrors POST /query's _resolve_request_plan, which renders whenever
        # `registered.params` is non-empty, never conditioned on the caller
        # having supplied named_params at all.
        if rq.params:
            # Build the resolved dict (apply defaults for missing optional params).
            _named_params = named_params or {}
            resolved: dict[str, Any] = {}
            for p in rq.params:
                if p.name in _named_params:
                    resolved[p.name] = _named_params[p.name]
                elif p.default is not None:
                    resolved[p.name] = p.default
                elif p.required:
                    raise AppError(
                        "missing_required_param",
                        f"Required param {p.name!r} was not supplied.",
                        400,
                    )
            resolved_sql, positional_params = resolve_named_params(resolved_sql, resolved)
    elif sql is not None:
        # SECURITY: ad-hoc SQL requires author:sql scope — same gate as query.py.
        # Embed tokens and restricted first-party tokens (kind!="access" or missing
        # author:sql) must NOT be able to execute arbitrary SQL via the tool path.
        from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL  # noqa: PLC0415

        tool_scopes: list[str] = claims.get("scope") or []
        tool_kind: str = claims.get("kind", "access")
        if tool_kind != "access" or not has_scope(tool_scopes, SCOPE_AUTHOR_SQL):
            raise AppError(
                "insufficient_scope",
                "Token does not carry the required scope: author:sql — "
                "raw SQL execution via the AI tool path is not permitted without this scope.",
                403,
            )
        resolved_sql = sql
    else:
        raise AppError("invalid_tool_input", "Either query_id or sql must be provided.", 400)

    # ── Plan ─────────────────────────────────────────────────────────────────
    physical_plan = plan(resolved_sql, claims=claims, params=positional_params)

    # ── Resolve the effective datastore (explicit arg wins, else the query's
    #    own binding) and build the matching connector — same resolution order
    #    as POST /query's _resolve_effective_datastore_id, INCLUDING the
    #    __demo__ sentinel collapsing to None. Without this collapse, an
    #    explicit datastore_id="__demo__" (the id every "Demo data" connector
    #    listing returns) fell through to resolve_datastore_connector, which
    #    tried to cast it to a UUID for a DB lookup and 500'd.
    from app.routes.connectors import DEMO_CONNECTOR_ID  # noqa: PLC0415

    effective_datastore_id = datastore_id or registered_datastore_id
    if effective_datastore_id == DEMO_CONNECTOR_ID:
        effective_datastore_id = None

    if effective_datastore_id is not None:
        from app.connectors.resolve import resolve_datastore_connector  # noqa: PLC0415
        from app.repos.provider import get_repo  # noqa: PLC0415

        org_id = claims.get("org")
        repo = get_repo()
        connector, _conn_kind, net_cleanup = _run_sync(
            resolve_datastore_connector(physical_plan, effective_datastore_id, org_id, repo)
        )
        try:
            arrow_table = connector.execute(physical_plan)
        finally:
            net_cleanup()
    else:
        # The SAME hardened, parquet-backed demo connector POST /query uses
        # (17 demo tables + the legacy 5-row `demo` table, external file
        # access disabled) — not a bare in-memory DuckDB seeded with only
        # `demo`, which would 500 on every other demo table and wouldn't be
        # hardened against read_csv_auto('/etc/passwd') either.
        from app.routes.query import _get_demo_connector  # noqa: PLC0415

        connector = _get_demo_connector()
        arrow_table = connector.execute(physical_plan)

    # Convert to JSON-serialisable rows.
    columns = arrow_table.schema.names
    rows = _json_safe_rows(arrow_table.to_pylist())
    return {"rows": rows, "row_count": len(rows), "columns": columns}


def _seed_demo_table(connector: Any) -> None:
    """Seed the ``demo`` table into a fresh DuckDB connector.

    This mirrors the demo seeding done in the query route so tools that
    reference ``demo_all`` / ``demo_active`` work in the agent context.
    """
    try:
        import pyarrow as pa  # noqa: PLC0415

        demo = pa.table(
            {
                "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
                "name": pa.array(["alpha", "beta", "gamma", "delta", "epsilon"]),
                "active": pa.array([True, True, False, True, False]),
                "value": pa.array([10.0, 20.0, 30.0, 40.0, 50.0], type=pa.float64()),
            }
        )
        connector.register({"demo": demo})
    except Exception:  # noqa: BLE001
        pass  # If seeding fails, the query will just fail naturally.


def _tool_list_metrics(claims: dict[str, Any]) -> dict[str, Any]:
    """Return all registered governed metrics.

    Each entry mirrors the metrics list-view shape: ``id``, ``name``,
    ``measure`` ({name, agg, expr, type, format}), ``dimensions`` (allowed
    grouping columns), ``time_grains`` (allowed bucket grains), and
    ``description``.  These are GOVERNED definitions — the agent must answer
    metric questions from these rather than hallucinating SQL.

    Returns ``{metrics: list[dict]}``.
    """
    from app.metrics.registry import get_metric_registry  # noqa: PLC0415

    registry = get_metric_registry()
    metrics: list[dict[str, Any]] = []
    for m in registry.all():
        td = m.time_dimension
        metrics.append(
            {
                "id": m.id,
                "name": m.name,
                "measure": {
                    "name": m.measure.name,
                    "agg": m.measure.agg,
                    "expr": m.measure.expr,
                    "type": m.measure.type,
                    "format": m.measure.format,
                },
                "dimensions": [d.name for d in m.dimensions],
                "time_grains": list(td.grains) if td is not None else [],
                "description": m.description,
            }
        )
    return {"metrics": metrics}


def _tool_query_metric(
    claims: dict[str, Any],
    metric_id: str,
    dimensions: list[str] | None = None,
    time_grain: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Execute a GOVERNED metric query and return its rows.

    Builds a :class:`MetricQuery` via ``MetricQuery.from_dict``, resolves the
    metric from the registry, compiles it to ``(sql, params)`` via
    ``compile_metric``, then executes it through the SAME in-process plan +
    DuckDB-execute path that :func:`_tool_run_query` uses — so the caller's
    *claims* are passed to the planner and RLS predicates are injected at the
    AST level.  The metric layer never lets a caller widen scope (only allowed
    dimensions / grains / filter fields compile) and RLS narrows rows further.

    On an unknown metric or any ``MetricError`` (unknown dimension, bad grain,
    bad filter field/op/value, …) this returns a structured
    ``{error: {code, message}}`` instead of raising past the tool boundary.

    Returns ``{columns, rows, row_count}`` on success.
    """
    from app.connectors.duckdb_conn import DuckDBConnector  # noqa: PLC0415
    from app.connectors.planner import plan, resolve_named_params  # noqa: PLC0415
    from app.metrics.compile import compile_metric  # noqa: PLC0415
    from app.metrics.models import MetricError, MetricQuery  # noqa: PLC0415
    from app.metrics.registry import (  # noqa: PLC0415
        SEED_METRIC_IDS,
        get_metric_registry,
        metric_belongs_to_org,
    )

    # ── Resolve the governed metric definition (unknown → structured error) ──
    registry = get_metric_registry()
    metric = registry.get(metric_id)
    if metric is None:
        return {
            "error": {
                "code": "metric_not_found",
                "message": f"No metric found for id={metric_id!r}.",
            }
        }

    # ── TENANT ISOLATION (SEC): the metric registry is a process-GLOBAL ──────
    #    singleton, so a ``registry.get(slug)`` hit may be a metric loaded by a
    #    DIFFERENT org on this same process. Before handing back its definition
    #    (or running it), confirm the slug is owned by the CALLER's org. In-code
    #    seeds (``SEED_METRIC_IDS``) belong to no tenant and resolve for everyone.
    #    Fail closed: a metric we can't prove the caller owns → metric_not_found
    #    (NOT another org's definition / data). RLS on claims["policies"] still
    #    narrows rows below; this is an ADDITIONAL gate on the definition itself.
    org_id = claims.get("org")
    if metric_id not in SEED_METRIC_IDS:
        if not org_id or not _run_sync(metric_belongs_to_org(metric_id, str(org_id))):
            return {
                "error": {
                    "code": "metric_not_found",
                    "message": f"No metric found for id={metric_id!r}.",
                }
            }

    # ── Build the MetricQuery + compile to (sql, params) — governance here ──
    # CORRECTNESS (RLS top-N): derive policy_cols from claims so the layered
    # __base CTE hoists the policy column into its GROUP BY/SELECT before the
    # planner injects WHERE policy_col=val on the outer query.  Without this,
    # a metric with derived_measures (layered path) and rls_keys=[] would emit
    # a __base that omits the policy column — causing a column-not-found at
    # runtime.  Mirrors routes/metrics.py (~740-742).
    policy_cols = tuple((claims.get("policies") or {}).keys())
    try:
        mq = MetricQuery.from_dict(
            {
                "metric_id": metric_id,
                "dimensions": dimensions or [],
                "time_grain": time_grain,
                "filters": filters or [],
                "limit": limit,
            }
        )
        sql, named_params = compile_metric(metric, mq, policy_cols=policy_cols)
    except MetricError as exc:
        return {"error": {"code": exc.code, "message": exc.message}}

    # ── Resolve {{name}} placeholders → positional params (planner helper) ──
    effective_sql, positional_params = resolve_named_params(sql, named_params)

    # ── Plan + execute — the SAME path run_query uses, so the caller's claims
    #    drive RLS predicate injection in the planner, AND the metric's own
    #    ``datastore_id`` binding (like a registered query's) routes to its
    #    real datastore instead of always landing on the demo dataset.
    physical_plan = plan(effective_sql, claims=claims, params=positional_params)

    if metric.datastore_id is not None:
        from app.connectors.resolve import resolve_datastore_connector  # noqa: PLC0415
        from app.repos.provider import get_repo  # noqa: PLC0415

        repo = get_repo()
        connector, _conn_kind, net_cleanup = _run_sync(
            resolve_datastore_connector(physical_plan, metric.datastore_id, org_id, repo)
        )
        try:
            arrow_table = connector.execute(physical_plan)
        finally:
            net_cleanup()
    else:
        connector = DuckDBConnector()
        _seed_demo_table(connector)
        arrow_table = connector.execute(physical_plan)

    columns = arrow_table.schema.names
    rows = _json_safe_rows(arrow_table.to_pylist())
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def _tool_create_dashboard(
    question: str,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Generate a canonical DashboardSpec for *question*.

    Reuses ``app.ai.dashboard.generate_dashboard_spec`` (M8).  With NullProvider
    the result is a deterministic generic TEMPLATE that does not actually
    interpret *question* beyond using it as the title.

    Returns ``{spec, html, generated_by, ai_generated, valid, issues, note?}``.

    ``generated_by`` / ``ai_generated`` exist because the caller previously had
    no way to tell an LLM-authored spec from the no-LLM fallback template: both
    came back looking identical, and ``valid`` (which is ONLY an HTML/XSS
    safety check on the rendered output — never a statement about whether the
    spec's queries and columns make sense together) read as blanket success. An
    agent asking for "revenue by region" with no API key configured therefore
    got a generic template back and no signal that its question had been
    ignored.
    """
    from app.ai.dashboard import generate_dashboard_spec, validate_dashboard_html  # noqa: PLC0415
    from app.ai.grounding import build_catalog  # noqa: PLC0415
    from app.ai.provider import get_provider  # noqa: PLC0415
    from app.dashboards.spec import spec_to_html  # noqa: PLC0415

    catalog = build_catalog()
    provider = get_provider()
    spec = generate_dashboard_spec(question, catalog, provider)
    html_out = spec_to_html(spec)
    ok, issues = validate_dashboard_html(html_out)

    generated_by = getattr(spec, "generated_by", "unknown")
    result: dict[str, Any] = {
        "spec": spec.model_dump(),
        "html": html_out,
        "generated_by": generated_by,
        "ai_generated": generated_by == "llm",
        # NOTE: `valid` is an HTML-safety result, NOT a semantic one.
        "valid": ok,
        "issues": issues,
    }
    if generated_by == "null_template":
        result["note"] = (
            "No LLM provider is configured, so this is a generic starter "
            "template — the question was used only as the title and was NOT "
            "interpreted. Widgets point at an arbitrary registered query. To "
            "build a dashboard that answers a real question without an LLM, "
            "use get_schema + create_query + run_query to author the queries "
            "yourself, then save_dashboard with a spec you construct."
        )
    return result


def _tool_edit_dashboard(
    spec: dict[str, Any],
    op: dict[str, Any],
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Apply an edit operation to a DashboardSpec and re-validate.

    The *op* dict describes what to do.  Supported operations:

    ``{"action": "add_widget", "widget": {...}}``
        Add a new widget to the spec.  The widget dict must conform to the
        ``Widget`` schema.

    ``{"action": "move_widget", "widget_id": "w1", "pos": {"x":1,"y":2,"w":4,"h":2}}``
        Update the position of an existing widget.

    ``{"action": "configure_widget", "widget_id": "w1", "updates": {...}}``
        Merge *updates* into the widget's fields (except ``id`` and ``type``).

    ``{"action": "remove_widget", "widget_id": "w1"}``
        Remove a widget by id.

    Returns
    -------
    dict
        ``{spec: dict, valid: bool, issues: list[str]}``
    """
    from app.dashboards.spec import validate_spec  # noqa: PLC0415

    # Deep-copy to avoid mutating caller's dict.
    working_spec = copy.deepcopy(spec)

    action = op.get("action", "")

    if action == "add_widget":
        widget_data = op.get("widget")
        if not widget_data or not isinstance(widget_data, dict):
            from app.errors import AppError  # noqa: PLC0415
            raise AppError(
                "invalid_tool_input",
                "edit_dashboard add_widget requires a 'widget' dict.",
                400,
            )
        working_spec.setdefault("widgets", []).append(widget_data)

    elif action == "move_widget":
        widget_id = op.get("widget_id")
        new_pos = op.get("pos")
        if not widget_id or not isinstance(new_pos, dict):
            from app.errors import AppError  # noqa: PLC0415
            raise AppError(
                "invalid_tool_input",
                "edit_dashboard move_widget requires 'widget_id' and 'pos'.",
                400,
            )
        for w in working_spec.get("widgets", []):
            if w.get("id") == widget_id:
                w["pos"] = new_pos
                break

    elif action == "configure_widget":
        widget_id = op.get("widget_id")
        updates = op.get("updates", {})
        if not widget_id or not isinstance(updates, dict):
            from app.errors import AppError  # noqa: PLC0415
            raise AppError(
                "invalid_tool_input",
                "edit_dashboard configure_widget requires 'widget_id' and 'updates'.",
                400,
            )
        for w in working_spec.get("widgets", []):
            if w.get("id") == widget_id:
                for k, v in updates.items():
                    if k not in ("id", "type"):  # protect immutable fields
                        w[k] = v
                break

    elif action == "remove_widget":
        widget_id = op.get("widget_id")
        if not widget_id:
            from app.errors import AppError  # noqa: PLC0415
            raise AppError(
                "invalid_tool_input",
                "edit_dashboard remove_widget requires 'widget_id'.",
                400,
            )
        working_spec["widgets"] = [
            w for w in working_spec.get("widgets", []) if w.get("id") != widget_id
        ]

    else:
        from app.errors import AppError  # noqa: PLC0415
        raise AppError(
            "invalid_tool_input",
            f"Unknown edit_dashboard action {action!r}. "
            "Supported: add_widget, move_widget, configure_widget, remove_widget.",
            400,
        )

    # Re-validate via dashboards/spec.py
    result_spec, issues = validate_spec(working_spec)
    if result_spec is not None:
        return {
            "spec": result_spec.model_dump(),
            "valid": len([i for i in issues if "not in the registered" not in i]) == 0,
            "issues": issues,
        }
    # Pydantic parse failed → return the raw working_spec + issues.
    return {"spec": working_spec, "valid": False, "issues": issues}


def _tool_save_dashboard(
    spec: dict[str, Any],
    name: str,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Persist *spec* as a new board — the missing link ``create_dashboard`` /
    ``edit_dashboard`` don't provide (those two only build/mutate a spec in
    memory; neither one writes it to the database).

    Saves via the SAME path ``POST /{resource}`` (``app.routes.resources``)
    uses for ``resource="boards"`` — ``repo.create(resource="boards", ...,
    config={"spec": ...})`` — so the result is a real board, listable via
    ``GET /boards`` and openable in the app exactly like one created through
    the UI. Does NOT go through ``require_writer`` (the route-level
    editor/owner role gate) since the tool layer has no role in *claims* to
    check — mirrors ``create_query``'s existing lack of a role gate; the
    embed-token exclusion below is the security-relevant boundary here (a
    read-only third-party embed session must never persist a board).

    Returns
    -------
    dict
        ``{id, name, org_id, saved: True}`` on success, or
        ``{error: {code, message}}`` / ``{valid: False, issues: [...]}
        without saving anything if *spec* fails validation.
    """
    from app.dashboards.spec import validate_spec  # noqa: PLC0415
    from app.errors import AppError  # noqa: PLC0415

    tool_kind: str = claims.get("kind", "access")
    if tool_kind != "access":
        raise AppError(
            "insufficient_scope",
            "Embed/restricted tokens cannot save dashboards via the AI tool path.",
            403,
        )
    org_id = claims.get("org")
    user_id = claims.get("sub")
    if not org_id or not user_id:
        raise AppError("invalid_tool_input", "Caller claims are missing org/user id.", 400)

    result_spec, issues = validate_spec(spec)
    if result_spec is None:
        return {"valid": False, "issues": issues, "saved": False}

    from app.repos.provider import get_repo  # noqa: PLC0415

    repo = get_repo()
    row = _run_sync(
        repo.create(
            resource="boards",
            org_id=str(org_id),
            created_by=str(user_id),
            name=name,
            config={"spec": result_spec.model_dump()},
        )
    )
    return {"id": row["id"], "name": row.get("name"), "org_id": str(org_id), "saved": True}


def _tool_upload_image(
    claims: dict[str, Any],
    data_base64: str | None = None,
    content_type: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Store an image (base64 bytes OR a URL) and return a servable URL.

    Exactly one of *data_base64* or *url* must be given. For *data_base64*
    the content-type is sniffed from magic bytes when *content_type* is
    omitted; fetching by *url* is SSRF-guarded (DNS-rebind-safe pinned fetch —
    see ``app.dashboards.images.fetch_image_from_url``).

    Returns ``{id, url, content_type, size}`` — *url* is a relative
    ``/api/v1/images/{id}`` path this app's own backend serves (embed it
    directly in an ``html`` widget's ``<img src="...">`` — there is no
    dedicated "image" widget type, see ``Widget.type`` in
    ``app.dashboards.spec``). Raises ``AppError("invalid_image", 400)`` for
    an unsupported/unsniffable content-type or an oversized payload, or
    ``AppError("ssrf_blocked", 400)`` for a disallowed *url* target.
    """
    import base64  # noqa: PLC0415

    from app.dashboards.images import (  # noqa: PLC0415
        fetch_image_from_url,
        save_image_bytes,
        sniff_content_type,
    )
    from app.errors import AppError  # noqa: PLC0415

    if bool(data_base64) == bool(url):
        raise AppError(
            "invalid_tool_input", "Provide exactly one of data_base64 or url.", 400
        )

    if url is not None:
        result = fetch_image_from_url(url)
    else:
        try:
            data = base64.b64decode(data_base64, validate=True)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            raise AppError("invalid_tool_input", f"data_base64 is not valid base64: {exc}", 400) from exc
        ctype = content_type or sniff_content_type(data)
        if ctype is None:
            raise AppError(
                "invalid_image",
                "Could not determine the image's content-type from its bytes; "
                "pass content_type explicitly (image/png, image/jpeg, image/gif, image/webp).",
                400,
            )
        result = save_image_bytes(data, ctype)

    return {
        "id": result["id"],
        "url": f"/api/v1/images/{result['id']}",
        "content_type": result["content_type"],
        "size": result["size"],
    }


# ---------------------------------------------------------------------------
# JSON Schemas for each tool
# ---------------------------------------------------------------------------

_SCHEMA_GET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "table_pattern": {
            "type": "string",
            "description": (
                "Substring to match against table names (case-insensitive). "
                "When given, returns full columns for matching tables plus the "
                "queries that touch them. Omit for a compact table listing."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Max queries to return when table_pattern narrows the result (default 30).",
        },
    },
    "additionalProperties": False,
}

_SCHEMA_LIST_QUERIES: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SCHEMA_GENERATE_SQL: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "Natural-language question to convert to SQL.",
        },
        "datastore_id": {
            "type": "string",
            "description": "Optional datastore id for context.",
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}

_SCHEMA_CREATE_QUERY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": (
                "Optional stable URL-safe identifier. Omit this to get a "
                "generated id AND have the query persisted (survives a "
                "backend restart, referenceable by a saved dashboard widget) "
                "— an explicit id registers in-memory only for this session."
            ),
        },
        "sql": {
            "type": "string",
            "description": "The SELECT SQL string to register.",
        },
        "name": {
            "type": "string",
            "description": (
                "Human-readable display name shown in the Queries list. "
                "Defaults to a title-cased version of id when omitted — "
                "which is a poor name when id is also omitted, so pass this "
                "explicitly for anything meant to be recognisable later."
            ),
        },
        "datastore_id": {
            "type": "string",
            "description": (
                "Optional datastore/connector id to bind this query to. Omit "
                "for the built-in demo dataset. REQUIRED to target a real "
                "(BYO) connector — without it the query always runs against "
                "the demo dataset regardless of what tables the SQL names."
            ),
        },
        "params": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["text", "number", "date", "daterange", "select", "multiselect"],
                    },
                    "required": {"type": "boolean"},
                    "default": {},
                    "options_query_id": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            "description": "Optional named parameter descriptors.",
        },
    },
    "required": ["sql"],
    "additionalProperties": False,
}

_SCHEMA_LIST_FILTERABLE_COLUMNS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query_id": {"type": "string", "description": "Id of the registered query."},
    },
    "required": ["query_id"],
    "additionalProperties": False,
}

_SCHEMA_ADD_FILTER_PARAM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query_id": {"type": "string", "description": "Id of the registered query to modify."},
        "param": {
            "type": "string",
            "description": "New parameter name. A dashboard filter variable of the same name can then drive this query.",
        },
        "column": {
            "type": "string",
            "description": "Column to filter on — use a name from list_filterable_columns.",
        },
        "subtype": {
            "type": "string",
            "enum": ["multiselect", "select", "daterange"],
            "description": "Filter shape (default multiselect).",
        },
        "apply": {
            "type": "boolean",
            "description": "False (default) previews the rewrite without saving; true saves it once verified.",
        },
    },
    "required": ["query_id", "param", "column"],
    "additionalProperties": False,
}

_SCHEMA_RUN_QUERY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query_id": {
            "type": "string",
            "description": "Id of a registered query to execute.",
        },
        "sql": {
            "type": "string",
            "description": "Ad-hoc SELECT SQL (used if query_id not provided).",
        },
        "named_params": {
            "type": "object",
            "description": "Named parameter values for {{name}} placeholders.",
            "additionalProperties": True,
        },
        "datastore_id": {
            "type": "string",
            "description": (
                "Optional datastore id to run against (overrides a registered "
                "query's own binding; required for ad-hoc SQL against a non-demo "
                "datastore)."
            ),
        },
    },
    "additionalProperties": False,
}

_SCHEMA_LIST_METRICS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SCHEMA_QUERY_METRIC: dict[str, Any] = {
    "type": "object",
    "properties": {
        "metric_id": {
            "type": "string",
            "description": "Id of a registered governed metric (see list_metrics).",
        },
        "dimensions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Allowed dimensions to group by (subset of the metric's dims).",
        },
        "time_grain": {
            "type": "string",
            "enum": ["hour", "day", "week", "month", "quarter", "year"],
            "description": "Optional time bucket grain (requires the metric to declare a time dimension).",
        },
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "op": {
                        "type": "string",
                        "enum": ["=", "!=", "<", "<=", ">", ">=", "in", "not_in"],
                    },
                    "value": {},
                },
                "required": ["field"],
                "additionalProperties": False,
            },
            "description": "Filters on allowed dimensions or the time column (values are bound, not concatenated).",
        },
        "limit": {
            "type": "integer",
            "description": "Optional row limit.",
        },
    },
    "required": ["metric_id"],
    "additionalProperties": False,
}

_SCHEMA_CREATE_DASHBOARD: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "Natural-language question describing the desired dashboard.",
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}

_SCHEMA_EDIT_DASHBOARD: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": {
            "type": "object",
            "description": "The current DashboardSpec dict to modify.",
        },
        "op": {
            "type": "object",
            "description": (
                "Edit operation. "
                "{'action':'add_widget','widget':{...}} | "
                "{'action':'move_widget','widget_id':'w1','pos':{x,y,w,h}} | "
                "{'action':'configure_widget','widget_id':'w1','updates':{...}} | "
                "{'action':'remove_widget','widget_id':'w1'}"
            ),
            "properties": {
                "action": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    "required": ["spec", "op"],
    "additionalProperties": False,
}

_SCHEMA_SAVE_DASHBOARD: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": {
            "type": "object",
            "description": "The DashboardSpec dict to persist (from create_dashboard/edit_dashboard).",
        },
        "name": {
            "type": "string",
            "description": "Name for the saved board.",
        },
    },
    "required": ["spec", "name"],
    "additionalProperties": False,
}

_SCHEMA_UPLOAD_IMAGE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "data_base64": {
            "type": "string",
            "description": "Base64-encoded image bytes (provide this OR url, not both).",
        },
        "content_type": {
            "type": "string",
            "description": (
                "MIME type of data_base64 (image/png, image/jpeg, image/gif, image/webp). "
                "Optional — sniffed from magic bytes if omitted."
            ),
        },
        "url": {
            "type": "string",
            "description": "URL to fetch the image from (provide this OR data_base64, not both).",
        },
    },
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _make_registry() -> dict[str, ToolDef]:
    """Build and return the module-level tool registry."""

    def _wrap_get_schema(
        claims: dict[str, Any],
        table_pattern: str | None = None,
        limit: int | None = None,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_get_schema(claims, table_pattern=table_pattern, limit=limit)

    def _wrap_list_queries(claims: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        return _tool_list_queries(claims)

    def _wrap_generate_sql(
        claims: dict[str, Any],
        question: str,
        datastore_id: str | None = None,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_generate_sql(question, claims, datastore_id=datastore_id)

    def _wrap_create_query(
        claims: dict[str, Any],
        sql: str,
        id: str | None = None,
        name: str | None = None,
        params: list[dict[str, Any]] | None = None,
        datastore_id: str | None = None,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_create_query(sql, claims, id=id, name=name, params=params, datastore_id=datastore_id)

    def _wrap_list_filterable_columns(
        claims: dict[str, Any], query_id: str, **_kw: Any
    ) -> dict[str, Any]:
        return _tool_list_filterable_columns(query_id, claims)

    def _wrap_add_filter_param(
        claims: dict[str, Any],
        query_id: str,
        param: str,
        column: str,
        subtype: str = "multiselect",
        apply: bool = False,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_add_filter_param(
            query_id, param, column, claims, subtype=subtype, apply=apply
        )

    def _wrap_run_query(
        claims: dict[str, Any],
        query_id: str | None = None,
        sql: str | None = None,
        named_params: dict[str, Any] | None = None,
        datastore_id: str | None = None,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_run_query(
            claims,
            query_id=query_id,
            sql=sql,
            named_params=named_params,
            datastore_id=datastore_id,
        )

    def _wrap_list_metrics(claims: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        return _tool_list_metrics(claims)

    def _wrap_query_metric(
        claims: dict[str, Any],
        metric_id: str,
        dimensions: list[str] | None = None,
        time_grain: str | None = None,
        filters: list[dict[str, Any]] | None = None,
        limit: int | None = None,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_query_metric(
            claims,
            metric_id,
            dimensions=dimensions,
            time_grain=time_grain,
            filters=filters,
            limit=limit,
        )

    def _wrap_create_dashboard(
        claims: dict[str, Any],
        question: str,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_create_dashboard(question, claims)

    def _wrap_edit_dashboard(
        claims: dict[str, Any],
        spec: dict[str, Any],
        op: dict[str, Any],
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_edit_dashboard(spec, op, claims)

    def _wrap_save_dashboard(
        claims: dict[str, Any],
        spec: dict[str, Any],
        name: str,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_save_dashboard(spec, name, claims)

    def _wrap_upload_image(
        claims: dict[str, Any],
        data_base64: str | None = None,
        content_type: str | None = None,
        url: str | None = None,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_upload_image(
            claims, data_base64=data_base64, content_type=content_type, url=url
        )

    from app.ai.flow_tools import make_flow_tool_defs  # noqa: PLC0415

    tools = [
        ToolDef(
            name="get_schema",
            description=(
                "Return the catalog schema from the query registry. With no "
                "arguments, returns a COMPACT table listing (name + column "
                "count) — call again with table_pattern (a substring of the "
                "table name) to get full columns and the queries that touch "
                "those tables. List, then narrow — don't expect full detail "
                "on the first call."
            ),
            json_schema=_SCHEMA_GET_SCHEMA,
            fn=_wrap_get_schema,
        ),
        ToolDef(
            name="list_queries",
            description="List all registered queries with their ids, names, and parameter descriptors.",
            json_schema=_SCHEMA_LIST_QUERIES,
            fn=_wrap_list_queries,
        ),
        ToolDef(
            name="generate_sql",
            description="Generate a grounded SQL SELECT from a natural-language question.",
            json_schema=_SCHEMA_GENERATE_SQL,
            fn=_wrap_generate_sql,
        ),
        ToolDef(
            name="create_query",
            description="Register a SQL query in the query registry under a given id.",
            json_schema=_SCHEMA_CREATE_QUERY,
            fn=_wrap_create_query,
        ),
        ToolDef(
            name="list_filterable_columns",
            description=(
                "List the columns of a registered query that a dashboard "
                "filter could be attached to — including columns inside "
                "subqueries that the query aggregates away before its output. "
                "Call this before add_filter_param to pick a real column."
            ),
            json_schema=_SCHEMA_LIST_FILTERABLE_COLUMNS,
            fn=_wrap_list_filterable_columns,
        ),
        ToolDef(
            name="add_filter_param",
            description=(
                "Make a registered query filterable: inject a guarded filter "
                "on `column` bound to a new `param`, so a dashboard filter "
                "variable of that name can drive it. The rewrite is injected "
                "at the innermost scope exposing the column (so it filters "
                "before any roll-up) and is VERIFIED before saving — the "
                "query is re-run with the filter unset and must return "
                "exactly the original result, otherwise the change is "
                "refused. Use list_filterable_columns first."
            ),
            json_schema=_SCHEMA_ADD_FILTER_PARAM,
            fn=_wrap_add_filter_param,
        ),
        ToolDef(
            name="run_query",
            description=(
                "Execute a registered query (by query_id) or an ad-hoc SELECT. "
                "Caller's RLS claims are enforced — results never exceed caller scope."
            ),
            json_schema=_SCHEMA_RUN_QUERY,
            fn=_wrap_run_query,
        ),
        ToolDef(
            name="list_metrics",
            description=(
                "List all registered governed metrics (id, name, measure, dimensions, "
                "time_grains, description). Answer metric questions from these, not raw SQL."
            ),
            json_schema=_SCHEMA_LIST_METRICS,
            fn=_wrap_list_metrics,
        ),
        ToolDef(
            name="query_metric",
            description=(
                "Execute a GOVERNED metric query (group by allowed dimensions / time_grain, "
                "filtered) and return rows. Caller's RLS claims are enforced — results never "
                "exceed caller scope. Use this to answer metric questions instead of hallucinated SQL."
            ),
            json_schema=_SCHEMA_QUERY_METRIC,
            fn=_wrap_query_metric,
        ),
        ToolDef(
            name="create_dashboard",
            description="Generate a canonical DashboardSpec for a natural-language question.",
            json_schema=_SCHEMA_CREATE_DASHBOARD,
            fn=_wrap_create_dashboard,
        ),
        ToolDef(
            name="edit_dashboard",
            description=(
                "Apply an edit operation (add/move/configure/remove widget) to a DashboardSpec "
                "and re-validate it."
            ),
            json_schema=_SCHEMA_EDIT_DASHBOARD,
            fn=_wrap_edit_dashboard,
        ),
        ToolDef(
            name="save_dashboard",
            description=(
                "Persist a DashboardSpec as a real, listable board (create_dashboard/"
                "edit_dashboard only build/mutate a spec in memory — this is the save step)."
            ),
            json_schema=_SCHEMA_SAVE_DASHBOARD,
            fn=_wrap_save_dashboard,
        ),
        ToolDef(
            name="upload_image",
            description=(
                "Store an image (base64 bytes from a local file, or fetched from a URL) and "
                "return a servable /api/v1/images/{id} URL to use in a dashboard's html widget."
            ),
            json_schema=_SCHEMA_UPLOAD_IMAGE,
            fn=_wrap_upload_image,
        ),
    ]
    # Append flow orchestrator tools.
    tools.extend(make_flow_tool_defs())
    return {t.name: t for t in tools}


_REGISTRY: dict[str, ToolDef] = _make_registry()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_tool(name: str) -> ToolDef | None:
    """Return the ``ToolDef`` for *name*, or ``None`` if unknown."""
    return _REGISTRY.get(name)


def all_tools() -> list[ToolDef]:
    """Return all registered tools in insertion order."""
    return list(_REGISTRY.values())


def tool_schemas() -> list[dict[str, Any]]:
    """Return a list of tool descriptors ready to inject into an LLM prompt.

    Each entry has the shape::

        {
            "name": "<tool name>",
            "description": "...",
            "input_schema": { ... json schema ... }
        }

    This format matches the Anthropic tool-use API convention.  Other providers
    can adapt the shape; the agent loop is responsible for formatting.
    """
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.json_schema,
        }
        for t in _REGISTRY.values()
    ]


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Validate *arguments* against the tool's schema and execute it.

    Parameters
    ----------
    name:
        Tool name (must be registered).
    arguments:
        Dict of tool arguments from the agent / model.
    claims:
        Caller's auth claims (passed to every tool).

    Returns
    -------
    dict
        JSON-serialisable result from the tool.

    Raises
    ------
    AppError("tool_not_found", 404)
        If *name* is not a registered tool.
    AppError("invalid_tool_input", 400)
        If *arguments* fails basic schema validation.
    """
    from app.errors import AppError  # noqa: PLC0415

    tool = _REGISTRY.get(name)
    if tool is None:
        raise AppError("tool_not_found", f"No tool named {name!r}.", 404)

    # Basic schema validation: check required fields.
    required_fields: list[str] = tool.json_schema.get("required", [])
    for req in required_fields:
        if req not in arguments:
            raise AppError(
                "invalid_tool_input",
                f"Tool {name!r} requires argument {req!r}.",
                400,
            )

    # Check for unexpected arguments when additionalProperties is False.
    if not tool.json_schema.get("additionalProperties", True):
        allowed = set(tool.json_schema.get("properties", {}).keys())
        extra = set(arguments.keys()) - allowed
        if extra:
            raise AppError(
                "invalid_tool_input",
                f"Tool {name!r} received unexpected arguments: {sorted(extra)}.",
                400,
            )

    return tool.fn(claims=claims, **arguments)
