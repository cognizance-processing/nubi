"""Every MySQL query session is read-only and time-limited, whichever driver runs it."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

DSN = "mysql://u:p@db.example.com:3306/app"


class _Plan:
    def __init__(self, sql: str, params: list[Any] | None = None) -> None:
        self.sql = sql
        self.params = params or []


def _statements(value: Any) -> str:
    return " ; ".join(value if isinstance(value, (list, tuple)) else [value or ""]).upper()


def test_arrow_path_sets_up_a_read_only_session(monkeypatch):
    import pyarrow as pa

    from app.connectors import mysql

    seen: dict[str, Any] = {}

    class _Cx:
        @staticmethod
        def read_sql(conn, query, *, return_type="pandas", pre_execution_query=None):
            seen.update(conn=conn, query=query, pre_execution_query=pre_execution_query)
            return pa.table({"x": [1]})

    monkeypatch.setattr(mysql, "_import_connectorx", lambda: _Cx)
    monkeypatch.setattr(mysql.MySQLConnector, "_preflight", lambda self: None)
    mysql.MySQLConnector(DSN).execute(_Plan("SELECT 1"))

    setup = _statements(seen.get("pre_execution_query"))
    assert "TRANSACTION READ ONLY" in setup
    assert "MAX_EXECUTION_TIME" in setup


class _Cursor:
    def __init__(self, log, fail_on=None):
        self.log, self.fail_on = log, fail_on

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, sql, *_a):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("server refused")
        self.log.append(sql)


class _Conn:
    def __init__(self, fail_on=None):
        self.log, self.closed, self.fail_on = [], False, fail_on

    def cursor(self):
        return _Cursor(self.log, self.fail_on)

    def close(self):
        self.closed = True


def _pymysql(conn, captured):
    class _PyMySQL:
        @staticmethod
        def connect(**kwargs):
            captured.update(kwargs)
            return conn

    return _PyMySQL


@pytest.mark.parametrize("cls_name", ["MySQLConnector", "MariaDBConnector"])
def test_bound_parameter_path_applies_each_rule_as_its_own_statement(monkeypatch, cls_name):
    from app.connectors import mariadb, mysql

    cls = getattr(mysql, cls_name, None) or getattr(mariadb, cls_name)
    conn, captured = _Conn(), {}
    monkeypatch.setattr(mysql, "_import_pymysql", lambda: _pymysql(conn, captured))
    assert cls(DSN)._connect_pymysql() is conn
    assert conn.log == list(cls.session_rules)
    assert any("READ ONLY" in rule.upper() for rule in cls.session_rules)
    assert all("," not in rule for rule in cls.session_rules)  # one statement each
    assert "init_command" not in captured


def test_a_session_that_cannot_take_the_rules_is_not_handed_out(monkeypatch):
    from app.connectors import mysql

    conn = _Conn(fail_on="READ ONLY")
    monkeypatch.setattr(mysql, "_import_pymysql", lambda: _pymysql(conn, {}))
    with pytest.raises(RuntimeError):
        mysql.MySQLConnector(DSN)._connect_pymysql()
    assert conn.closed


def test_mariadb_uses_its_own_time_limit_setting():
    from app.connectors.mariadb import MariaDBConnector

    rules = " ".join(MariaDBConnector.session_rules).lower()
    assert "max_statement_time" in rules and "max_execution_time" not in rules


def test_a_driver_without_session_setup_is_not_used(monkeypatch):
    """If the Arrow driver cannot take the session rules, the query runs on the other driver."""
    from app.connectors import mysql

    class _OldCx:
        @staticmethod
        def read_sql(conn, query, return_type="pandas"):  # no pre_execution_query
            raise AssertionError("must not run a query on an unguarded session")

    ran: list[str] = []
    monkeypatch.setattr(mysql, "_import_connectorx", lambda: _OldCx)
    monkeypatch.setattr(mysql.MySQLConnector, "_preflight", lambda self: None)
    monkeypatch.setattr(mysql.MySQLConnector, "_execute_pymysql", lambda self, plan: ran.append(plan.sql) or "ok")
    assert mysql.MySQLConnector(DSN).execute(_Plan("SELECT 1")) == "ok"
    assert ran == ["SELECT 1"]


@pytest.mark.skipif(not os.environ.get("NUBI_TEST_MYSQL_DSN"), reason="needs a live MySQL (NUBI_TEST_MYSQL_DSN)")
def test_live_mysql_sessions_are_read_only_on_both_paths():
    from app.connectors.mysql import MySQLConnector
    from app.errors import AppError

    connector = MySQLConnector(os.environ["NUBI_TEST_MYSQL_DSN"])
    probe = "SELECT @@session.transaction_read_only AS ro, @@session.max_execution_time AS met"
    for plan in (_Plan(probe), _Plan(probe + ", $1 AS x", [1])):
        row = connector.execute(plan).to_pylist()[0]
        assert int(row["ro"]) == 1 and int(row["met"]) > 0
    for plan in (_Plan("CREATE TABLE _nubi_ro_probe (a int)"), _Plan("DELETE FROM mysql.user WHERE user = $1", ["nobody"])):
        with pytest.raises(AppError):
            connector.execute(plan)
