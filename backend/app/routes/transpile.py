"""Public SQL transpile endpoint.

POST /api/v1/transpile
    Body: {sql: str, from_dialect: str, to_dialect: str}
    Response: {sql: str}

Uses sqlglot (same engine as the planner) to translate SQL between dialects.
Pure transform — no execution, no data access, no FS/network.
Auth required (any valid token). No org scoping needed (data-free).
"""
from __future__ import annotations

from typing import Any

import sqlglot
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import current_user
from app.errors import AppError
from app.routes import api_router

router = APIRouter(prefix="/transpile", tags=["transpile"])

# Allowlist: dialects supported by the connector layer + sqlglot
ALLOWED_DIALECTS: frozenset[str] = frozenset(
    {
        "duckdb",
        "bigquery",
        "postgres",
        "postgresql",  # alias for postgres
        "snowflake",
        "trino",
        "databricks",
        "clickhouse",
        "spark",
        "spark2",
        "mysql",
        "sqlite",
        "hive",
        "presto",
        "redshift",
        "tsql",
        "drill",
        "oracle",
    }
)


class TranspileIn(BaseModel):
    sql: str
    from_dialect: str
    to_dialect: str


@router.post("", status_code=200)
async def transpile_sql(
    body: TranspileIn,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Transpile SQL from one dialect to another using sqlglot.

    Validates dialect against an allowlist (400 on unknown dialect).
    Surfaces parse/transpile errors as 400.
    No data access — pure AST transform.
    """
    from_d = body.from_dialect.strip().lower()
    to_d = body.to_dialect.strip().lower()

    if from_d not in ALLOWED_DIALECTS:
        raise AppError(
            "unknown_dialect",
            f"Unknown from_dialect {body.from_dialect!r}. Allowed: {sorted(ALLOWED_DIALECTS)}",
            400,
        )
    if to_d not in ALLOWED_DIALECTS:
        raise AppError(
            "unknown_dialect",
            f"Unknown to_dialect {body.to_dialect!r}. Allowed: {sorted(ALLOWED_DIALECTS)}",
            400,
        )

    sql_in = body.sql.strip()
    if not sql_in:
        raise AppError("bad_request", "sql must not be empty.", 400)

    # Map allowlisted aliases to the names sqlglot actually recognises
    # (e.g. "postgresql" → "postgres") so an accepted dialect never 400s deep
    # inside sqlglot with an opaque "Unknown dialect" error.
    _alias = {"postgresql": "postgres"}
    try:
        result = sqlglot.transpile(
            sql_in, read=_alias.get(from_d, from_d), write=_alias.get(to_d, to_d), pretty=False
        )
    except sqlglot.errors.SqlglotError as exc:
        raise AppError("parse_error", f"SQL parse/transpile error: {exc}", 400)
    except Exception as exc:  # noqa: BLE001
        raise AppError("parse_error", f"Unexpected transpile error: {exc}", 400)

    if not result:
        raise AppError("parse_error", "Transpile produced no output.", 400)

    return {"sql": result[0]}


api_router.include_router(router)
