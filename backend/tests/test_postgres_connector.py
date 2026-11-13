"""PostgresConnector — ADBC DB-API import and streaming.

Regression for the connector Test failing with
``PostgreSQL query failed: module 'adbc_driver_postgresql' has no attribute 'dbapi'``:
the package does not expose ``.dbapi`` until the submodule is imported, so the
connector must ``import adbc_driver_postgresql.dbapi`` explicitly.

The fakes below reproduce that shape exactly — a parent module WITHOUT a
``dbapi`` attribute plus a registered ``adbc_driver_postgresql.dbapi``
submodule — so the old ``adbc_driver_postgresql.dbapi.connect(...)`` pattern
fails here while the fixed import passes. No ADBC wheels or database needed.

The live test runs only with ``RUN_PG_TESTS=1`` and a real ``DATABASE_URL``
(same opt-in as conftest's PG fixtures).
"""

from __future__ import annotations

import os
import sys
import types

import pyarrow as pa
import pytest

from app.connectors.plan import PhysicalPlan
from app.connectors.postgres import PostgresConnector


def _plan(sql: str = "SELECT 1 AS ok") -> PhysicalPlan:
    return PhysicalPlan(
        dialect="postgres",
        sql=sql,
        params=[],
        projection=None,
        predicates=[],
        rls_claims={},
        cache_key="deadbeef" * 8,
    )


class _FakeCursor:
    def __init__(self, table: pa.Table) -> None:
        self._table = table
        self.executed: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetch_arrow_table(self) -> pa.Table:
        return self._table

    def fetch_record_batch(self) -> pa.RecordBatchReader:
        # Real ADBC returns a reader over the whole result, not one batch.
        # An empty batch in the middle must be skipped, not end the stream.
        batches = self._table.to_batches(max_chunksize=1)
        empty = pa.RecordBatch.from_pylist([], schema=self._table.schema)
        return pa.RecordBatchReader.from_batches(
            self._table.schema, [batches[0], empty, *batches[1:]]
        )


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self) -> _FakeCursor:
        return self._cursor


@pytest.fixture
def fake_adbc(monkeypatch):
    """Install fake ADBC modules; the parent has no ``dbapi`` attribute."""
    table = pa.table({"ok": [1, 2, 3]})
    cursor = _FakeCursor(table)
    connected: list[str] = []

    dbapi = types.ModuleType("adbc_driver_postgresql.dbapi")

    def connect(dsn):
        connected.append(dsn)
        return _FakeConn(cursor)

    dbapi.connect = connect
    parent = types.ModuleType("adbc_driver_postgresql")
    # Deliberately NOT `parent.dbapi = dbapi` — that is the real package's
    # state before the submodule is imported.

    monkeypatch.setitem(sys.modules, "adbc_driver_manager", types.ModuleType("adbc_driver_manager"))
    monkeypatch.setitem(sys.modules, "adbc_driver_postgresql", parent)
    monkeypatch.setitem(sys.modules, "adbc_driver_postgresql.dbapi", dbapi)
    return types.SimpleNamespace(parent=parent, cursor=cursor, connected=connected, table=table)


def test_fake_reproduces_missing_dbapi_attribute(fake_adbc):
    # The pre-fix pattern (`import adbc_driver_postgresql` then
    # `adbc_driver_postgresql.dbapi.connect`) raises exactly the user's error.
    import adbc_driver_postgresql

    with pytest.raises(AttributeError, match="dbapi"):
        adbc_driver_postgresql.dbapi  # noqa: B018


def test_execute_imports_dbapi_submodule(fake_adbc):
    conn = PostgresConnector("postgresql://u@h/db")
    out = conn.execute(_plan())
    assert out.equals(fake_adbc.table)
    assert fake_adbc.connected == ["postgresql://u@h/db"]
    assert fake_adbc.cursor.executed == [("SELECT 1 AS ok", None)]


def test_execute_stream_iterates_record_batch_reader(fake_adbc):
    conn = PostgresConnector("postgresql://u@h/db")
    batches = list(conn.execute_stream(_plan()))
    assert all(isinstance(b, pa.RecordBatch) for b in batches)
    assert [b.num_rows for b in batches] == [1, 1, 1]
    assert pa.Table.from_batches(batches).equals(fake_adbc.table)


@pytest.mark.skipif(
    not (os.getenv("RUN_PG_TESTS") and os.getenv("DATABASE_URL")),
    reason="live Postgres test — set RUN_PG_TESTS=1 and DATABASE_URL",
)
def test_live_execute_and_stream():
    pytest.importorskip("adbc_driver_postgresql")
    conn = PostgresConnector(os.environ["DATABASE_URL"])
    assert conn.execute(_plan()).column("ok").to_pylist() == [1]
    rows = sum(b.num_rows for b in conn.execute_stream(_plan()))
    assert rows == 1
