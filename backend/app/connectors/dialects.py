"""Connector-type → sqlglot-dialect mapping (shared, vendor-neutral).

Maps the ``connector_type`` (or ``type``) string stored in a datastore's
config dict to the sqlglot dialect name used for SQL generation.  Both the
flow query path (``app.flows.registry``) and the ad-hoc query route
(``app.routes.query``) consult this map so that sqlglot emits warehouse-native
SQL — most importantly, safe-cast forms (``TRY_CAST`` / ``SAFE_CAST``) survive
to the target engine instead of being downgraded to a plain ``CAST`` by a
hardcoded ``postgres`` dialect.

``postgres`` is the default for unknown / absent connector types, matching the
historical planner default (``planner._DEFAULT_DIALECT``).
"""

from __future__ import annotations

# The default sqlglot dialect — mirrors ``planner._DEFAULT_DIALECT``.
DEFAULT_DIALECT: str = "postgres"

CONNECTOR_DIALECT: dict[str, str] = {
    "postgres": "postgres",
    "redshift": "postgres",
    "cockroachdb": "postgres",
    "cloudsql": "postgres",
    "duckdb": "duckdb",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlserver": "tsql",
    "azuresql": "tsql",
    "azuresynapse": "tsql",
    "oracle": "oracle",
    "clickhouse": "clickhouse",
    "trino": "trino",
    "presto": "presto",
    "athena": "trino",
    "databricks": "databricks",
    "http_json": "postgres",
    "jdbc": "postgres",
}


def dialect_for(ctype: str | None) -> str:
    """Return the sqlglot dialect for a connector-type string.

    Unknown or empty connector types fall back to ``DEFAULT_DIALECT``
    (``"postgres"``) so behaviour matches the historical planner default.
    """
    return CONNECTOR_DIALECT.get(ctype or "", DEFAULT_DIALECT)
