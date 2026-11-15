"""Rollups are built only from the caller's own tables and stay with their org."""

from __future__ import annotations

import os
import pathlib
import uuid

import pytest

from tests.security.conftest_helpers import as_user, make_tenant_app

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())


@pytest.fixture
def rollups_dir(tmp_path, monkeypatch):
    d = tmp_path / "rollups"
    monkeypatch.setenv("NUBI_ROLLUPS_DIR", str(d))
    monkeypatch.delenv("NUBI_DUCKDB_DATA_DIRS", raising=False)
    return d


@pytest.fixture
def client(rollups_dir):
    from fastapi.testclient import TestClient

    from app.connectors import preagg
    from app.connectors.query_log import get_query_log
    from app.repos.provider import set_repo

    preagg.reset_for_tests()
    get_query_log().clear()
    app, _repo, _users = make_tenant_app(
        {"a": (ORG_A, "owner"), "b": (ORG_B, "owner")},
        modules=("app.routes.preagg",),
    )
    with TestClient(app) as c:
        yield c
    set_repo(None)
    preagg.reset_for_tests()
    get_query_log().clear()


def _files(root: pathlib.Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _demo_shape() -> dict:
    return {"table": "budget", "dimensions": ["region"], "measures": ["sum(budget_nsv)"]}


@pytest.mark.parametrize(
    "table",
    ["/etc/hostname", "../../outside", "/abs/file.csv", 'x" UNION SELECT 1 --', "read_csv('/etc/hostname')", "no_such_table"],
)
def test_build_refuses_tables_that_are_not_the_orgs(client, rollups_dir, tmp_path, table):
    resp = client.post(
        "/api/v1/preagg/build",
        json={"table": table, "dimensions": [], "measures": ["count(a)"]},
        headers=as_user("a"),
    )
    assert resp.status_code == 400, resp.text
    assert _files(rollups_dir) == []
    assert not (tmp_path / "outside.duckdb").exists()


def test_build_request_cannot_name_a_source_file():
    from app.routes.preagg import BuildFromMetricIn, BuildRollupIn

    assert "source_database" not in BuildRollupIn.model_fields
    assert "source_database" not in BuildFromMetricIn.model_fields


def test_build_from_a_real_table_lands_in_the_orgs_directory(client, rollups_dir):
    resp = client.post("/api/v1/preagg/build", json=_demo_shape(), headers=as_user("a"))
    assert resp.status_code == 201, resp.text
    files = _files(rollups_dir)
    assert len(files) == 1
    assert files[0].startswith(ORG_A + os.sep)
    assert "budget" not in os.path.basename(files[0])


def test_rollups_are_listed_only_to_their_org(client):
    assert client.post("/api/v1/preagg/build", json=_demo_shape(), headers=as_user("a")).status_code == 201
    assert len(client.get("/api/v1/preagg", headers=as_user("a")).json()) == 1
    assert client.get("/api/v1/preagg", headers=as_user("b")).json() == []


def test_suggestions_are_mined_per_org(client):
    from app.connectors.query_log import get_query_log

    for _ in range(5):
        get_query_log().record(
            "SELECT region, sum(amount) FROM private_ledger GROUP BY region", "k", byte_size=10, org_id=ORG_A
        )
    mine_a = client.get("/api/v1/preagg/suggestions?min_hits=1", headers=as_user("a")).json()
    assert any(c["table"] == "private_ledger" for c in mine_a)
    assert client.get("/api/v1/preagg/suggestions?min_hits=1", headers=as_user("b")).json() == []
    assert client.get("/api/v1/_preagg/suggestions?min_hits=1", headers=as_user("b")).json() == []
    # A candidate mined from another org's queries cannot be built by key.
    key = mine_a[0]["cluster_key"]
    resp = client.post("/api/v1/preagg/build", json={"cluster_key": key}, headers=as_user("b"))
    assert resp.status_code == 404, resp.text


def test_legacy_register_is_operator_only(client):
    from unittest.mock import AsyncMock, patch

    with patch("app.db.fetchrow", new=AsyncMock(return_value={"is_superadmin": False})):
        resp = client.post("/api/v1/_preagg/register", json={"sig": "s", "table": "t"}, headers=as_user("a"))
    assert resp.status_code == 403, resp.text


def test_build_rollup_refuses_unmanaged_source_and_odd_names(rollups_dir, tmp_path):
    import duckdb

    from app.connectors.preagg import RollupCandidate, build_rollup, reset_for_tests
    from app.errors import AppError

    reset_for_tests()
    src = tmp_path / "elsewhere.duckdb"
    c = duckdb.connect(str(src))
    c.execute("CREATE TABLE t AS SELECT 1 AS a")
    c.close()
    cand = RollupCandidate(table="t", dimensions=[], measures=["count(a)"])
    with pytest.raises((AppError, ValueError)):
        build_rollup(cand, source_database=str(src), org_id=ORG_A)
    for bad in ("/etc/hostname", "../x", 'a"b'):
        with pytest.raises((AppError, ValueError)):
            build_rollup(RollupCandidate(table=bad, dimensions=[], measures=["count(a)"]), org_id=ORG_A)
    with pytest.raises((AppError, ValueError)):
        build_rollup(cand, rollup_id="../escape", org_id=ORG_A)
    assert _files(rollups_dir) == []
    assert _files(tmp_path) == ["elsewhere.duckdb"]


def test_refresh_task_runs_for_the_flows_own_org(rollups_dir):
    from types import SimpleNamespace

    from app.errors import AppError
    from app.flows.handlers.preagg_refresh import handle as handle_preagg_refresh

    ctx = SimpleNamespace(org_id=ORG_A)
    with pytest.raises(AppError) as exc:
        handle_preagg_refresh(config={"org_id": ORG_B}, ctx=ctx, claims={})
    assert exc.value.status == 400
    out = handle_preagg_refresh(config={}, ctx=ctx, claims={})
    assert out["errors"] == []
