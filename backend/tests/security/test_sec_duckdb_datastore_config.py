"""A duckdb datastore config can only expose allowed parquet files as views."""

from __future__ import annotations

import ast
import os
import pathlib
import uuid

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

ORG = str(uuid.uuid4())
OTHER_ORG = str(uuid.uuid4())
APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "app"


@pytest.fixture(autouse=True)
def _no_extra_data_dirs(monkeypatch):
    """The suite-wide tmp allowance must not apply to these checks."""
    monkeypatch.delenv("NUBI_DUCKDB_DATA_DIRS", raising=False)


@pytest.fixture(autouse=True)
def _secret_store():
    from app.connectors.secret_store import InMemorySecretStore, set_secret_store_for_tests

    set_secret_store_for_tests(InMemorySecretStore())
    yield
    set_secret_store_for_tests(None)


def _plan(sql: str = "SELECT 1"):
    from app.connectors.plan import PhysicalPlan

    return PhysicalPlan(sql=sql, params=[], cache_key="", rls_claims={})


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def _hostile_view_sql(canary: pathlib.Path) -> str:
    return ";\n".join(
        [
            "CREATE VIEW env AS SELECT * FROM read_text('/proc/self/environ')",
            "CREATE VIEW host AS SELECT * FROM read_csv_auto('/etc/hostname')",
            "CREATE VIEW host2 AS SELECT * FROM read_parquet('/etc/hostname')",
            f"COPY (SELECT 1 AS a) TO '{canary}' (FORMAT csv)",
            "CREATE TABLE t AS SELECT 1 AS a",
            "CREATE VIEW j AS SELECT * FROM read_parquet('x') UNION ALL SELECT * FROM read_text('/etc/hostname')",
            "INSTALL httpfs",
        ]
    )


async def _via_resolver(cfg: dict):
    from app.connectors.resolve import resolve_datastore_connector
    from app.repos.memory import InMemoryRepo

    repo = InMemoryRepo()
    row = await repo.create(resource="datastores", org_id=ORG, created_by="u", name="ds", config=cfg)
    connector, _kind, _cleanup = await resolve_datastore_connector(_plan(), str(row["id"]), ORG, repo)
    return connector._conn


async def _via_data_browser(cfg: dict):
    from app.routes.data_browser import _build_duckdb_connector

    return _build_duckdb_connector(cfg, ORG)._conn


async def _via_data_browser_write(cfg: dict):
    from app.routes.data_browser import _build_writable_duckdb_connector

    return _build_writable_duckdb_connector(cfg, ORG)._conn


async def _via_helper(cfg: dict):
    from app.connectors.duckdb_conn import connect_duckdb_datastore

    return connect_duckdb_datastore(cfg, ORG)


OPENERS = [_via_resolver, _via_data_browser, _via_data_browser_write, _via_helper]


@pytest.mark.asyncio
@pytest.mark.parametrize("opener", OPENERS, ids=lambda f: f.__name__)
async def test_hostile_view_sql_creates_nothing(opener, tmp_path):
    canary = tmp_path / "written.csv"
    conn = await opener({"connector_type": "duckdb", "database": ":memory:", "view_sql": _hostile_view_sql(canary)})
    try:
        assert _tables(conn) == set()
        assert not canary.exists()
        # ...and the connection that tenant SQL then runs on cannot reach the host.
        for sql in (
            "SELECT * FROM read_csv_auto('/etc/hostname')",
            "SELECT * FROM read_text('/proc/self/environ')",
            f"COPY (SELECT 1) TO '{canary}'",
            f"ATTACH '{tmp_path}/x.duckdb' AS x",
        ):
            with pytest.raises(Exception):
                conn.execute(sql).fetchall()
        assert not canary.exists()
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("opener", OPENERS, ids=lambda f: f.__name__)
async def test_database_outside_managed_directories_is_refused(opener, tmp_path):
    import duckdb

    from app.errors import AppError

    db = tmp_path / "x.duckdb"
    c = duckdb.connect(str(db))
    c.execute("CREATE TABLE secret AS SELECT 1 AS a")
    c.close()
    with pytest.raises(AppError) as exc:
        await opener({"connector_type": "duckdb", "database": str(db)})
    assert exc.value.status == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("opener", OPENERS, ids=lambda f: f.__name__)
