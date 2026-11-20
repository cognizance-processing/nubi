"""GET /bootstrap — hint verification and response shape.

The org and project hints are loaded speculatively; these tests pin that a
hint is only honoured when the caller's own org / project lists vouch for it,
and that nothing that can write runs for an unverified project.
"""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

import pytest

from app.routes import bootstrap as bootstrap_mod

USER = {"id": "u1"}
ORG_A, ORG_B, ORG_OTHER = "org-a", "org-b", "org-not-mine"
PROJECTS = {
    ORG_A: [{"id": "pa-default"}, {"id": "pa-2"}],
    ORG_B: [{"id": "pb-default"}],
    ORG_OTHER: [{"id": "p-foreign"}],
}
ENVS = [{"key": "dev"}, {"key": "prod"}]


class _EnvStore:
    def __init__(self) -> None:
        self.listed: list[str] = []
        self.ensured: list[str] = []

    async def list_environments(self, project_id: str) -> list[dict[str, Any]]:
        self.listed.append(project_id)
        return [dict(e, project_id=project_id) for e in ENVS]

    async def ensure_project_envs(self, project_id: str) -> list[dict[str, Any]]:
        self.ensured.append(project_id)
        return [dict(e, project_id=project_id) for e in ENVS]


@pytest.fixture
def env_store(monkeypatch) -> _EnvStore:
    store = _EnvStore()

    async def list_orgs(_user):
        return NS(orgs=[
            NS(model_dump=lambda o=o: {"id": o, "name": o, "role": "owner"})
            for o in (ORG_A, ORG_B)
        ])

    async def list_projects(org_id):
        return [dict(p) for p in PROJECTS.get(org_id, [])]

    async def list_features(_user):
        return {"features": ["billing"]}

    import app.routes.features as features_mod
    import app.routes.orgs as orgs_mod

    monkeypatch.setattr(orgs_mod, "list_orgs", list_orgs)
    monkeypatch.setattr(features_mod, "list_features", list_features)
    monkeypatch.setattr(bootstrap_mod.projects_repo, "list_projects", list_projects)
    monkeypatch.setattr(bootstrap_mod, "get_env_store", lambda: store)
    return store


@pytest.mark.asyncio
async def test_no_hints_picks_first_org_and_its_default_project(env_store) -> None:
    out = await bootstrap_mod.bootstrap(org_id=None, project_id=None, user=USER)
    assert out["active_org_id"] == ORG_A
    assert out["active_project_id"] == "pa-default"
    assert [p["id"] for p in out["projects"]] == ["pa-default", "pa-2"]
    assert {e["key"] for e in out["environments"]} == {"dev", "prod"}
    assert out["features"] == ["billing"]
    assert [o["id"] for o in out["orgs"]] == [ORG_A, ORG_B]


@pytest.mark.asyncio
async def test_valid_hints_are_honoured_without_extra_writes(env_store) -> None:
    out = await bootstrap_mod.bootstrap(org_id=ORG_B, project_id="pb-default", user=USER)
    assert out["active_org_id"] == ORG_B
    assert out["active_project_id"] == "pb-default"
    assert env_store.listed == ["pb-default"]
    assert env_store.ensured == []  # the pair already existed


@pytest.mark.asyncio
async def test_foreign_org_hint_is_ignored(env_store) -> None:
    out = await bootstrap_mod.bootstrap(org_id=ORG_OTHER, project_id="p-foreign", user=USER)
    assert out["active_org_id"] == ORG_A
    assert all(p["id"] != "p-foreign" for p in out["projects"])
    assert out["active_project_id"] == "pa-default"
    assert all(e["project_id"] == "pa-default" for e in out["environments"])
    # The foreign project was only ever READ speculatively, never ensured.
    assert "p-foreign" not in env_store.ensured


@pytest.mark.asyncio
async def test_project_hint_from_another_org_falls_back_to_default(env_store) -> None:
    out = await bootstrap_mod.bootstrap(org_id=ORG_A, project_id="pb-default", user=USER)
    assert out["active_org_id"] == ORG_A
    assert out["active_project_id"] == "pa-default"
    assert "pb-default" not in env_store.ensured


@pytest.mark.asyncio
async def test_no_orgs_returns_empty_shell(env_store, monkeypatch) -> None:
    import app.routes.orgs as orgs_mod

    async def no_orgs(_user):
        return NS(orgs=[])

    monkeypatch.setattr(orgs_mod, "list_orgs", no_orgs)
    out = await bootstrap_mod.bootstrap(org_id=None, project_id=None, user=USER)
    assert out == {
        "orgs": [], "active_org_id": None, "projects": [],
        "active_project_id": None, "environments": None, "features": ["billing"],
    }
