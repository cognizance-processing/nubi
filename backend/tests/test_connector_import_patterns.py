"""Connector import-pattern smoke tests (no drivers, no warehouses).

Follow-up to the Postgres bug where ``import adbc_driver_postgresql`` was
followed by ``adbc_driver_postgresql.dbapi.connect(...)`` — a submodule that
the package does not expose until it is imported. The deep Postgres regression
lives in ``test_postgres_connector.py``; this file covers the neighbours:

- Trino — ``trino.dbapi`` / ``trino.auth`` are imported explicitly, so the
  connector works whether or not the parent package re-exports them (upstream
  ``trino/__init__.py`` does; both shapes are tested).
- BigQuery — a different pattern (``from google.cloud import bigquery``); the
  fakes stand in for ``Client`` / ``QueryJobConfig`` / ``ScalarQueryParameter``.
- Postgres wire aliases (redshift / cockroachdb / cloudsql) resolve to
  ``PostgresConnector`` through the registry.
- A static guard that ``postgres.py`` keeps the explicit dbapi import.

All drivers are faked via ``monkeypatch.setitem(sys.modules, ...)``.
"""

from __future__ import annotations

import pathlib
import sys
import types

import pyarrow as pa
import pytest

from app.connectors.plan import PhysicalPlan
from app.errors import AppError


def _plan(sql: str = "SELECT 1 AS ok", params: list | None = None) -> PhysicalPlan:
    return PhysicalPlan(
        dialect="postgres",
        sql=sql,
        params=params or [],
        projection=None,
        predicates=[],
        rls_claims={},
        cache_key="deadbeef" * 8,
    )


# ---------------------------------------------------------------------------
# Trino
# ---------------------------------------------------------------------------


class _FakeTrinoCursor:
    description = [("ok", None, None, None, None, None, None)]

    def __init__(self) -> None:
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return [(1,), (2,)]

    def close(self):
        pass


class _FakeTrinoConn:
    def __init__(self, cursor: _FakeTrinoCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _install_fake_trino(monkeypatch, *, expose_submodules: bool):
    cursor = _FakeTrinoCursor()
    conn = _FakeTrinoConn(cursor)
    calls: list[dict] = []

    dbapi = types.ModuleType("trino.dbapi")

    def connect(**kwargs):
        calls.append(kwargs)
        return conn

    dbapi.connect = connect

    auth = types.ModuleType("trino.auth")

    class BasicAuthentication:
        def __init__(self, user, password):
            self.user, self.password = user, password

    auth.BasicAuthentication = BasicAuthentication

    parent = types.ModuleType("trino")
    if expose_submodules:
        # Upstream trino/__init__.py does `from . import auth, dbapi, ...`.
        parent.dbapi = dbapi
        parent.auth = auth
    monkeypatch.setitem(sys.modules, "trino", parent)
    monkeypatch.setitem(sys.modules, "trino.dbapi", dbapi)
    monkeypatch.setitem(sys.modules, "trino.auth", auth)
    return types.SimpleNamespace(cursor=cursor, conn=conn, calls=calls)


_TRINO_CFG = {"host": "trino.example.test", "user": "nubi", "catalog": "hive", "schema": "default"}


def test_trino_execute_with_upstream_shaped_package(monkeypatch):
    from app.connectors.trino import TrinoConnector

    fake = _install_fake_trino(monkeypatch, expose_submodules=True)
    out = TrinoConnector(_TRINO_CFG).execute(_plan("SELECT $1 AS ok", [1]))

    assert out.column("ok").to_pylist() == [1, 2]
    assert fake.cursor.executed == [("SELECT ? AS ok", [1])]
    assert fake.calls[0]["host"] == "trino.example.test"
    assert fake.calls[0]["catalog"] == "hive"
    assert "auth" not in fake.calls[0]
    assert fake.conn.closed


def test_trino_without_parent_dbapi_attribute(monkeypatch):
    # Regression: `import trino` + `trino.dbapi.connect` failed here with
    # "module 'trino' has no attribute 'dbapi'" (wrapped as AppError).
    from app.connectors.trino import TrinoConnector

    fake = _install_fake_trino(monkeypatch, expose_submodules=False)
    out = TrinoConnector(_TRINO_CFG).execute(_plan())

    assert out.column("ok").to_pylist() == [1, 2]
    assert fake.conn.closed


def test_trino_password_uses_basic_auth_without_parent_attributes(monkeypatch):
    from app.connectors.trino import TrinoConnector

    fake = _install_fake_trino(monkeypatch, expose_submodules=False)
    TrinoConnector({**_TRINO_CFG, "password": "not-a-secret"}).execute(_plan())

    auth = fake.calls[0]["auth"]
    assert type(auth).__name__ == "BasicAuthentication"
    assert (auth.user, auth.password) == ("nubi", "not-a-secret")


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------


def _install_fake_bigquery(monkeypatch, table: pa.Table):
    created: list[dict] = []
    queries: list[tuple] = []

    class _RowIterator:
        def to_arrow(self, **kwargs):
            return table

    class _Job:
        def result(self):
            return _RowIterator()

    class Client:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.closed = False

        def query(self, sql, job_config=None):
            queries.append((sql, job_config))
            return _Job()

        def close(self):
            self.closed = True

    class QueryJobConfig:
        def __init__(self, query_parameters=None, **kwargs):
            self.query_parameters = query_parameters or []

    class ScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name, self.type_, self.value = name, type_, value

    bigquery = types.ModuleType("google.cloud.bigquery")
    bigquery.Client = Client
    bigquery.QueryJobConfig = QueryJobConfig
    bigquery.ScalarQueryParameter = ScalarQueryParameter

    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    google.cloud = cloud
    cloud.bigquery = bigquery
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery)
    return types.SimpleNamespace(created=created, queries=queries)