async def test_demo_tables_still_return_rows(opener):
    from app.demo_bundle import export_demo_parquet_local, local_parquet_datastore_config

    export_demo_parquet_local()
    conn = await opener(local_parquet_datastore_config())
    try:
        tables = _tables(conn)
        assert len(tables) >= 17
        name = sorted(tables)[0]
        assert conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] > 0
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("opener", OPENERS, ids=lambda f: f.__name__)
async def test_materialized_dataset_still_reads_but_only_for_its_org(opener, tmp_path, monkeypatch):
    import duckdb

    monkeypatch.setattr("app.flows.incremental._operator_base", lambda settings=None: str(tmp_path))
    for org in (ORG, OTHER_ORG):
        (tmp_path / org).mkdir()
        c = duckdb.connect()
        c.execute(f"COPY (SELECT '{org}' AS owner) TO '{tmp_path}/{org}/orders.parquet' (FORMAT parquet)")
        c.close()

    def cfg(org):
        return {
            "connector_type": "duckdb",
            "database": ":memory:",
            "view_sql": f"CREATE VIEW \"orders\" AS SELECT * FROM read_parquet('{tmp_path}/{org}/orders.parquet')",
        }

    conn = await opener(cfg(ORG))
    try:
        assert conn.execute("SELECT owner FROM orders").fetchone()[0] == ORG
        with pytest.raises(Exception):
            conn.execute(f"SELECT * FROM read_parquet('{tmp_path}/{OTHER_ORG}/orders.parquet')").fetchall()
    finally:
        conn.close()

    conn = await opener(cfg(OTHER_ORG))  # another org's file named in OUR config
    try:
        assert _tables(conn) == set()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_blend_database_still_opens(tmp_path, monkeypatch):
    import duckdb

    from app.connectors import duckdb_conn

    monkeypatch.setattr(duckdb_conn, "duckdb_database_dirs", lambda: [str(tmp_path)])
    db = tmp_path / "blend.duckdb"
    c = duckdb.connect(str(db))
    c.execute("CREATE TABLE blended AS SELECT 1 AS a")
    c.close()
    conn = await _via_resolver({"type": "duckdb", "database": str(db)})
    try:
        assert conn.execute("SELECT a FROM blended").fetchone()[0] == 1
    finally:
        conn.close()


def test_platform_storage_credentials_only_apply_to_the_org_prefix(monkeypatch):
    from app.connectors.duckdb_conn import _target_problem

    monkeypatch.setenv("NUBI_S3_ORG_PREFIX", "s3://platform/datasets/{org_id}/")
    assert _target_problem(f"s3://platform/datasets/{ORG}/a.parquet", {}, ORG) is None
    assert _target_problem(f"s3://platform/datasets/{OTHER_ORG}/a.parquet", {}, ORG)
    assert _target_problem(f"s3://platform/datasets/{ORG}/../{OTHER_ORG}/a.parquet", {}, ORG)
    assert _target_problem("s3://their-bucket/a.parquet", {"s3_key_id": "AKIA"}, ORG) is None
    monkeypatch.delenv("NUBI_S3_ORG_PREFIX")
    assert _target_problem(f"s3://platform/datasets/{ORG}/a.parquet", {}, ORG)


# ---------------------------------------------------------------------------
# Write-time validation
# ---------------------------------------------------------------------------

BAD_CONFIGS = [
    {"view_sql": "CREATE VIEW v AS SELECT * FROM read_text('/proc/self/environ')"},
    {"view_sql": "CREATE VIEW v AS SELECT * FROM read_parquet('/etc/hostname')"},
    {"view_sql": "COPY (SELECT 1) TO '/tmp/x'"},
    {"database": "/abs/x.duckdb"},
    {"path": "/abs/x.duckdb"},
    {"s3_views": {"v": "/etc/hostname"}},
    {"s3_views": {"v": "s3://someone-elses/data.parquet"}},
    {"s3_views": {"v; DROP": "s3://b/k.parquet"}},
    {"parquet_path": "/etc/hostname"},
]


@pytest.mark.parametrize("cfg", BAD_CONFIGS, ids=lambda c: next(iter(c)))
def test_validator_rejects(cfg):
    from app.connectors.duckdb_conn import validate_duckdb_config
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        validate_duckdb_config({"connector_type": "duckdb", **cfg}, ORG)
    assert exc.value.status == 400


def test_validator_accepts_server_configs():
    from app.connectors.duckdb_conn import validate_duckdb_config
    from app.demo_bundle import local_parquet_datastore_config

    validate_duckdb_config(local_parquet_datastore_config(), ORG)
    validate_duckdb_config({"connector_type": "duckdb", "database": ":memory:"}, ORG)
    stored = {"type": "duckdb", "database": "/srv/blends/x.duckdb"}
    validate_duckdb_config(dict(stored), ORG, existing=stored)  # unchanged round trip


