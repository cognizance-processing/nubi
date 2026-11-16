"""First-contact pre-flight on the MySQL connector.

connectorx's connection pool retries a *fatal* authentication error for thirty
seconds and then reports "timed out waiting for connection", burying the real
cause. PyMySQL reports ``Access denied`` in milliseconds. The connector opens one
cheap PyMySQL connection the first time this process sees a DSN so a credential
or reachability problem is reported immediately and truthfully.
"""

from __future__ import annotations

import pytest

from app.connectors.mysql import MySQLConnector, _PREFLIGHT_OK
from app.errors import AppError

DSN = "mysql://u:p@127.0.0.1:3306/db"


# The connector matches the driver error by class NAME (it must not import
# pymysql just to catch it), so a stand-in with the right name is enough.
OperationalError = type("OperationalError", (Exception,), {})


@pytest.fixture(autouse=True)
def _clear_cache():
    _PREFLIGHT_OK.clear()
    yield
    _PREFLIGHT_OK.clear()


def _connector(monkeypatch, connect):
    c = MySQLConnector(DSN)
    monkeypatch.setattr(MySQLConnector, "_connect_pymysql", lambda self: connect())
    return c


def test_bad_credentials_fail_immediately_with_the_engines_own_message(monkeypatch):
    def connect():
        raise OperationalError(
            "(1045, \"Access denied for user 'u'@'localhost' (using password: YES)\")"
        )

    c = _connector(monkeypatch, connect)
    with pytest.raises(AppError) as exc:
        c._preflight()
    assert "Access denied" in str(exc.value)
    assert DSN not in _PREFLIGHT_OK


def test_a_healthy_dsn_is_checked_once_then_cached(monkeypatch):
    calls = {"n": 0}

    class _Conn:
        def close(self):
            pass

    def connect():
        calls["n"] += 1
        return _Conn()

    c = _connector(monkeypatch, connect)
    c._preflight()
    c._preflight()
    MySQLConnector(DSN)._preflight()  # a fresh instance for the same DSN
    assert calls["n"] == 1, "pre-flight must cost one connect per DSN, not per query"
    assert DSN in _PREFLIGHT_OK


def test_an_unexpected_preflight_failure_never_blocks_a_query(monkeypatch):
    """A missing driver must not turn a working query into an error."""

    def connect():
        raise RuntimeError("pymysql not installed")

    c = _connector(monkeypatch, connect)
    c._preflight()  # must not raise — connectorx still gets its chance
    assert DSN not in _PREFLIGHT_OK
