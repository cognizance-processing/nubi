"""Connectors cannot be pointed at the platform's own network."""

from __future__ import annotations

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

INTERNAL = [
    {"host": "127.0.0.1", "port": 5432},
    {"host": "localhost", "port": 5432},
    {"host": "10.0.0.5"},
    {"host": "192.168.1.10"},
    {"host": "169.254.169.254"},
    {"host": "[::1]"},
    {"host": "0.0.0.0"},
    {"dsn": "postgresql://u:p@127.0.0.1:5432/nubi"},
    {"dsn": "mysql://u:p@10.1.2.3/db"},
    {"url": "http://169.254.169.254/latest/meta-data/"},
    {"host": "db.example.com", "dsn": "postgresql://u:p@localhost/x"},
]


@pytest.fixture(autouse=True)
def _no_escape_hatch(monkeypatch):
    monkeypatch.delenv("NUBI_SSRF_ALLOW_PRIVATE", raising=False)


@pytest.mark.parametrize("cfg", INTERNAL, ids=[str(next(iter(c.values()))) for c in INTERNAL])
def test_direct_mode_refuses_internal_targets(cfg):
    from app.connectors.network import resolve_network
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        resolve_network({"connector_type": "postgres", **cfg})
    assert exc.value.status == 400
    # Generic text: no resolved address, no hint about what is listening.
    for leak in ("127.0.0.1", "10.0.0.5", "169.254", "refused", "resolves"):
        assert leak not in exc.value.message


def test_public_targets_and_the_operator_flag_still_work(monkeypatch):
    from app.connectors.network import resolve_network

    assert resolve_network({"host": "8.8.8.8", "port": 5432}).host == "8.8.8.8"
    monkeypatch.setenv("NUBI_SSRF_ALLOW_PRIVATE", "true")
    assert resolve_network({"host": "127.0.0.1", "port": 5432}).host == "127.0.0.1"
    # ...but link-local / metadata stay closed even then.
    from app.errors import AppError

    with pytest.raises(AppError):
        resolve_network({"host": "169.254.169.254"})


def test_tenant_supplied_object_storage_endpoint_follows_the_same_policy():
    from app.connectors.ssrf import guard_connector_endpoint
    from app.errors import AppError

    for endpoint in ("127.0.0.1:9000", "http://10.0.0.9:9000", "169.254.169.254"):
        with pytest.raises(AppError):
            guard_connector_endpoint(endpoint)
    guard_connector_endpoint("8.8.8.8:9000")


def test_default_duckdb_connector_cannot_reach_the_host(tmp_path):
    from app.connectors.duckdb_conn import DuckDBConnector
    from app.connectors.plan import PhysicalPlan

    connector = DuckDBConnector()
    for sql in ("SELECT * FROM read_csv_auto('/etc/hostname')", f"COPY (SELECT 1) TO '{tmp_path}/x.csv'"):
        with pytest.raises(Exception):
            connector.execute(PhysicalPlan(sql=sql, params=[], cache_key="", rls_claims={}))
    assert not (tmp_path / "x.csv").exists()


def test_flow_query_tasks_use_the_shared_datastore_opener():
    import inspect

    from app.flows import registry

    src = inspect.getsource(registry)
    assert "open_duckdb_readonly(" not in src
    assert "connect_duckdb_datastore" in src