def _client(role: str = "owner"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth.deps import current_user
    from app.errors import register_handlers
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo
    import app.routes.connectors  # noqa: F401
    import app.routes.resources  # noqa: F401
    from app.routes import api_router

    user_id = str(uuid.uuid4())
    repo = InMemoryRepo()
    repo.seed_org_member(ORG, user_id, role)
    set_repo(repo)
    app = FastAPI()
    register_handlers(app)
    app.dependency_overrides[current_user] = lambda: {"id": user_id, "email": "t@example.com"}
    app.include_router(api_router, prefix="/api/v1")
    return TestClient(app), repo


@pytest.fixture
def client():
    from app.repos.provider import set_repo

    c, repo = _client()
    with c:
        yield c, repo
    set_repo(None)


@pytest.mark.parametrize("cfg", BAD_CONFIGS[:5], ids=lambda c: next(iter(c)))
def test_connector_create_and_update_reject(client, cfg):
    c, _repo = client
    resp = c.post("/api/v1/connectors", json={"name": "x", "type": "duckdb", "config": cfg})
    assert resp.status_code == 400, resp.text

    ok = c.post("/api/v1/connectors", json={"name": "ok", "type": "duckdb", "config": {"database": ":memory:"}})
    assert ok.status_code == 201, ok.text
    resp = c.put(f"/api/v1/connectors/{ok.json()['id']}", json={"config": cfg})
    assert resp.status_code == 400, resp.text


@pytest.mark.parametrize("cfg", BAD_CONFIGS[:5], ids=lambda c: next(iter(c)))
def test_generic_datastore_writes_reject(client, cfg):
    c, _repo = client
    body = {"name": "x", "config": {"connector_type": "duckdb", **cfg}}
    resp = c.post("/api/v1/datastores", json=body)
    assert resp.status_code == 400, resp.text

    import asyncio

    row = asyncio.run(
        _repo.create(resource="datastores", org_id=ORG, created_by="u", name="ok", config={"connector_type": "duckdb"})
    )
    resp = c.put(f"/api/v1/datastores/{row['id']}", json=body)
    assert resp.status_code == 400, resp.text


def test_no_route_opens_duckdb_datastores_itself():
    """Datastore configs are opened only through the shared helper."""
    allowed = {
        "connectors/duckdb_conn.py",  # the helper itself
        "connectors/preagg.py",  # server-built rollup files
        "flows/materialize.py",  # server-built blend / rollup outputs
        "embedding/snapshot.py",  # server-built export bundle
    }
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        rel = str(path.relative_to(APP_DIR))
        if rel in allowed:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Name)
                and "duckdb" in node.func.value.id.lower()
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# A config with no connector type is a DuckDB config everywhere
# ---------------------------------------------------------------------------

UNTYPED_BAD = [
    {"database": "/abs/x.duckdb"},
    {"database": "/abs/x.duckdb", "host": "db.internal"},
    {"path": "/abs/x.duckdb"},
    {"type": "", "database": "/abs/x.duckdb"},
]


@pytest.mark.parametrize("cfg", UNTYPED_BAD, ids=lambda c: ",".join(c))
def test_untyped_config_is_guarded_as_duckdb(cfg):
    from app.connectors.duckdb_conn import datastore_connector_type, guard_datastore_config
    from app.errors import AppError

    assert datastore_connector_type(cfg) == "duckdb"
    with pytest.raises(AppError) as exc:
        guard_datastore_config(cfg, ORG)
    assert exc.value.status == 400


def test_typed_and_empty_configs_pass_the_guard():
    from app.connectors.duckdb_conn import datastore_connector_type, guard_datastore_config

    guard_datastore_config({}, ORG)
    guard_datastore_config({"note": "bookkeeping"}, ORG)
    guard_datastore_config({"connector_type": "postgres", "host": "db.example.com", "database": "app"}, ORG)
    assert datastore_connector_type({"type": "mysql"}) == "mysql"
    assert datastore_connector_type({"connector_type": "postgres", "type": "x"}) == "postgres"
    assert datastore_connector_type(None) == "duckdb"


@pytest.mark.parametrize("cfg", UNTYPED_BAD[:3], ids=lambda c: ",".join(c))
def test_untyped_datastore_writes_reject(client, cfg):
    c, _repo = client
    resp = c.post("/api/v1/datastores", json={"name": "x", "config": cfg})
    assert resp.status_code == 400, resp.text

    import asyncio

    row = asyncio.run(_repo.create(resource="datastores", org_id=ORG, created_by="u", name="ok", config={}))
    resp = c.put(f"/api/v1/datastores/{row['id']}", json={"name": "x", "config": cfg})
    assert resp.status_code == 400, resp.text


def test_one_rule_decides_what_an_untyped_config_is():
    """Readers and the write guard must agree, so only one place may default the type."""
    import pathlib
    import re

    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    pattern = re.compile(r"""get\(\s*["']type["']\s*\)\s*or\s*["']duckdb["']""")
    offenders = [
        str(p.relative_to(app_dir))
        for p in app_dir.rglob("*.py")
        if p.name != "duckdb_conn.py" and pattern.search(p.read_text())
    ]
    assert offenders == []
