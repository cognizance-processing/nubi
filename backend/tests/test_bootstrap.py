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


# ---------------------------------------------------------------------------
# POST /auth/refresh?bootstrap=1 — same payload, same verification rules
# ---------------------------------------------------------------------------


@pytest.fixture
def refresh_env(env_store, monkeypatch):
    import app.routes.auth as auth_mod

    async def rotate(_raw, **_kw):
        return "new-raw", "u1", None

    async def me_payload(user_id):
        return {"id": user_id, "email": "u1@example.com"}

    monkeypatch.setattr(auth_mod, "rotate_refresh", rotate)
    monkeypatch.setattr(auth_mod, "set_refresh_cookie", lambda *a, **k: None)
    monkeypatch.setattr(auth_mod, "mint_access_token", lambda uid, **_kw: f"tok-{uid}")
    monkeypatch.setattr(auth_mod, "_me_payload", me_payload)
    return auth_mod


def _req():
    return NS(headers={}, client=NS(host="127.0.0.1"))


@pytest.mark.asyncio
async def test_refresh_without_flag_has_no_bootstrap(refresh_env) -> None:
    out = await refresh_env.refresh(
        _req(), NS(), nubi_refresh="raw", bootstrap=False, org_id=None, project_id=None
    )
    assert out["access_token"] == "tok-u1"
    assert out["user"]["id"] == "u1"
    assert "bootstrap" not in out


@pytest.mark.asyncio
async def test_refresh_bootstrap_matches_get_bootstrap(refresh_env) -> None:
    out = await refresh_env.refresh(
        _req(), NS(), nubi_refresh="raw", bootstrap=True, org_id=ORG_B, project_id="pb-default"
    )
    direct = await bootstrap_mod.bootstrap(org_id=ORG_B, project_id="pb-default", user=USER)
    assert out["bootstrap"] == direct
    assert out["bootstrap"]["active_org_id"] == ORG_B


@pytest.mark.asyncio
async def test_refresh_bootstrap_ignores_foreign_hints(refresh_env, env_store) -> None:
    out = await refresh_env.refresh(
        _req(), NS(), nubi_refresh="raw", bootstrap=True, org_id=ORG_OTHER, project_id="p-foreign"
    )
    assert out["bootstrap"]["active_org_id"] == ORG_A
    assert out["bootstrap"]["active_project_id"] == "pa-default"
    assert "p-foreign" not in env_store.ensured


@pytest.mark.asyncio
async def test_refresh_bootstrap_failure_still_restores_session(refresh_env, monkeypatch) -> None:
    import app.routes.orgs as orgs_mod

    async def boom(_user):
        raise RuntimeError("db down")

    monkeypatch.setattr(orgs_mod, "list_orgs", boom)
    out = await refresh_env.refresh(
        _req(), NS(), nubi_refresh="raw", bootstrap=True, org_id=None, project_id=None
    )
    assert out["access_token"] == "tok-u1"
    assert out["user"]["id"] == "u1"
    assert "bootstrap" not in out
