"""Conformance suite fixtures — seed a deterministic DuckDB connector.

The seeded connector is the sole data source for all conformance cases.
It uses two tables:

``users(id int32, tenant_id string, name string, age int32)``
    6 rows across 2 tenants: ``acme`` (ids 1–3) and ``globex`` (ids 4–6).

``orders(id int32, tenant_id string, amount float64)``
    5 rows: 3 belong to ``acme`` (ids 1, 2, 5) and 2 to ``globex`` (ids 3, 4).

The data is fixed Python literals so the suite is reproducible without
any network I/O or external files.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from app.connectors.duckdb_conn import DuckDBConnector


# ---------------------------------------------------------------------------
# Seed data (frozen literals)
# ---------------------------------------------------------------------------

_USERS = pa.table(
    {
        "id": pa.array([1, 2, 3, 4, 5, 6], type=pa.int32()),
        "tenant_id": pa.array(
            ["acme", "acme", "acme", "globex", "globex", "globex"],
            type=pa.string(),
        ),
        "name": pa.array(
            ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank"],
            type=pa.string(),
        ),
        "age": pa.array([30, 25, 35, 28, 42, 31], type=pa.int32()),
    }
)

_ORDERS = pa.table(
    {
        "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
        "tenant_id": pa.array(
            ["acme", "acme", "globex", "globex", "acme"],
            type=pa.string(),
        ),
        "amount": pa.array([99.99, 149.50, 200.00, 75.25, 50.00], type=pa.float64()),
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_connector() -> DuckDBConnector:
    """Return a DuckDBConnector pre-loaded with the deterministic seed dataset.

    Scoped to the module so the DuckDB connection is created once and shared
    across all conformance tests in the same file (read-only workload — safe).
    """
    conn = DuckDBConnector()
    conn.register({"users": _USERS, "orders": _ORDERS})
    return conn
