"""Engine-agnostic catalog introspection — the ONE place that lists tables.

Why this module exists
---------------------
Listing a connector's tables was implemented twice (``routes/data_browser.py``
and ``routes/query_tools.py``), and the copies drifted.  Both reached for
``information_schema.tables`` and then read the result with a case-sensitive
``dict.get("table_name")`` — which is correct for DuckDB and Postgres and
silently WRONG for MySQL, Snowflake and Oracle, all of which return result
labels UPPER-CASE.  The lookup missed, the surrounding ``zip()`` produced
nothing, and the endpoint answered ``200 {"tables": []}``: a connector that was
working perfectly looked like it had no tables, with no error anywhere.

Duplication is what let one fix miss the other copy, so both routes now call
this module instead of rolling their own.

The three rules encoded here
----------------------------
1. **Never read a result label case-sensitively** — go through :func:`pick_col`.
2. **Never turn a failure into an empty list** — empty means "queried fine,
   found nothing"; an unreachable database must raise (see
   :func:`introspect_tables`).  Swallowing it makes a dead connector
   indistinguishable from an empty one.
3. **Qualify a table with its schema** when the bare name would resolve against
   the connection's default schema and miss (see :func:`resolve_table_ref`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.connectors.plan import PhysicalPlan
from app.errors import AppError

log = logging.getLogger(__name__)

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Schemas that are engine plumbing, never user data. Compared case-folded so one
# list covers DuckDB/Postgres (`pg_catalog`) and MySQL (`mysql`, `sys`,
# `performance_schema`) without a per-engine branch.
SYSTEM_SCHEMAS = frozenset(
    {
        "information_schema",
        "pg_catalog",
        "pg_toast",
        "mysql",
        "performance_schema",
        "sys",
    }
)


def safe_identifier(name: str) -> bool:
    """Return True if *name* is a safe bare SQL identifier (no injection risk)."""
    return bool(_SAFE_IDENTIFIER_RE.match(str(name))) and len(str(name)) <= 256


def pick_col(d: dict[str, Any], *candidates: str) -> list[Any]:
    """Case-insensitively pull a column out of a ``to_pydict()`` result.

    Engines disagree on the case of result labels: DuckDB and Postgres return
    them lower-case, MySQL / Snowflake / Oracle UPPER-CASE.  Tries each
    candidate exactly first (respecting a genuine case-sensitive collision),
    then case-folded.  Returns ``[]`` when nothing matches.
    """
    for c in candidates:
        if c in d:
            return d[c]
    folded = {k.lower(): v for k, v in d.items()}
    for c in candidates:
        v = folded.get(c.lower())
        if v is not None:
            return v
    return []


def _plan(sql: str) -> PhysicalPlan:
    """A policy-free physical plan — introspection never carries RLS claims."""
    return PhysicalPlan(sql=sql, params=[], cache_key="", rls_claims={})


def introspect_tables(connector: Any) -> list[dict[str, Any]]:
    """Return ``[{"schema": s, "name": n}, …]`` for every user table.

    Works on any connector exposing ``.execute(plan)``.  System schemas are
    filtered client-side rather than in the WHERE clause, so a single query
    serves engines whose system-schema names differ.

    This is a BLOCKING call (it drives the underlying driver), so async callers
    must run it via ``asyncio.to_thread`` — in ``network_mode="bridge"`` the
    event loop also runs the tunnel that serves this query, and blocking it
    deadlocks the very request being served.

    Raises
    ------
    AppError
        When both the ``information_schema`` query and the ``SHOW TABLES``
        fallback fail. An unreachable database is an error, not an empty schema.
    """
    first_error: Exception | None = None
    try:
        rows = connector.execute(
            _plan(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "ORDER BY table_schema, table_name"
            )
        ).to_pydict()
        out = [
            {"schema": s, "name": n}
            for s, n in zip(pick_col(rows, "table_schema"), pick_col(rows, "table_name"))
            if str(s).lower() not in SYSTEM_SCHEMAS
        ]
        if out:
            return out
        log.warning(
            "information_schema.tables returned no usable rows (labels=%s); "
            "falling back to SHOW TABLES",
            list(rows)[:8],
        )
    except Exception as exc:  # noqa: BLE001 — retried via SHOW TABLES below
        first_error = exc
        log.warning("information_schema introspection failed", exc_info=True)

    try:
        d = connector.execute(_plan("SHOW TABLES")).to_pydict()
        # 'name'/'Name' on DuckDB, 'Tables_in_<db>' on MySQL.
        col = next(
            (k for k in d if k.lower() == "name" or k.lower().startswith("tables_in_")),
            None,
        )
        return [{"schema": "main", "name": n} for n in (d.get(col, []) if col else [])]
    except Exception as exc:  # noqa: BLE001 — classified below
        log.warning("SHOW TABLES fallback failed", exc_info=True)
        err = first_error or exc
        if isinstance(err, AppError):
            raise err
        raise AppError(
            "introspection_failed",
            f"Could not list tables for this connector: {err}",
            502,
        ) from err


def resolve_table_ref(
    table: str,
    tables: list[dict[str, Any]],
    schema: str | None = None,
) -> str:
    """Return the SQL reference for *table* — bare, or ``schema.table``.

    A connector can expose many schemas, but browse URLs carry only a bare table
    name.  An unqualified ``SELECT * FROM projects`` resolves against the
    connection's DEFAULT schema and fails outright when the table lives
    elsewhere, so a table that was listed could never be opened.

    Resolution order:

    1. An explicit *schema* matching a listed table → qualify with it.
    2. The name is unique across schemas → qualify with its own schema, so a
       table outside the default schema still opens.
    3. The name is ambiguous → stay unqualified and let the engine's default
       schema win: the historical behaviour, and the least surprising reading of
       a bare name.

    Both parts are re-validated with :func:`safe_identifier`; a schema failing
    validation is ignored rather than interpolated into SQL.
    """
    if schema and safe_identifier(schema):
        if any(t.get("name") == table and t.get("schema") == schema for t in tables):
            return f"{schema}.{table}"
    owners = {t.get("schema") for t in tables if t.get("name") == table}
    if len(owners) == 1:
        only = next(iter(owners))
        if only and safe_identifier(str(only)):
            return f"{only}.{table}"
    return table
