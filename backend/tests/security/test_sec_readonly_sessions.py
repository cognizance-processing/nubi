"""Warehouse sessions opened for queries are read-only and time-limited."""

from __future__ import annotations

import os
import sys
import types

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)


def _plan(sql="SELECT 1"):
    from app.connectors.plan import PhysicalPlan

    return PhysicalPlan(sql=sql, params=[], cache_key="", rls_claims={})


class _Cursor:
    def __init__(self, log):
        self.log = log
        self.description = [("one",)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)

    def fetch_arrow_table(self):
        import pyarrow as pa

        return pa.table({"one": [1]})

    def fetch_record_batch(self):
        return None

    def fetchall(self):
        return [(1,)]


class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _Cursor(self.log)

    def close(self):
        pass


def test_postgres_sessions_are_read_only_with_a_timeout(monkeypatch):
    from app.connectors import postgres

    log: list[str] = []
    fake = types.ModuleType("adbc_driver_postgresql.dbapi")
    fake.connect = lambda dsn: _Conn(log)  # type: ignore[attr-defined]
    pkg = types.ModuleType("adbc_driver_postgresql")
    pkg.dbapi = fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "adbc_driver_postgresql", pkg)
    monkeypatch.setitem(sys.modules, "adbc_driver_postgresql.dbapi", fake)
    monkeypatch.setattr(postgres, "_import_adbc", lambda: None)

    connector = postgres.PostgresConnector("postgresql://u:p@db.example.com/x")
    connector.execute(_plan("SELECT 42"))
    joined = " | ".join(log).lower()
    assert "read only" in joined or "read_only" in joined
    assert "statement_timeout" in joined
    assert log.index("SELECT 42") == len(log) - 1  # guards run before the query

    log.clear()
    list(connector.execute_stream(_plan("SELECT 43")))
    joined = " | ".join(log).lower()
    assert ("read only" in joined or "read_only" in joined) and "statement_timeout" in joined


def test_mysql_parameterised_sessions_are_read_only_with_a_timeout(monkeypatch):
    from app.connectors import mysql

    ran: list[str] = []

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql, *_a):
            ran.append(sql.lower())

    class _Session:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    class _PyMySQL:
        @staticmethod
        def connect(**_kwargs):
            return _Session()

    monkeypatch.setattr(mysql, "_import_pymysql", lambda: _PyMySQL)
    connector = mysql.MySQLConnector("mysql://u:p@db.example.com/x")
    connector._connect_pymysql()
    assert any("read only" in sql for sql in ran) and any("max_execution_time" in sql for sql in ran)


@pytest.mark.skipif(not os.environ.get("NUBI_TEST_PG_DSN"), reason="needs a live Postgres (NUBI_TEST_PG_DSN)")
def test_live_postgres_refuses_writes():
    from app.connectors.postgres import PostgresConnector
    from app.errors import AppError

    connector = PostgresConnector(os.environ["NUBI_TEST_PG_DSN"])
    assert connector.execute(_plan("SELECT 1 AS one")).num_rows == 1
    with pytest.raises(AppError):
        connector.execute(_plan("CREATE TABLE _nubi_ro_probe (a int)"))
    with pytest.raises(AppError):
        connector.execute(_plan("SELECT pg_sleep(600)"))
