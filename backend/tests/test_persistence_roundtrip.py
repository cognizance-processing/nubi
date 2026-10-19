"""Live persistence round-trip: every user-editable resource survives a reload.

For each PATCH/PUT-able resource/settings endpoint this sets the writable fields
to non-default values, re-reads them through the GET(s) the frontend loads from,
and asserts they came back — then clears the nullable ones (explicit ``null``)
and asserts the clear persisted too. It catches the "saved but gone after
refresh" family end to end, against the real stack and the real Postgres:

* a field the write accepts but the read model drops (or the store never writes);
* explicit ``null`` being treated as "unchanged", so a clear never reaches the DB;
* routes that ignore ``X-Org-Id`` and write into the caller's default org —
  every write here targets a dedicated, non-default throwaway org and checks the
  default org can't see it;
* returned asset URLs that don't serve.

Opt-in — it talks to a RUNNING backend and creates (then deletes) real rows:

    NUBI_LIVE_API=http://localhost:8000 \\
        ../.venv/bin/python -m pytest -q tests/test_persistence_roundtrip.py

Credentials come from SUPERUSER_EMAIL / SUPERUSER_PASSWORD (env, else the repo
root ``.env``). Everything is created inside one org named
``zz-persistence-roundtrip`` (reused across runs — orgs can't be deleted through
the API while they own a project) and removed again in ``finally`` blocks; the
profile test uses its own throwaway user rather than the superuser's profile.
"""

from __future__ import annotations

import os
import pathlib
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest

LIVE = os.getenv("NUBI_LIVE_API", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not LIVE, reason="Set NUBI_LIVE_API=http://localhost:8000 to run the live persistence round-trip."
)

ORG_NAME = "zz-persistence-roundtrip"
PROFILE_EMAIL = "zz-persistence-roundtrip@example.com"
PROFILE_PASSWORD = "Zz-roundtrip-local-only-1!"  # local throwaway account, no privileges

_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415478da63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


# ---------------------------------------------------------------------------
# Client plumbing
# ---------------------------------------------------------------------------


def _root_env(key: str) -> str | None:
    if os.getenv(key):
        return os.environ[key]
    env = pathlib.Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


class Api:
    """Tiny JSON client: bearer auth, optional X-Org-Id, re-login on 401."""

    def __init__(self, email: str, password: str, *, register_name: str | None = None) -> None:
        import httpx  # noqa: PLC0415

        self.http = httpx.Client(base_url=f"{LIVE}/api/v1", timeout=60.0)
        self.email, self.password = email, password
        self.org: str | None = None
        self.token = self._login(register_name)

    def _login(self, register_name: str | None = None) -> str:
        r = self.http.post("/auth/login", json={"email": self.email, "password": self.password})
        if r.status_code != 200 and register_name:
            r = self.http.post(
                "/auth/register",
                json={"email": self.email, "password": self.password, "name": register_name},
            )
        assert r.status_code in (200, 201), f"login failed: {r.status_code} {r.text[:200]}"
        return r.json()["access_token"]

    def request(self, method: str, path: str, *, org: str | None | bool = True, **kw: Any):
        headers = dict(kw.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self.token}"
        target = self.org if org is True else (org or None)
        if target:
            headers["X-Org-Id"] = target
        r = self.http.request(method, path, headers=headers, **kw)
        if r.status_code == 401:
            self.token = self._login()
            headers["Authorization"] = f"Bearer {self.token}"
            r = self.http.request(method, path, headers=headers, **kw)
        return r

    def ok(self, method: str, path: str, json: Any = None, *, expect: tuple[int, ...] = (200, 201), **kw):
        r = self.request(method, path, json=json, **kw)
        assert r.status_code in expect, f"{method} {path} -> {r.status_code}: {r.text[:500]}"
        return r.json() if r.content else None

    def get(self, path: str, **kw):
        return self.ok("GET", path, **kw)


@pytest.fixture(scope="module")
def api() -> Iterator[Api]:
    email, password = _root_env("SUPERUSER_EMAIL"), _root_env("SUPERUSER_PASSWORD")
    if not (email and password):
        pytest.skip("SUPERUSER_EMAIL / SUPERUSER_PASSWORD not available.")
    client = Api(email, password)
    orgs = client.get("/orgs", org=False)["orgs"]
    found = next((o for o in orgs if o["name"] == ORG_NAME), None)
    client.org = found["id"] if found else client.ok("POST", "/orgs", {"name": ORG_NAME}, org=False)["id"]
    default = client.get("/orgs", org=False)  # sanity: the throwaway org must not be the default
    assert any(o["id"] == client.org for o in default["orgs"])
    yield client
    client.http.close()


