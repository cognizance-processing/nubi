"""A direct database connection dials the address that was checked.

The host a connector names is resolved once; the driver is then handed that
address, so a second lookup at connect time cannot land somewhere else.
"""

from __future__ import annotations

import asyncio
import socket
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

PUBLIC = "93.184.216.34"
PUBLIC_2 = "93.184.216.35"
INTERNAL = "127.0.0.1"


@pytest.fixture(autouse=True)
def _no_escape_hatch(monkeypatch):
    monkeypatch.delenv("NUBI_SSRF_ALLOW_PRIVATE", raising=False)


@pytest.fixture()
def rebinding_dns(monkeypatch):
    """Names resolve to a public address the first time and an internal one afterwards."""
    calls: dict[str, int] = {}
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        try:
            import ipaddress

            ipaddress.ip_address(host)
            return real(host, *args, **kwargs)
        except ValueError:
            pass
        if host.endswith(".invalid"):
            raise socket.gaierror("no such host")
        n = calls[host] = calls.get(host, 0) + 1
        ip = (PUBLIC_2 if host.startswith("second.") else PUBLIC) if n == 1 else INTERNAL
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return calls


def test_postgres_parts_are_pinned_with_hostaddr(rebinding_dns):
    from app.connectors.registry import postgres_dsn
    from app.connectors.ssrf import pin_connector_target

    cfg = {"connector_type": "postgres", "host": "db.example.test", "port": 5432, "user": "u", "database": "app"}
    pin_connector_target(cfg)
    parts = urlsplit(postgres_dsn(cfg))
    assert parts.hostname == "db.example.test"  # kept for TLS verification
    assert parse_qs(parts.query)["hostaddr"] == [PUBLIC]
    assert rebinding_dns["db.example.test"] == 1  # resolved exactly once


def test_postgres_multi_host_list_is_pinned_entry_by_entry(rebinding_dns):
    from app.connectors.registry import postgres_dsn
    from app.connectors.ssrf import pin_connector_target

    cfg = {"connector_type": "postgres", "host": "db.example.test,second.example.test"}
    pin_connector_target(cfg)
    assert parse_qs(urlsplit(postgres_dsn(cfg)).query)["hostaddr"] == [f"{PUBLIC},{PUBLIC_2}"]


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@db.example.test:5432/app?sslmode=require",
        "postgresql://u:p@db.example.test/app",
        "host=db.example.test dbname=app user=u",
    ],
)
def test_postgres_connection_strings_are_pinned(rebinding_dns, dsn):
    from app.connectors.registry import postgres_dsn
    from app.connectors.ssrf import pin_connector_target

    cfg = {"connector_type": "postgres", "dsn": dsn}
    pin_connector_target(cfg)
    out = postgres_dsn(cfg)
    assert f"hostaddr={PUBLIC}" in out and "db.example.test" in out
    if "sslmode" in dsn:
        assert "sslmode=require" in out


@pytest.mark.parametrize("ctype", ["mysql", "mariadb"])
def test_mysql_dials_the_checked_address(rebinding_dns, ctype):
    from app.connectors.registry import _mysql_dsn_from_config
    from app.connectors.ssrf import pin_connector_target

    cfg = {"connector_type": ctype, "host": "db.example.test", "port": 3306, "user": "u", "password": "p@ss", "database": "app"}
    pin_connector_target(cfg)
    parts = urlsplit(_mysql_dsn_from_config(cfg))
    assert parts.hostname == PUBLIC and parts.port == 3306

    cfg = {"connector_type": ctype, "dsn": "mysql://u:p%40ss@db.example.test:3307/app"}
    rebinding_dns.clear()
    pin_connector_target(cfg)
    parts = urlsplit(_mysql_dsn_from_config(cfg))
    assert parts.hostname == PUBLIC and parts.port == 3307 and parts.password == "p%40ss"