def test_bigquery_execute_with_fake_client(monkeypatch):
    from app.connectors.bigquery import BigQueryConnector

    table = pa.table({"ok": [1]})
    fake = _install_fake_bigquery(monkeypatch, table)
    out = BigQueryConnector({"project": "p"}).execute(_plan("SELECT $1 AS ok", [1]))

    assert out.equals(table)
    assert fake.created == [{"project": "p", "location": None}]
    sql, job_config = fake.queries[0]
    assert sql == "SELECT ? AS ok"
    [param] = job_config.query_parameters
    assert (param.name, param.value) == (None, 1)


def test_bigquery_missing_driver_is_driver_unavailable(monkeypatch):
    # Kept separate from the Postgres dbapi class of bug: a broken
    # `_import_bigquery` surfaces as driver_unavailable, not AttributeError.
    from app.connectors.bigquery import BigQueryConnector

    # Fake `google.cloud` as a plain module (no `bigquery` attribute, no
    # `__path__`) and drop any cached submodule, so the import raises
    # ImportError whether or not the real package was loaded earlier in the
    # session. (A `None` sys.modules entry is not enough: on 3.13 the import
    # then yields None instead of raising.)
    google = types.ModuleType("google")
    google.cloud = types.ModuleType("google.cloud")
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", google.cloud)
    monkeypatch.delitem(sys.modules, "google.cloud.bigquery", raising=False)
    with pytest.raises(AppError) as exc_info:
        BigQueryConnector({"project": "p"}).execute(_plan())
    assert exc_info.value.code == "driver_unavailable"


# ---------------------------------------------------------------------------
# Postgres wire aliases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ctype", ["redshift", "cockroachdb", "cloudsql"])
def test_postgres_wire_aliases_use_postgres_connector(ctype):
    from app.connectors.postgres import PostgresConnector
    from app.connectors.registry import _pg_dsn_from_config, get_connector_registry

    cfg = {"host": f"{ctype}.example.test", "port": 5439, "database": "analytics", "user": "nubi", "sslmode": "require"}
    conn = get_connector_registry().get(ctype)(cfg)

    assert isinstance(conn, PostgresConnector)
    assert conn._dsn == _pg_dsn_from_config(cfg)
    assert conn._dsn == f"postgresql://nubi@{ctype}.example.test:5439/analytics?sslmode=require"


# ---------------------------------------------------------------------------
# Static guard
# ---------------------------------------------------------------------------


def test_postgres_connector_imports_dbapi_submodule_explicitly():
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "connectors" / "postgres.py").read_text(
        encoding="utf-8"
    )
    assert "import adbc_driver_postgresql.dbapi as pg_dbapi" in src
    assert "adbc_driver_postgresql.dbapi.connect" not in src