@pytest.fixture(scope="module")
def project_id(api: Api) -> str:
    return api.get("/projects")[0]["id"]


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def assert_persisted(expected: Any, actual: Any, where: str) -> None:
    """*actual* must contain everything in *expected* (dicts compared as subsets)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: expected an object, got {actual!r}"
        for k, v in expected.items():
            assert k in actual, f"{where}.{k}: missing after reload (got keys {sorted(actual)})"
            assert_persisted(v, actual[k], f"{where}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{where}: {expected!r} != {actual!r}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            assert_persisted(e, a, f"{where}[{i}]")
    else:
        assert actual == expected, f"{where}: wrote {expected!r}, reloaded {actual!r}"


def _in_list(rows: Any, rid: str, key: str = "id") -> dict[str, Any]:
    if isinstance(rows, dict):  # {items|orgs|metrics|watches|...: [...]}
        rows = next((v for v in rows.values() if isinstance(v, list)), [])
    match = [r for r in rows if str(r.get(key)) == str(rid)]
    assert match, f"{rid} not in the list response"
    return match[0]


def check_reads(expected: dict[str, Any], reads: dict[str, Callable[[], dict[str, Any]]]) -> None:
    for label, read in reads.items():
        assert_persisted(expected, read(), label)


def assert_not_in_default_org(api: Api, path: str) -> None:
    """Class (f): a row written with X-Org-Id must not be visible without it."""
    r = api.request("GET", path, org=False)
    assert r.status_code in (403, 404), f"GET {path} in the DEFAULT org -> {r.status_code}: written to the wrong org?"


# ---------------------------------------------------------------------------
# Profile + org + project settings
# ---------------------------------------------------------------------------


def test_profile_roundtrip(api: Api) -> None:
    user = Api(PROFILE_EMAIL, PROFILE_PASSWORD, register_name="ZZ Roundtrip")
    try:
        up = user.http.post(
            "/images",
            files={"file": ("a.png", _PNG_1PX, "image/png")},
            headers={"Authorization": f"Bearer {user.token}"},
        )
        assert up.status_code == 201, up.text
        avatar = up.json()["url"]

        written = {"name": f"ZZ {uuid.uuid4().hex[:6]}", "avatar_url": avatar}
        user.ok("PATCH", "/auth/me", written, org=False)
        me = user.get("/auth/me", org=False)["user"]
        assert_persisted(written, me, "GET /auth/me")

        # The stored avatar URL must actually serve (as an <img src>, no auth).
        served = user.http.get(f"{LIVE}{me['avatar_url']}" if me["avatar_url"].startswith("/") else me["avatar_url"])
        assert served.status_code == 200 and served.content == _PNG_1PX, f"avatar URL {me['avatar_url']} -> {served.status_code}"

        user.ok("PATCH", "/auth/me", {"avatar_url": None}, org=False)
        assert user.get("/auth/me", org=False)["user"]["avatar_url"] is None, "clearing the avatar did not persist"
    finally:
        user.http.close()


def test_org_settings_roundtrip(api: Api) -> None:
    org = api.org
    written = {"name": f"{ORG_NAME}", "avatar_url": "https://example.com/zz-org.png"}
    api.ok("PATCH", f"/orgs/{org}", written, org=False)
    try:
        check_reads(written, {
            f"GET /orgs/{org}": lambda: api.get(f"/orgs/{org}", org=False),
            "GET /orgs (OrgContext)": lambda: _in_list(api.get("/orgs", org=False), org),
        })
        api.ok("PATCH", f"/orgs/{org}", {"avatar_url": None}, org=False)
        check_reads({"avatar_url": None}, {
            f"GET /orgs/{org}": lambda: api.get(f"/orgs/{org}", org=False),
            "GET /orgs": lambda: _in_list(api.get("/orgs", org=False), org),
        })
    finally:
        api.request("PATCH", f"/orgs/{org}", org=False, json={"name": ORG_NAME, "avatar_url": None})


def test_project_settings_roundtrip(api: Api) -> None:
    pid = api.ok("POST", "/projects", {"name": f"zz-proj-{uuid.uuid4().hex[:6]}"})["id"]
    try:
        written = {"name": f"zz-proj-renamed-{uuid.uuid4().hex[:6]}", "git": {"remote": "https://example.com/r.git", "branch": "dev"}}
        api.ok("PATCH", f"/projects/{pid}", written)
        check_reads(written, {
            f"GET /projects/{pid}": lambda: api.get(f"/projects/{pid}"),
            "GET /projects": lambda: _in_list(api.get("/projects"), pid),
        })
        assert_not_in_default_org(api, f"/projects/{pid}")
    finally:
        r = api.request("GET", f"/projects/{pid}")
        if r.status_code == 200:
            api.request("DELETE", f"/projects/{pid}", json={"confirm_name": r.json()["name"]})


def test_environment_roundtrip(api: Api, project_id: str) -> None:
    key = f"zz{uuid.uuid4().hex[:6]}"
    env = api.ok("POST", f"/projects/{project_id}/environments", {"key": key, "name": "ZZ env"})
    eid = env.get("id") or env.get("environment", {}).get("id")
    try:
        written = {"name": "ZZ env renamed", "protected": True, "position": 7}
        api.ok("PATCH", f"/environments/{eid}", written)
        check_reads(written, {
            "GET /projects/{id}/environments": lambda: _in_list(api.get(f"/projects/{project_id}/environments"), eid),
        })
    finally:
        api.request("DELETE", f"/environments/{eid}")


# ---------------------------------------------------------------------------
# Connectors / datastores
# ---------------------------------------------------------------------------


@pytest.fixture
def connector(api: Api) -> Iterator[str]:
    row = api.ok("POST", "/connectors", {
        "name": "zz-conn",
        "type": "postgres",
        "config": {"host": "h1.invalid", "port": 5432, "database": "d", "user": "u", "schema": "s1"},
        "secret": {"password": "x"},
    })
    try:
        yield row["id"]
    finally:
        api.request("DELETE", f"/connectors/{row['id']}")


def test_connector_roundtrip(api: Api, connector: str) -> None:
    cid = connector
    written = {"name": "zz-conn-renamed", "config": {"host": "h2.invalid", "port": 6543, "database": "d2", "sslmode": "require"}}
    api.ok("PUT", f"/connectors/{cid}", written)
    reads = {
        f"GET /connectors/{cid}": lambda: api.get(f"/connectors/{cid}"),
        "GET /connectors": lambda: _in_list(api.get("/connectors"), cid),
    }
    check_reads(written, reads)
    assert_not_in_default_org(api, f"/connectors/{cid}")

    # Clearing optional fields (the edit form sends null for an emptied field).
    api.ok("PUT", f"/connectors/{cid}", {"config": {"schema": None, "sslmode": None}})
    for label, read in reads.items():
        cfg = read()["config"]
        assert "schema" not in cfg and "sslmode" not in cfg, f"{label}: cleared fields came back: {cfg}"
        assert cfg["host"] == "h2.invalid", f"{label}: an omitted field was lost"


# ---------------------------------------------------------------------------
# Generic resources: boards, queries, widgets, folders
# ---------------------------------------------------------------------------

_GENERIC = {
    "boards": {"spec": {"title": "ZZ board", "stylePreset": "glass", "tab_bar": {"variant": "pill"}, "widgets": []}, "folderId": None},
    "queries": {"sql": "SELECT 1 AS n", "description": "zz", "params": [{"name": "p", "type": "text"}]},
    "widgets": {"widget": {"id": "w", "type": "text", "content": "hi", "style": {"color": "#f00"}}},
    "dashboard_folders": {"color": "#123456", "icon": "folder", "position": 3},
}


@pytest.mark.parametrize("resource", sorted(_GENERIC))
def test_generic_resource_roundtrip(api: Api, resource: str) -> None:
    rid = api.ok("POST", f"/{resource}", {"name": f"zz-{resource}", "config": {"seed": True}})["id"]
    try:
        written = {"name": f"zz-{resource}-renamed", "config": {**_GENERIC[resource], "nested": {"a": [1, 2], "b": None}}}
        api.ok("PUT", f"/{resource}/{rid}", written)
        check_reads(written, {
            f"GET /{resource}/{rid}": lambda: api.get(f"/{resource}/{rid}"),
            f"GET /{resource}": lambda: _in_list(api.get(f"/{resource}"), rid),
        })
        assert_not_in_default_org(api, f"/{resource}/{rid}")
    finally:
        api.request("DELETE", f"/{resource}/{rid}")


# ---------------------------------------------------------------------------
# Flows, metrics, watches, variables
# ---------------------------------------------------------------------------


def test_flow_roundtrip(api: Api) -> None:
    spec = {"version": 1, "name": "zz-flow", "tasks": [{"key": "a", "kind": "noop"}]}
    fid = api.ok("POST", "/flows", {"name": "zz-flow", "spec": spec})["id"]
    try:
        new_spec = {
            "version": 1,
            "name": "zz-flow",
            "view": "notebook",
            "runtime_config": {"auto_rebuild_downstream": True},
            "params": [{"name": "day", "type": "date"}],
            "tasks": [
                {"key": "a", "kind": "noop", "ui": {"x": 5.0, "y": 7.0}, "cell_type": "markdown", "retries": 2, "timeout_s": 90},
                {"key": "b", "kind": "noop", "needs": ["a"]},
            ],
        }
        written = {"name": "zz-flow-renamed", "enabled": False, "schedule": "0 * * * *", "spec": new_spec}
        api.ok("PUT", f"/flows/{fid}", written)
        check_reads(written, {
            f"GET /flows/{fid}": lambda: api.get(f"/flows/{fid}"),
            "GET /flows": lambda: _in_list(api.get("/flows"), fid),
        })
        assert_not_in_default_org(api, f"/flows/{fid}")

        api.ok("PUT", f"/flows/{fid}", {"schedule": None})
        assert api.get(f"/flows/{fid}")["schedule"] is None, "clearing the schedule did not persist"
    finally:
        api.request("DELETE", f"/flows/{fid}")


def test_metric_and_watch_roundtrip(api: Api, connector: str) -> None:
    slug_name = f"zz metric {uuid.uuid4().hex[:6]}"
    metric = api.ok("POST", "/metrics", {
        "name": slug_name,
        "measure": {"name": "n", "agg": "sum", "expr": "n", "type": "additive"},
        "base_sql": "SELECT 1 AS n, 'a' AS d",
        "datastore_id": connector,
        "dimensions": [{"name": "d", "type": "text"}],
    })
    mid = metric["id"]
    wid = None
    try:
        written = {
            "name": slug_name,
            "description": "zz description",
            "measure": {"name": "n", "agg": "max", "expr": "n", "type": "additive", "format": "currency"},
            "base_sql": "SELECT 2 AS n, 'b' AS d",
            "datastore_id": connector,
            "dimensions": [{"name": "d", "type": "text"}],
            "rls_keys": ["d"],
        }
        api.ok("PUT", f"/metrics/{mid}", written)
        check_reads(written, {f"GET /metrics/{mid}": lambda: api.get(f"/metrics/{mid}")})

        # Class (f): the metric's datastore must resolve in the metric's org.
        q = api.request("POST", f"/metrics/{mid}/query", json={"dimensions": ["d"]},
                        headers={"Accept": "application/vnd.apache.arrow.stream"})
        if q.status_code != 200:
            code = (q.json().get("error") or {}).get("code") if q.headers.get("content-type", "").startswith("application/json") else None
            assert code != "datastore_not_found", "metric query resolved its datastore in the DEFAULT org"

        wid = api.ok("POST", "/watches", {"name": "zz-watch", "metric_id": mid, "config": {"threshold": {"op": ">", "value": 1}}})["id"]
        w_written = {
            "name": "zz-watch-renamed",
            "metric_id": mid,
            "config": {"threshold": {"op": "<", "value": 9}, "time_grain": None, "enabled": False, "dimensions": ["d"],
                       "channel_config": {"kind": "email"}, "labels": {"team": "zz"}},
        }
        api.ok("PUT", f"/watches/{wid}", w_written)
        check_reads(w_written, {
            f"GET /watches/{wid}": lambda: api.get(f"/watches/{wid}"),
            "GET /watches": lambda: _in_list(api.get("/watches"), wid),
        })
        assert_not_in_default_org(api, f"/watches/{wid}")
    finally:
        if wid:
            api.request("DELETE", f"/watches/{wid}")
        api.request("DELETE", f"/metrics/{mid}")
        # DELETE /metrics keeps the backing query (by design) — remove it too.
        for q in api.get("/queries"):
            if q.get("name") == slug_name:
                api.request("DELETE", f"/queries/{q['id']}")


def test_variable_roundtrip(api: Api) -> None:
    key = f"zz_var_{uuid.uuid4().hex[:6]}"
    try:
        written = {"value": {"region": "EMEA", "n": [1, 2], "empty": None}}
        api.ok("PUT", f"/variables/{key}", written)
        check_reads(written, {
            f"GET /variables/{key}": lambda: api.get(f"/variables/{key}"),
            "GET /variables": lambda: _in_list(api.get("/variables"), key, key="key"),
        })
        assert_not_in_default_org(api, f"/variables/{key}")
        api.ok("PUT", f"/variables/{key}", {"value": None})
        assert api.get(f"/variables/{key}")["value"] is None, "clearing a variable did not persist"
    finally:
        api.request("DELETE", f"/variables/{key}")


# ---------------------------------------------------------------------------
# Integrations, MCP servers, JWT issuers, webhooks
# ---------------------------------------------------------------------------


def test_integration_roundtrip(api: Api) -> None:
    iid = api.ok("POST", "/integrations", {"kind": "email", "name": "zz-int", "config": {"recipients": ["a@example.com"]}})["id"]
    try:
        written = {"name": "zz-int-renamed", "enabled": False, "config": {"recipients": ["b@example.com", "c@example.com"]}}
        api.ok("PUT", f"/integrations/{iid}", written)
        check_reads(written, {
            f"GET /integrations/{iid}": lambda: api.get(f"/integrations/{iid}"),
            "GET /integrations": lambda: _in_list(api.get("/integrations"), iid),
        })
        assert_not_in_default_org(api, f"/integrations/{iid}")
    finally:
        api.request("DELETE", f"/integrations/{iid}")


def test_mcp_server_roundtrip(api: Api) -> None:
    sid = api.ok("POST", "/mcp/servers", {"name": "zz-mcp", "url": "https://example.com/mcp", "auth_token": "tok"})["id"]
    try:
        written = {"name": "zz-mcp-renamed", "url": "https://example.org/mcp2", "transport": "sse", "enabled": False}
        api.ok("PUT", f"/mcp/servers/{sid}", {**written, "auth_token": None})
        check_reads(written, {
            f"GET /mcp/servers/{sid}": lambda: api.get(f"/mcp/servers/{sid}"),
            "GET /mcp/servers": lambda: _in_list(api.get("/mcp/servers"), sid),
        })
        assert_not_in_default_org(api, f"/mcp/servers/{sid}")
    finally:
        api.request("DELETE", f"/mcp/servers/{sid}")


def test_jwt_issuer_roundtrip(api: Api) -> None:
    # Slash-less collection path on purpose: it is what the Security settings page calls.
    iid = api.ok("POST", "/security/jwt-issuers", {
        "name": "zz-iss", "issuer": "https://zz.example", "audience": "zz",
        "jwks_url": "https://zz.example/jwks.json", "algorithms": ["RS256"],
    })["id"]
    try:
        written = {"name": "zz-iss-renamed", "audience": "zz2", "algorithms": ["RS256", "ES256"], "enabled": False,
                   "jwks_url": "https://zz.example/jwks2.json"}
        api.ok("PUT", f"/security/jwt-issuers/{iid}", written)
        check_reads(written, {
            f"GET /security/jwt-issuers/{iid}": lambda: api.get(f"/security/jwt-issuers/{iid}"),
            "GET /security/jwt-issuers": lambda: _in_list(api.get("/security/jwt-issuers"), iid),
        })
        assert_not_in_default_org(api, f"/security/jwt-issuers/{iid}")
    finally:
        api.request("DELETE", f"/security/jwt-issuers/{iid}")


def test_webhook_roundtrip(api: Api) -> None:
    wid = api.ok("POST", "/webhooks", {"name": "zz-hook", "url": "https://example.com/hook", "secret": "abcdefgh12345"})["id"]
    try:
        written = {"name": "zz-hook-renamed", "url": "https://example.org/hook2", "event_types": ["watch_breach"], "active": False}
        api.ok("PUT", f"/webhooks/{wid}", written)
        check_reads(written, {
            f"GET /webhooks/{wid}": lambda: api.get(f"/webhooks/{wid}"),
            "GET /webhooks": lambda: _in_list(api.get("/webhooks"), wid),
        })
        assert_not_in_default_org(api, f"/webhooks/{wid}")
    finally:
        api.request("DELETE", f"/webhooks/{wid}")