@pytest.mark.parametrize(
    "cfg",
    [
        {"connector_type": "postgres", "host": "gone.invalid"},
        {"connector_type": "mysql", "host": "gone.invalid"},
        {"connector_type": "postgres", "dsn": "postgresql://u@gone.invalid/app"},
        {"connector_type": "postgres", "dsn": "postgresql://u@db.example.test/app?host=second.example.test"},
    ],
    ids=["pg-unresolvable", "mysql-unresolvable", "dsn-unresolvable", "two-host-sources"],
)
def test_a_target_that_cannot_be_pinned_is_refused(rebinding_dns, cfg):
    from app.connectors.registry import postgres_dsn
    from app.connectors.ssrf import pin_connector_target
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        pin_connector_target(cfg)
        postgres_dsn(cfg)
    assert exc.value.status == 400


def test_a_stored_hostaddr_is_replaced_by_the_checked_address(rebinding_dns):
    from app.connectors.ssrf import pin_connector_target

    cfg = {"connector_type": "postgres", "host": "db.example.test", "hostaddr": "10.0.0.5"}
    pin_connector_target(cfg)
    assert cfg["hostaddr"] == PUBLIC


def test_internal_targets_are_still_refused(rebinding_dns):
    from app.connectors.ssrf import pin_connector_target
    from app.errors import AppError

    for cfg in ({"connector_type": "postgres", "host": "127.0.0.1"}, {"connector_type": "mysql", "host": "10.0.0.5"}):
        with pytest.raises(AppError):
            pin_connector_target(cfg)


def test_direct_mode_resolution_pins(rebinding_dns):
    from app.connectors.network import resolve_network

    cfg = {"connector_type": "postgres", "host": "db.example.test", "port": 5432}
    resolve_network(cfg)
    assert cfg["hostaddr"] == PUBLIC
    cfg = {"connector_type": "mysql", "host": "db.example.test", "port": 3306}
    rebinding_dns.clear()
    assert resolve_network(cfg).host == PUBLIC


def test_a_caller_supplied_hostaddr_must_be_an_address():
    from app.connectors.registry import postgres_dsn
    from app.errors import AppError

    with pytest.raises(AppError):
        postgres_dsn({"host": "db.example.com", "hostaddr": "internal.example.com"})
    with pytest.raises(AppError):
        postgres_dsn({"host": "db.example.com", "hostaddr": "8.8.8.8&host=/tmp"})


class _Broker:
    def is_connected(self, _bridge_id):
        return True

    async def open_tcp_proxy(self, _bridge_id, _host, _port):
        return "127.0.0.1", 40001

    async def close_tcp_proxy(self, *_a):
        return None


@pytest.mark.parametrize("key", ["dsn", "connection_string", "jdbc_url", "url", "uri", "base_url"])
def test_a_bridged_connection_cannot_carry_its_own_connection_string(monkeypatch, key):
    import app.bridges.broker as broker_mod
    from app.connectors.network import resolve_network_async
    from app.errors import AppError

    monkeypatch.setattr(broker_mod, "get_broker", lambda: _Broker())
    cfg = {"network_mode": "bridge", "bridge_id": "b1", "host": "db.internal", "port": 5432, key: "postgresql://u@10.0.0.5/app"}
    with pytest.raises(AppError) as exc:
        asyncio.run(resolve_network_async(cfg, {"id": "b1"}))
    assert exc.value.status == 400


def test_a_bridged_connection_dials_only_the_tunnel(monkeypatch):
    import app.bridges.broker as broker_mod
    from app.connectors.network import apply_network_target, resolve_network_async
    from app.connectors.registry import postgres_dsn

    monkeypatch.setattr(broker_mod, "get_broker", lambda: _Broker())
    cfg = {"network_mode": "bridge", "bridge_id": "b1", "host": "db.internal", "port": 5432, "hostaddr": "10.0.0.5"}
    target = asyncio.run(resolve_network_async(cfg, {"id": "b1"}))
    apply_network_target(cfg, target)
    parts = urlsplit(postgres_dsn(cfg))
    assert (parts.hostname, parts.port, parts.query) == ("127.0.0.1", 40001, "")


