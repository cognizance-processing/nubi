"""Query editor tooling endpoints — validation, completion, schema.

This module exposes a small set of editor-support endpoints used by the
Playground SQL editor (``src/components/SqlEditor.jsx``):

POST /query/validate
    Body: ``{sql: str, dialect?: str}``
    Parse *sql* with **sqlglot** in the requested dialect and return
    ``{ok: bool, errors: [{message, line, col}]}``.  This gives accurate,
    dialect-aware (BigQuery / DuckDB / Postgres / MySQL) syntax checking that
    the editor renders as Monaco markers (squiggles).

POST /query/complete
    Body: ``{sql: str, cursor?: int, schema?: str}``
    A quick, best-effort LLM completion for the text immediately before the
    cursor.  Reuses the existing ``app.ai.provider`` client/key.  Kept short and
    low-latency (single suggestion, tiny token budget).  Returns
    ``{suggestion: str}`` — empty string when nothing useful can be produced
    (e.g. NullProvider / no API key / provider error).  Never raises on a
    provider failure so the editor can fall back to local completion.

GET /query/schema
    Returns ``{tables: {name: [cols...]}}`` derived from the AI grounding
    catalog (registered queries + lineage index).  The editor uses this to
    seed schema-aware autocomplete (table + column suggestions).

GET /datastores/{datastore_id}/tables
    Returns ``{tables: [{name, schema, rows?}]}`` — the tables in a datastore,
    introspected via ``information_schema`` (with a ``SHOW TABLES`` fallback),
    plus a cheap ``COUNT(*)`` row count per table.  Org-scoped + authed.  Used
    by the data browser (``src/pages/app/DataBrowser.jsx``) to show what a
    connector contains.  Degrades gracefully for connectors that cannot
    introspect (returns an empty list rather than 500).

GET /datastores/{datastore_id}/tables/{table}/preview?limit=50
    Returns ``{columns: [{name, type}], rows: [[...], ...], row_count}`` — the
    first N rows of a table as plain JSON (capped at 200).  Reuses the same
    connector/planner execution path as ``POST /query``.  The table name is
    validated against the introspected table allowlist before use (no SQL
    injection).

Auth
----
All endpoints reuse the first-party ``current_user`` dependency from
``app.auth.deps`` (same auth posture as ``POST /ai/ask``).

Wiring
------
This file defines its OWN ``APIRouter`` (``router``) — it does NOT mutate the
shared ``api_router`` and does NOT edit ``main.py``.  To mount it, add to
``main.py`` (next to the other route imports)::

    from app.routes.query_tools import router as query_tools_router
    api_router.include_router(query_tools_router)

The router carries no prefix of its own; paths are ``/query/validate``,
``/query/complete`` and ``/query/schema``.  Once mounted under the api_router
(``/api/v1``) they resolve to ``/api/v1/query/validate`` etc.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import current_user

router = APIRouter(tags=["query-tools"])

# Dialects we accept from the editor's dialect selector.  Anything else falls
# back to a permissive default so a typo in the selector never 500s.
_SUPPORTED_DIALECTS: frozenset[str] = frozenset(
    {"bigquery", "duckdb", "postgres", "mysql"}
)
_DEFAULT_DIALECT = "postgres"


def _normalise_dialect(dialect: str | None) -> str:
    """Map a requested dialect to a supported sqlglot dialect string."""
    if not dialect:
        return _DEFAULT_DIALECT
    d = dialect.strip().lower()
    return d if d in _SUPPORTED_DIALECTS else _DEFAULT_DIALECT


# ---------------------------------------------------------------------------
# POST /query/validate
# ---------------------------------------------------------------------------


class ValidateIn(BaseModel):
    """Request body for POST /query/validate."""

    sql: str = ""
    dialect: str | None = None


class ValidationError(BaseModel):
    """A single parse error, positioned for Monaco markers (1-based line/col).

    ``severity`` mirrors the Monaco ``MarkerSeverity`` string values the
    frontend maps to ``monaco.MarkerSeverity.Error`` / ``.Warning`` etc.
    Currently all sqlglot parse errors are classified as ``'error'``; reserved
    ``'warning'`` for future lint/hint diagnostics.
    """

    message: str
    line: int
    col: int
    severity: str = "error"  # 'error' | 'warning' | 'info' | 'hint'


class ValidateOut(BaseModel):
    """Response body for POST /query/validate."""

    ok: bool
    errors: list[ValidationError] = []


@router.post("/query/validate", response_model=ValidateOut)
async def validate_sql(
    body: ValidateIn,
    _user: dict[str, Any] = Depends(current_user),
) -> ValidateOut:
    """Parse *sql* with sqlglot in *dialect* and return positioned errors/warnings.

    Returns ``{ok: True, errors: []}`` for empty/whitespace SQL so the editor
    does not flag a blank cell.  On a parse failure returns ``{ok: False,
    errors: [...]}`` with one entry per error sqlglot reports, each carrying a
    1-based ``line``/``col`` suitable for Monaco markers.

    When the SQL parses successfully a lightweight AST-based WARNING pass is
    also run (see ``_lint_warnings``).  Warnings do NOT set ``ok=False`` — they
    are purely advisory markers appended to the ``errors`` list with
    ``severity='warning'``.
    """
    sql = body.sql or ""
    dialect = _normalise_dialect(body.dialect)

    if not sql.strip():
        return ValidateOut(ok=True, errors=[])

    import sqlglot
    from sqlglot.errors import ParseError

    try:
        # parse() surfaces all statements; a ParseError aggregates every issue
        # sqlglot found, each with line/col context in ``.errors``.
        stmts = sqlglot.parse(sql, dialect=dialect)
    except ParseError as exc:
        errors = _parse_errors_from_exc(exc, sql)
        return ValidateOut(ok=False, errors=errors)
    except Exception as exc:  # noqa: BLE001 — any other sqlglot error → single marker
        return ValidateOut(
            ok=False,
            errors=[ValidationError(message=str(exc).strip() or "Invalid SQL", line=1, col=1, severity="error")],
        )

    # Parse succeeded — run the advisory WARNING pass.
    warnings = _lint_warnings(stmts or [])
    return ValidateOut(ok=True, errors=warnings)


def _parse_errors_from_exc(exc: Any, sql: str) -> list[ValidationError]:
    """Extract positioned ``ValidationError``s from a sqlglot ``ParseError``.

    sqlglot's ``ParseError.errors`` is a list of dicts with ``description``,
    ``line``, ``col`` (1-based) keys.  We defensively handle older/newer shapes
    and fall back to a single line-1 marker when no structured data is present.
    """
    raw_errors = getattr(exc, "errors", None) or []
    out: list[ValidationError] = []
    for e in raw_errors:
        if not isinstance(e, dict):
            continue
        message = str(
            e.get("description") or e.get("message") or "Syntax error"
        ).strip()
        # sqlglot positions are 1-based; clamp to >= 1 for Monaco.
        line = e.get("line")
        col = e.get("col")
        try:
            line = max(1, int(line)) if line is not None else 1
        except (TypeError, ValueError):
            line = 1
        try:
            col = max(1, int(col)) if col is not None else 1
        except (TypeError, ValueError):
            col = 1
        out.append(ValidationError(message=message, line=line, col=col, severity="error"))

    if not out:
        # No structured positions — surface the raw message at the start.
        out.append(
            ValidationError(
                message=str(exc).strip().splitlines()[0] if str(exc).strip() else "Invalid SQL",
                line=1,
                col=1,
                severity="error",
            )
        )
    return out


def _lint_warnings(stmts: list[Any]) -> list[ValidationError]:
    """Run an AST-level advisory WARNING pass over successfully-parsed statements.

    Checks performed
    ----------------
    (a) ``SELECT *`` — suggests using explicit column names instead of a
        star-expansion, which can cause brittle pipelines when upstream
        schemas change.
    (b) ``DELETE`` or ``UPDATE`` without a ``WHERE`` clause — these mutate
        every row in the table, which is almost certainly unintentional.
    (c) Cartesian / comma-join (a ``FROM`` clause with multiple comma-separated
        tables and no explicit JOIN condition) — these produce a cross-product
        and are usually a forgotten WHERE predicate.

    Warnings do NOT affect ``ok`` — they are purely advisory.  The line/col
    of the offending node is used when available; otherwise defaults to (1, 1).

    Parameters
    ----------
    stmts:
        The list of sqlglot AST nodes returned by ``sqlglot.parse()``.

    Returns
    -------
    list[ValidationError]
        Zero or more ``severity='warning'`` entries.
    """
    import sqlglot.expressions as _exp  # noqa: PLC0415

    warnings: list[ValidationError] = []

    def _pos(node: Any) -> tuple[int, int]:
        """Extract 1-based (line, col) from a sqlglot node, defaulting to (1,1)."""
        meta = getattr(node, "meta", None) or {}
        line = meta.get("line") or 1
        col = meta.get("col") or 1
        try:
            line = max(1, int(line))
            col = max(1, int(col))
        except (TypeError, ValueError):
            line, col = 1, 1
        return line, col

    for stmt in stmts:
        if stmt is None:
            continue

        # ── (a) SELECT * ─────────────────────────────────────────────────────
        if isinstance(stmt, _exp.Select):
            for star_node in stmt.find_all(_exp.Star):
                # Only flag top-level stars; nested ones (e.g. COUNT(*)) are OK
                # when they appear inside an aggregate function.
                if isinstance(star_node.parent, _exp.AggFunc):
                    continue
                line, col = _pos(star_node)
                warnings.append(
                    ValidationError(
                        message=(
                            "SELECT * detected — consider naming columns explicitly "
                            "to avoid breakage when the source schema changes."
                        ),
                        line=line,
                        col=col,
                        severity="warning",
                    )
                )
                break  # one warning per SELECT * statement is enough

        # ── (b) DELETE / UPDATE without WHERE ────────────────────────────────
        if isinstance(stmt, (_exp.Delete, _exp.Update)):
            where_node = stmt.args.get("where")
            if where_node is None:
                stmt_name = "DELETE" if isinstance(stmt, _exp.Delete) else "UPDATE"
                line, col = _pos(stmt)
                warnings.append(
                    ValidationError(
                        message=(
                            f"{stmt_name} without a WHERE clause will affect every row "
                            "in the table — add a WHERE condition or this is almost "
                            "certainly unintentional."
                        ),
                        line=line,
                        col=col,
                        severity="warning",
                    )
                )

        # ── (c) Cartesian / comma-join ────────────────────────────────────────
        # A FROM clause that lists ≥ 2 tables via comma (no explicit JOIN) AND
        # has no WHERE clause is almost certainly a cross-product.
        if isinstance(stmt, _exp.Select):
            from_node = stmt.args.get("from")
            joins = stmt.args.get("joins") or []
            where_node = stmt.args.get("where")
            # from_node.expressions holds the comma-separated table refs.
            from_tables: list[Any] = []
            if from_node is not None:
                from_tables = getattr(from_node, "expressions", []) or []
            if len(from_tables) >= 2 and not joins and where_node is None:
                line, col = _pos(from_node)
                warnings.append(
                    ValidationError(
                        message=(
                            "Possible cartesian join: multiple tables in FROM without "
                            "an explicit JOIN or WHERE condition produce a cross-product."
                        ),
                        line=line,
                        col=col,
                        severity="warning",
                    )
                )

    return warnings


# ---------------------------------------------------------------------------
# POST /query/complete
# ---------------------------------------------------------------------------


class CompleteIn(BaseModel):
    """Request body for POST /query/complete."""

    sql: str = ""
    cursor: int | None = None
    # ``schema_`` avoids shadowing pydantic's BaseModel.schema; the wire field
    # name stays ``schema`` via the alias so the frontend payload is unchanged.
    schema_: str | None = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True}


class CompleteOut(BaseModel):
    """Response body for POST /query/complete."""

    suggestion: str = ""


_COMPLETE_SYSTEM = (
    "You are a SQL autocomplete engine. Given a partial SQL query (everything "
    "BEFORE the user's cursor), output ONLY the text that should be inserted at "
    "the cursor to continue the query — no explanation, no markdown, no leading "
    "whitespace, no repetition of text already typed. Keep it to a single short "
    "clause. If a schema is provided, only reference its tables/columns."
)


@router.post("/query/complete", response_model=CompleteOut)
async def complete_sql(
    body: CompleteIn,
    _user: dict[str, Any] = Depends(current_user),
) -> CompleteOut:
    """Best-effort single-shot LLM completion for the text before the cursor.

    Reuses ``app.ai.provider.get_provider()`` (same client/key resolution as the
    rest of the AI surface).  With ``NullProvider`` (no API key) or on ANY
    provider error this returns ``{suggestion: ""}`` so the editor falls back to
    local completion.  Kept short/low-latency by truncating the prompt context
    and trimming the output to a single line.
    """
    from app.ai.provider import NullProvider, get_provider

    sql = body.sql or ""
    cursor = body.cursor if body.cursor is not None else len(sql)
    cursor = max(0, min(cursor, len(sql)))

    # Context = text immediately before the cursor (cap to keep latency low).
    prefix = sql[:cursor][-1000:]
    if not prefix.strip():
        return CompleteOut(suggestion="")

    try:
        provider = get_provider()
    except Exception:  # noqa: BLE001 — llm_not_configured etc. → no suggestion
        return CompleteOut(suggestion="")

    # NullProvider produces no useful completion — skip the call entirely so the
    # client falls straight back to local schema/keyword completion.
    if isinstance(provider, NullProvider):
        return CompleteOut(suggestion="")

    system = _COMPLETE_SYSTEM
    if body.schema_ and body.schema_.strip():
        system = f"{system}\n\nSCHEMA:\n{body.schema_.strip()[:2000]}"

    user = f"Partial SQL (complete at the end):\n{prefix}"

    try:
        raw = provider.complete(user, system=system)
    except Exception:  # noqa: BLE001 — network/SDK/key error → no suggestion
        return CompleteOut(suggestion="")

    suggestion = _clean_completion(raw)
    return CompleteOut(suggestion=suggestion)


def _clean_completion(raw: str) -> str:
    """Trim an LLM completion to a single short insertable fragment."""
    if not raw:
        return ""
    text = raw.strip()
    # Strip accidental code fences.
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1 if lines and lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines and lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end]).strip()
    # Single line only — autocomplete inserts one clause.
    first_line = text.splitlines()[0] if text.splitlines() else ""
    return first_line[:200]


# ---------------------------------------------------------------------------
# GET /query/schema
# ---------------------------------------------------------------------------


class SchemaOut(BaseModel):
    """Response body for GET /query/schema."""

    tables: dict[str, list[str]] = {}


@router.get("/query/schema", response_model=SchemaOut)
async def schema(
    _user: dict[str, Any] = Depends(current_user),
) -> SchemaOut:
    """Return ``{tables: {name: [cols...]}}`` for schema-aware autocomplete.

    Sourced from the AI grounding catalog (registered queries + lineage index),
    the single source of truth already used for text-to-SQL grounding.  Returns
    an empty mapping (never raises) when no catalog data is available.
    """
    try:
        from app.ai.grounding import build_catalog

        catalog = build_catalog()
        tables = catalog.get("tables", {}) or {}
        # Defensive copy + coerce to the declared shape.
        clean: dict[str, list[str]] = {
            str(t): [str(c) for c in (cols or [])] for t, cols in tables.items()
        }
        return SchemaOut(tables=clean)
    except Exception:  # noqa: BLE001 — best-effort; empty schema is acceptable
        return SchemaOut(tables={})


# ---------------------------------------------------------------------------
# Data browser — list a datastore's tables + preview rows
#
# These endpoints power src/pages/app/DataBrowser.jsx (reached from the
# Connectors page).  They return plain JSON (not Arrow IPC) so the browser can
# render a simple preview grid without an Arrow dependency on the request path.
# Execution reuses the same connector + planner path as POST /query.
# ---------------------------------------------------------------------------

import re as _re

from fastapi import Path

from app.connectors.plan import PhysicalPlan
from app.errors import AppError

# Cap preview rows hard, independent of what the caller requests.
_PREVIEW_MAX_LIMIT = 200

# A safe SQL identifier — checked before any table name is interpolated into
# introspection SQL.  Tables are *also* validated against the introspected
# allowlist; this is belt-and-braces.
_SAFE_IDENT_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_.$]*$")


def _safe_ident(name: str) -> bool:
    return bool(name) and len(name) <= 256 and bool(_SAFE_IDENT_RE.match(name))


class TableInfo(BaseModel):
    """A single table in a datastore."""

    name: str
    schema_: str | None = Field(default=None, alias="schema")
    rows: int | None = None

    model_config = {"populate_by_name": True}


class TablesOut(BaseModel):
    """Response body for GET /datastores/{id}/tables."""

    tables: list[TableInfo] = []
    datastore_id: str


class PreviewColumn(BaseModel):
    """A single column in a preview result."""

    name: str
    type: str


class PreviewOut(BaseModel):
    """Response body for GET /datastores/{id}/tables/{table}/preview."""

    table: str
    columns: list[PreviewColumn] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    limit: int = 50
    truncated: bool = False


async def _resolve_datastore_connector(
    datastore_id: str,
    user: dict[str, Any],
) -> Any:
    """Resolve an org-scoped datastore and build a (DuckDB) connector for it.

    Mirrors the org-scoping posture of POST /query and the existing data
    browser: a datastore belonging to another org is treated as not-found so
    no information leaks.  Currently introspectable connector types (duckdb)
    build a read-only connector; other types raise a 400 so the UI can show a
    clear "introspection not supported" message rather than a 500.
    """
    from app.repos.provider import get_repo
    from app.routes.resources import get_user_org

    repo = get_repo()
    org_id = await get_user_org(str(user["id"]), repo)
    ds = await repo.get("datastores", org_id, datastore_id)
    if ds is None:
        raise AppError("not_found", f"Datastore {datastore_id!r} not found.", 404)

    cfg: dict = dict(ds.get("config") or {})
    ctype = cfg.get("connector_type") or cfg.get("type") or "duckdb"

    if ctype != "duckdb":
        raise AppError(
            "not_supported",
            f"Data preview currently supports duckdb connectors; got {ctype!r}. "
            "Other connector types can be queried from the SQL editor.",
            400,
        )

    from app.connectors.duckdb_conn import DuckDBConnector

    db_path = cfg.get("database") or cfg.get("path")
    if db_path and db_path not in (":memory:", ""):
        import duckdb as _duckdb

        conn = _duckdb.connect(database=db_path, read_only=True)
        try:
            conn.execute("SET enable_external_access=false")
        except Exception:  # noqa: BLE001 — best-effort hardening
            pass
        return DuckDBConnector(conn)
    return DuckDBConnector()


def _introspect_tables(connector: Any) -> list[dict[str, Any]]:
    """Return [{name, schema}] for user tables; [] if introspection fails."""
    sql = (
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
        "ORDER BY table_schema, table_name"
    )
    plan = PhysicalPlan(sql=sql, params=[], cache_key="", rls_claims={})
    try:
        d = connector.execute(plan).to_pydict()
        schemas = d.get("table_schema", [])
        names = d.get("table_name", [])
        return [{"name": n, "schema": s} for s, n in zip(schemas, names)]
    except Exception:  # noqa: BLE001 — fall back to SHOW TABLES
        try:
            plan2 = PhysicalPlan(sql="SHOW TABLES", params=[], cache_key="", rls_claims={})
            d2 = connector.execute(plan2).to_pydict()
            col = next((k for k in d2 if k.lower() == "name"), None)
            return [{"name": n, "schema": "main"} for n in (d2.get(col, []) if col else [])]
        except Exception:  # noqa: BLE001 — connector can't introspect
            return []


def _count_rows(connector: Any, table: str) -> int | None:
    """Cheap COUNT(*) for *table*; None if it fails (best-effort)."""
    if not _safe_ident(table):
        return None
    plan = PhysicalPlan(
        sql=f"SELECT COUNT(*) AS n FROM {table}", params=[], cache_key="", rls_claims={}
    )
    try:
        d = connector.execute(plan).to_pydict()
        vals = next(iter(d.values()), [])
        return int(vals[0]) if vals else 0
    except Exception:  # noqa: BLE001
        return None


@router.get("/datastores/{datastore_id}/tables", response_model=TablesOut)
async def list_datastore_tables(
    datastore_id: str = Path(...),
    user: dict[str, Any] = Depends(current_user),
) -> TablesOut:
    """List a datastore's tables (with cheap row counts) for the data browser.

    Org-scoped + authed.  Introspects via ``information_schema`` and adds a
    ``COUNT(*)`` per table.  Returns an empty list (never 500) when the
    connector cannot be introspected.
    """
    connector = await _resolve_datastore_connector(datastore_id, user)
    tables = _introspect_tables(connector)
    out: list[TableInfo] = []
    for t in tables:
        out.append(
            TableInfo(
                name=t["name"],
                schema=t.get("schema"),
                rows=_count_rows(connector, t["name"]),
            )
        )
    return TablesOut(tables=out, datastore_id=datastore_id)


@router.get(
    "/datastores/{datastore_id}/tables/{table}/preview",
    response_model=PreviewOut,
)
async def preview_datastore_table(
    datastore_id: str = Path(...),
    table: str = Path(...),
    limit: int = 50,
    user: dict[str, Any] = Depends(current_user),
) -> PreviewOut:
    """Return the first *limit* rows of *table* as plain JSON (columns + rows).

    The table name is validated against the datastore's introspected table
    list (allowlist) AND against a safe-identifier regex before it is used in
    SQL.  *limit* is clamped to ``[1, 200]``.  Execution reuses the connector
    that POST /query would use for this datastore.
    """
    limit = max(1, min(int(limit or 50), _PREVIEW_MAX_LIMIT))

    connector = await _resolve_datastore_connector(datastore_id, user)
    tables = _introspect_tables(connector)
    known = {t["name"] for t in tables}
    if table not in known or not _safe_ident(table):
        raise AppError(
            "not_found",
            f"Table {table!r} not found in datastore {datastore_id!r}.",
            404,
        )

    plan = PhysicalPlan(
        sql=f"SELECT * FROM {table} LIMIT {limit}",
        params=[],
        cache_key="",
        rls_claims={},
    )
    arrow_table = connector.execute(plan)

    columns = [
        PreviewColumn(name=str(f.name), type=str(f.type))
        for f in arrow_table.schema
    ]
    col_names = [c.name for c in columns]
    data = arrow_table.to_pydict()
    n = arrow_table.num_rows
    rows: list[list[Any]] = []
    for i in range(n):
        rows.append([_jsonable(data[c][i]) for c in col_names])

    total = _count_rows(connector, table)
    return PreviewOut(
        table=table,
        columns=columns,
        rows=rows,
        row_count=total if total is not None else n,
        limit=limit,
        truncated=(total is not None and total > n),
    )


def _jsonable(v: Any) -> Any:
    """Coerce an Arrow scalar to a JSON-serialisable Python value."""
    if v is None:
        return None
    # bigint → int (JSON has no distinct int64); bytes → utf-8 best-effort.
    if isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return repr(v)
    return str(v)