@pytest.mark.parametrize("mode", ["bridge", "psc", "ssh_tunnel", "anything"])
def test_paths_without_a_tunnel_refuse_other_network_modes(mode):
    from app.connectors.network import pin_direct_target
    from app.errors import AppError

    cfg = {"connector_type": "postgres", "host": "8.8.8.8", "network_mode": mode}
    with pytest.raises(AppError):
        pin_direct_target({"config": cfg}, cfg)
    with pytest.raises(AppError):
        pin_direct_target({"network_mode": mode}, {"connector_type": "postgres", "host": "8.8.8.8"})
    ok = {"connector_type": "postgres", "host": "8.8.8.8"}
    pin_direct_target({}, ok)
    assert ok["hostaddr"] == "8.8.8.8"


def test_connection_paths_use_the_pinning_guard():
    """Every place that builds a direct connection goes through pin_connector_target."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p.name != "ssrf.py" and "guard_connector_target(" in p.read_text()
    ]
    assert offenders == []
    for rel in ("dashboards/board_data.py", "flows/registry.py"):
        assert "pin_direct_target(" in (root / rel).read_text()
    for rel in ("routes/query.py", "connectors/resolve.py"):
        text = (root / rel).read_text()
        assert 'cfg["host"] = _target.host' not in text and "apply_network_target(" in text


# ---------------------------------------------------------------------------
# An address a connection string already names is checked like any other
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@8.8.8.8:5432/db?hostaddr=127.0.0.1",
        "postgresql://u:p@db.example.test/db?hostaddr=10.0.0.5",
        "postgresql://u:p@db.example.test/db?sslmode=require&hostaddr=169.254.169.254",
        "postgresql://u:p@db.example.test/db?hostaddr=8.8.8.8,127.0.0.1",
        "postgresql://u:p@db.example.test/db?HOSTADDR=127.0.0.1",
        "host=8.8.8.8 hostaddr=10.0.0.5 dbname=x",
        "host=8.8.8.8 hostaddr='127.0.0.1' dbname=x",
    ],
)
def test_a_connection_string_cannot_name_an_internal_address_to_dial(rebinding_dns, dsn):
    from app.connectors.ssrf import pin_connector_target
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        pin_connector_target({"connector_type": "postgres", "dsn": dsn})
    assert exc.value.status == 400


def test_a_public_address_in_a_connection_string_is_still_accepted(rebinding_dns):
    from app.connectors.ssrf import pin_connector_target

    cfg = {"connector_type": "postgres", "dsn": "postgresql://u:p@db.example.test/db?hostaddr=93.184.216.34"}
    pin_connector_target(cfg)
    assert cfg["dsn"].endswith("hostaddr=93.184.216.34")


@pytest.mark.parametrize(
    "extra",
    [
        {"port": "3306@127.0.0.1:3307"},
        {"port": "3306/x?y=@127.0.0.1"},
        {"port": "abc"},
        {"port": 0},
        {"port": 70000},
        {"database": "db@127.0.0.1:3307/x"},
        {"database": "db?socket=/var/run/mysqld/mysqld.sock"},
        {"database": "a/b#c"},
    ],
    ids=lambda e: next(iter(e)) + ":" + str(next(iter(e.values())))[:12],
)
def test_mysql_connection_string_parts_cannot_move_the_target(extra):
    from app.connectors.registry import _mysql_dsn_from_config
    from app.errors import AppError

    cfg = {"connector_type": "mysql", "host": "8.8.8.8", "port": 3306, "user": "u", "password": "p", "database": "d", **extra}
    try:
        dsn = _mysql_dsn_from_config(cfg)
    except AppError as exc:
        assert exc.status == 400
        return
    parts = urlsplit(dsn)
    assert parts.hostname == "8.8.8.8" and parts.port == 3306
    assert parts.query == "" and parts.fragment == "" and parts.path.count("/") == 1


def test_mysql_connection_string_keeps_ordinary_values():
    from app.connectors.registry import _mysql_dsn_from_config

    parts = urlsplit(_mysql_dsn_from_config(
        {"host": "8.8.8.8", "port": "3307", "user": "u", "password": "p@ss", "database": "sales_2026"}
    ))
    assert (parts.hostname, parts.port, parts.path) == ("8.8.8.8", 3307, "/sales_2026")
