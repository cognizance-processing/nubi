"""Tests for the super-admin capability (/admin/*, geo, login analytics).

Coverage
--------
1.  Every /admin route → 403 for a normal authenticated user, 401 unauthenticated.
2.  Every /admin route → 200 with the contract shape for a superadmin.
3.  /admin/users includes org memberships.
4.  Escalation is impossible:
    - PUT /orgs/{id}/members/{uid} with role='superadmin' → 400.
    - A crafted payload with is_superadmin=true is ignored (closed Pydantic
      models on the member-role and profile-update endpoints).
5.  app.geo.lookup: cache hit skips HTTP; private IP / no-token short-circuit.
6.  Successful login/register inserts a login_events row (X-Forwarded-For first
    hop); a failing insert never breaks auth.
7.  /auth/me reports is_superadmin from the DB row.

Pattern: like test_orgs.py, the admin route module's ``fetch``/``fetchrow``
are patched per-fixture (the conftest FakeDB doesn't speak the admin SQL).
``require_superadmin`` resolves through the conftest-patched ``app.db.fetchrow``
(FakeDB users), so the superadmin flag is just a key on the seeded user dict.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token

# Self-registering routers (idempotent; main.py imports them too).
import app.routes.admin  # noqa: F401, E402
import app.routes.orgs  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str, email: str, *, superadmin: bool = False) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "is_superadmin": superadmin,
        "created_at": datetime.now(tz=timezone.utc),
    }


# ---------------------------------------------------------------------------
# Fixture: client with one normal user + one superadmin + admin SQL fakes
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def admin_client(app, fake_db):
    """Yield (client, ctx) with admin.py fetch/fetchrow faked over a tiny dataset."""
    normal_id = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    now = datetime.now(tz=timezone.utc)

    fake_db.users[normal_id] = _make_user(normal_id, "user@example.com")
    fake_db.users[admin_id] = _make_user(admin_id, "root@example.com", superadmin=True)

    counts_row = {
        "users": 2, "orgs": 1, "projects": 1, "boards": 0,
        "queries": 3, "flows": 1, "datastores": 2,
    }
    signups = [{"day": "2026-06-08", "count": 2}]
    logins = [{"day": "2026-06-09", "count": 5}]
    user_rows = [
        {
            "id": normal_id, "email": "user@example.com", "name": "Test User",
            "created_at": now, "is_superadmin": False,
            "last_login_at": now, "last_ip": "8.8.8.8",
            "geo_city": "Cape Town", "geo_region": "Western Cape", "geo_country": "ZA",
        },
        {
            "id": admin_id, "email": "root@example.com", "name": "Root",
            "created_at": now, "is_superadmin": True,
            "last_login_at": None, "last_ip": None,
            "geo_city": None, "geo_region": None, "geo_country": None,
        },
    ]
    membership_rows = [
        {"user_id": normal_id, "org_id": org_id, "org_name": "Acme", "role": "owner"},
    ]
    org_row = {
        "id": org_id, "name": "Acme", "slug": "acme-1234",
        "created_at": now, "member_count": 1, "project_count": 1,
    }
    member_rows = [
        {"user_id": normal_id, "email": "user@example.com", "name": "Test User", "role": "owner"},
    ]
    project_rows = [
        {"id": project_id, "name": "Default", "slug": "default", "created_at": now},
    ]
    country_rows = [{"country": "ZA", "count": 4}, {"country": "US", "count": 1}]
    geo_lookups: list[str] = []

    async def _admin_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split()).upper()
        if "LEFT JOIN LATERAL" in q:
            return user_rows
        if "ANY($1::UUID[])" in q:
            wanted = {str(u) for u in args[0]}
            return [m for m in membership_rows if m["user_id"] in wanted]
        if "FROM LOGIN_EVENTS" in q and "GROUP BY 1" in q:
            return logins
        if "FROM USERS" in q and "GROUP BY 1" in q:
            return signups
        if "DISTINCT LE.IP" in q:
            return [{"ip": "203.0.113.99"}]
        if "GROUP BY G.COUNTRY" in q:
            return country_rows
        if "JOIN USERS U" in q:
            return member_rows
        # NOTE: the orgs LIST query also contains "FROM PROJECTS P" inside its
        # count subselect, so check it BEFORE the bare projects query.
        if "FROM ORGS O" in q:
            return [org_row]
        if "FROM PROJECTS" in q:
            return project_rows
        return []

    async def _admin_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = " ".join(query.split()).upper()
        if "(SELECT COUNT(*)::INT FROM USERS)" in q:
            return counts_row
        if "TOTAL_EVENTS" in q:
            return {"total_events": 5, "total_located": 4}
        if "FROM USERS U" in q and "AS TOTAL" in q:
            return {"total": len(user_rows)}
        if "WHERE O.ID = $1" in q:
            return org_row if str(args[0]) == org_id else None
        if "FROM ORGS O" in q and "AS TOTAL" in q:
            return {"total": 1}
        return None

    async def _fake_geo_lookup(ip: str | None) -> dict[str, Any] | None:
        geo_lookups.append(str(ip))
        return None

    ctx = {
        "normal_id": normal_id,
        "admin_id": admin_id,
        "org_id": org_id,
        "geo_lookups": geo_lookups,
    }

    with (
        patch("app.routes.admin.fetch", side_effect=_admin_fetch),
        patch("app.routes.admin.fetchrow", side_effect=_admin_fetchrow),
        patch("app.routes.admin.geo_module.lookup", side_effect=_fake_geo_lookup),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=False
        ) as ac:
            yield ac, ctx


def _admin_paths(org_id: str) -> list[str]:
    return [
        "/api/v1/admin/overview",
        "/api/v1/admin/users",
        "/api/v1/admin/orgs",
        f"/api/v1/admin/orgs/{org_id}",
        "/api/v1/admin/geo/summary",
    ]


# ---------------------------------------------------------------------------
# 1. Access control
# ---------------------------------------------------------------------------


class TestAdminAccessControl:
    @pytest.mark.asyncio
    async def test_every_admin_route_403_for_normal_user(self, admin_client):
        client, ctx = admin_client
        for path in _admin_paths(ctx["org_id"]):
            resp = await client.get(path, headers=_auth_headers(ctx["normal_id"]))
            assert resp.status_code == 403, f"{path} → {resp.status_code}"
            assert resp.json()["error"]["code"] == "forbidden"

    @pytest.mark.asyncio
    async def test_every_admin_route_401_unauthenticated(self, admin_client):
        client, ctx = admin_client
        for path in _admin_paths(ctx["org_id"]):
            resp = await client.get(path)
            assert resp.status_code == 401, f"{path} → {resp.status_code}"

    @pytest.mark.asyncio
    async def test_superadmin_flag_read_from_db_not_jwt(self, admin_client, fake_db):
        """Revoking the DB flag locks out even a fresh, valid JWT."""
        client, ctx = admin_client
        fake_db.users[ctx["admin_id"]]["is_superadmin"] = False
        resp = await client.get(
            "/api/v1/admin/overview", headers=_auth_headers(ctx["admin_id"])
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 2. Contract shapes for superadmin
# ---------------------------------------------------------------------------


class TestAdminShapes:
    @pytest.mark.asyncio
    async def test_overview_shape(self, admin_client):
        client, ctx = admin_client
        resp = await client.get(
            "/api/v1/admin/overview", headers=_auth_headers(ctx["admin_id"])
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["counts"]) == {
            "users", "orgs", "projects", "boards", "queries", "flows", "datastores"
        }
        assert body["counts"]["users"] == 2
        assert body["signups_by_day"] == [{"day": "2026-06-08", "count": 2}]
        assert body["logins_by_day"] == [{"day": "2026-06-09", "count": 5}]

    @pytest.mark.asyncio
    async def test_users_list_shape_and_org_memberships(self, admin_client):
        client, ctx = admin_client
        resp = await client.get(
            "/api/v1/admin/users", headers=_auth_headers(ctx["admin_id"])
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        by_id = {u["id"]: u for u in body["users"]}
        normal = by_id[ctx["normal_id"]]
        for key in ("id", "email", "name", "created_at", "is_superadmin",
                    "last_login_at", "last_ip", "last_location", "orgs"):
            assert key in normal
        # Org memberships are included.
        assert normal["orgs"] == [{"id": ctx["org_id"], "name": "Acme", "role": "owner"}]
        # Location string is 'City, CC'.
        assert normal["last_location"] == "Cape Town, ZA"
        assert by_id[ctx["admin_id"]]["last_location"] is None
        assert by_id[ctx["admin_id"]]["orgs"] == []

    @pytest.mark.asyncio
    async def test_orgs_list_shape(self, admin_client):
        client, ctx = admin_client
        resp = await client.get(
            "/api/v1/admin/orgs", headers=_auth_headers(ctx["admin_id"])
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        org = body["orgs"][0]
        assert org["id"] == ctx["org_id"]
        for key in ("id", "name", "slug", "created_at", "member_count", "project_count"):
            assert key in org
        assert org["member_count"] == 1
        assert org["project_count"] == 1

    @pytest.mark.asyncio
    async def test_org_detail_shape(self, admin_client):
        client, ctx = admin_client
        resp = await client.get(
            f"/api/v1/admin/orgs/{ctx['org_id']}", headers=_auth_headers(ctx["admin_id"])
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["org"]["id"] == ctx["org_id"]
        assert body["members"][0] == {
            "user_id": ctx["normal_id"], "email": "user@example.com",
            "name": "Test User", "role": "owner",
        }
        assert {"id", "name", "slug", "created_at"} <= set(body["projects"][0])

    @pytest.mark.asyncio
    async def test_org_detail_404_unknown(self, admin_client):
        client, ctx = admin_client
        resp = await client.get(
            f"/api/v1/admin/orgs/{uuid.uuid4()}", headers=_auth_headers(ctx["admin_id"])
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_geo_summary_shape_and_lazy_lookup(self, admin_client):
        client, ctx = admin_client
        resp = await client.get(
            "/api/v1/admin/geo/summary", headers=_auth_headers(ctx["admin_id"])
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["countries"] == [
            {"country": "ZA", "count": 4}, {"country": "US", "count": 1}
        ]
        assert body["total_located"] == 4
        assert body["total_events"] == 5
        # The uncached IP returned by the fake was lazily passed to geo.lookup.
        assert ctx["geo_lookups"] == ["203.0.113.99"]


# ---------------------------------------------------------------------------
# 3. Escalation is impossible
# ---------------------------------------------------------------------------


class TestNoEscalation:
    @pytest.mark.asyncio
    async def test_member_role_endpoint_rejects_superadmin_role(self, app, fake_db):
        """PUT /orgs/{id}/members/{uid} with role='superadmin' → 400 (not in VALID_ROLES)."""
        owner_id = str(uuid.uuid4())
        target_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        fake_db.users[owner_id] = _make_user(owner_id, "owner@example.com")
        fake_db.users[target_id] = _make_user(target_id, "member@example.com")

        async def _orgs_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
            q = " ".join(query.split()).upper()
            if "ORG_MEMBERS" in q and "JOIN ORGS" in q:
                return {"id": org_id, "name": "Acme", "role": "owner"}
            if "ORG_MEMBERS OM JOIN USERS U" in q:
                return {"role": "member", "name": "Member", "email": "member@example.com"}
            return None

        executed: list[tuple[str, tuple[Any, ...]]] = []

        async def _orgs_execute(query: str, *args: Any) -> str:
            executed.append((query, args))
            return "UPDATE 1"

        with (
            patch("app.routes.orgs.fetchrow", side_effect=_orgs_fetchrow),
            patch("app.routes.orgs.execute", side_effect=_orgs_execute),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                resp = await ac.put(
                    f"/api/v1/orgs/{org_id}/members/{target_id}",
                    json={"role": "superadmin"},
                    headers=_auth_headers(owner_id),
                )
                assert resp.status_code == 400
                assert executed == []  # nothing written

                # Crafted payload: extra is_superadmin field is silently dropped
                # (closed Pydantic model) and the role update only touches
                # org_members.role — never users.is_superadmin.
                resp2 = await ac.put(
                    f"/api/v1/orgs/{org_id}/members/{target_id}",
                    json={"role": "admin", "is_superadmin": True},
                    headers=_auth_headers(owner_id),
                )
                assert resp2.status_code == 200
                assert len(executed) == 1
                update_sql = executed[0][0].upper()
                assert "ORG_MEMBERS" in update_sql
                assert "IS_SUPERADMIN" not in update_sql
                assert fake_db.users[target_id].get("is_superadmin") is False

    def test_no_request_model_exposes_is_superadmin(self):
        """No user/member-update request schema carries an is_superadmin field."""
        from app.assets.routes import PatchMeIn
        from app.routes.orgs import UpdateMemberRoleIn

        assert "is_superadmin" not in UpdateMemberRoleIn.model_fields
        assert "is_superadmin" not in PatchMeIn.model_fields
        # Extra keys are dropped, not stored.
        body = UpdateMemberRoleIn.model_validate({"role": "member", "is_superadmin": True})
        assert not hasattr(body, "is_superadmin")

    def test_no_sql_writes_is_superadmin_outside_seed(self):
        """Static audit: no SQL string in backend/app writes is_superadmin.

        Walks every string CONSTANT in backend/app (docstrings excluded — they
        legitimately document the manual-SQL grant procedure) and asserts none
        contains an UPDATE/INSERT touching users.is_superadmin.  The only
        writers in the repo are backend/seed.py and manual SQL.
        """
        import ast
        import pathlib
        import re

        pattern = re.compile(
            r"(UPDATE\s+users\b[\s\S]*is_superadmin|INSERT\s+INTO\s+users\b[\s\S]*is_superadmin)",
            re.IGNORECASE,
        )
        app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
        offenders: list[str] = []
        for py in app_dir.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            tree = ast.parse(py.read_text(encoding="utf-8"))
            docstring_ids: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = getattr(node, "body", [])
                    if (
                        body
                        and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)
                    ):
                        docstring_ids.add(id(body[0].value))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstring_ids
                    and pattern.search(node.value)
                ):
                    offenders.append(f"{py}:{node.lineno}")
        assert offenders == [], f"is_superadmin written outside seed.py: {offenders}"


# ---------------------------------------------------------------------------
# 4. /auth/me reports is_superadmin from the DB row
# ---------------------------------------------------------------------------


class TestAuthMeSuperadmin:
    @pytest.mark.asyncio
    async def test_me_includes_is_superadmin(self, client, fake_db):
        uid = str(uuid.uuid4())
        fake_db.users[uid] = _make_user(uid, "root@example.com", superadmin=True)
        resp = await client.get("/api/v1/auth/me", headers=_auth_headers(uid))
        assert resp.status_code == 200
        assert resp.json()["user"]["is_superadmin"] is True

    @pytest.mark.asyncio
    async def test_me_is_superadmin_false_for_normal_user(self, client, fake_db):
        uid = str(uuid.uuid4())
        fake_db.users[uid] = _make_user(uid, "user@example.com")
        resp = await client.get("/api/v1/auth/me", headers=_auth_headers(uid))
        assert resp.status_code == 200
        assert resp.json()["user"]["is_superadmin"] is False


# ---------------------------------------------------------------------------
# 5. Geo lookup (cache, private IPs, token gating)
# ---------------------------------------------------------------------------


class _FakeResponse:
    status_code = 200

    @staticmethod
    def json() -> dict[str, Any]:
        return {
            "ip": "8.8.8.8", "city": "Cape Town", "region": "Western Cape",
            "country": "ZA", "org": "AS328474 Example ISP",
        }


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; counts GETs on the class."""

    calls: list[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        _FakeAsyncClient.calls.append(url)
        return _FakeResponse()


@pytest.fixture
def geo_db():
    """Patch app.db with a dict-backed ip_geo cache; yield the dict."""
    cache: dict[str, dict[str, Any]] = {}

    async def _fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        if "FROM ip_geo" in query:
            return cache.get(str(args[0]))
        return None

    async def _execute(query: str, *args: Any) -> str:
        if "INSERT INTO ip_geo" in query:
            cache[str(args[0])] = {
                "ip": args[0], "country": args[1], "region": args[2],
                "city": args[3], "org": args[4],
            }
        return "INSERT 0 1"

    with (
        patch("app.db.fetchrow", side_effect=_fetchrow),
        patch("app.db.execute", side_effect=_execute),
    ):
        yield cache


class TestGeoLookup:
    @pytest.mark.asyncio
    async def test_lookup_uses_cache_no_second_http_call(self, geo_db, monkeypatch):
        from app import geo
        from app.config import get_settings

        monkeypatch.setenv("IPINFO_TOKEN", "test-token")
        get_settings.cache_clear()
        _FakeAsyncClient.calls = []
        monkeypatch.setattr("app.geo.httpx.AsyncClient", _FakeAsyncClient)

        first = await geo.lookup("8.8.8.8")
        assert first == {
            "country": "ZA", "region": "Western Cape",
            "city": "Cape Town", "org": "AS328474 Example ISP",
        }
        assert len(_FakeAsyncClient.calls) == 1
        assert "8.8.8.8" in geo_db  # upserted into the cache

        second = await geo.lookup("8.8.8.8")
        assert second == first
        assert len(_FakeAsyncClient.calls) == 1  # cache hit → no second HTTP call

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ip", [
        "127.0.0.1", "10.0.0.8", "192.168.1.10", "172.16.0.1",
        "::1", "fe80::1", "0.0.0.0", "not-an-ip", "", None,
    ])
    async def test_private_or_invalid_ip_short_circuits(self, geo_db, monkeypatch, ip):
        from app import geo
        from app.config import get_settings

        monkeypatch.setenv("IPINFO_TOKEN", "test-token")
        get_settings.cache_clear()
        _FakeAsyncClient.calls = []
        monkeypatch.setattr("app.geo.httpx.AsyncClient", _FakeAsyncClient)

        assert await geo.lookup(ip) is None
        assert _FakeAsyncClient.calls == []  # no HTTP
        assert geo_db == {}  # no cache write

    @pytest.mark.asyncio
    async def test_no_token_means_no_http(self, geo_db, monkeypatch):
        from app import geo
        from app.config import get_settings

        monkeypatch.delenv("IPINFO_TOKEN", raising=False)
        get_settings.cache_clear()
        _FakeAsyncClient.calls = []
        monkeypatch.setattr("app.geo.httpx.AsyncClient", _FakeAsyncClient)

        assert await geo.lookup("8.8.8.8") is None
        assert _FakeAsyncClient.calls == []

    @pytest.mark.asyncio
    async def test_http_failure_never_raises(self, geo_db, monkeypatch):
        from app import geo
        from app.config import get_settings

        monkeypatch.setenv("IPINFO_TOKEN", "test-token")
        get_settings.cache_clear()

        class _Boom:
            def __init__(self, *a: Any, **k: Any) -> None:
                raise RuntimeError("network down")

        monkeypatch.setattr("app.geo.httpx.AsyncClient", _Boom)
        assert await geo.lookup("8.8.8.8") is None  # swallowed


# ---------------------------------------------------------------------------
# 6. Login analytics (best-effort login_events inserts)
# ---------------------------------------------------------------------------


class TestLoginEvents:
    @pytest.mark.asyncio
    async def test_register_and_login_insert_login_events(self, client, fake_db):
        recorded: list[tuple[str, tuple[Any, ...]]] = []

        async def _spy_execute(query: str, *args: Any) -> str:
            recorded.append((query, args))
            return "INSERT 0 1"

        # login_events resolves db.execute lazily via the app.db module, so a
        # targeted patch here catches ONLY its insert (auth/session writes go
        # through their own patched module-level imports).
        with patch("app.db.execute", side_effect=_spy_execute):
            resp = await client.post(
                "/api/v1/auth/register",
                json={"email": "evt@example.com", "password": "password123"},
                headers={
                    "x-forwarded-for": "203.0.113.7, 10.0.0.1",
                    "user-agent": "pytest-agent",
                },
            )
            assert resp.status_code == 201
            user_id = resp.json()["user"]["id"]

            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "evt@example.com", "password": "password123"},
                headers={"x-forwarded-for": "203.0.113.7"},
            )
            assert resp.status_code == 200

        inserts = [r for r in recorded if "INSERT INTO login_events" in r[0]]
        assert len(inserts) == 2  # one for register, one for login
        _, args = inserts[0]
        assert args[0] == user_id
        assert args[1] == "203.0.113.7"  # first X-Forwarded-For hop
        assert args[2] == "pytest-agent"

    @pytest.mark.asyncio
    async def test_login_event_failure_never_breaks_auth(self, client, fake_db):
        async def _broken_execute(query: str, *args: Any) -> str:
            raise RuntimeError("login_events table is on fire")

        with patch("app.db.execute", side_effect=_broken_execute):
            resp = await client.post(
                "/api/v1/auth/register",
                json={"email": "ok@example.com", "password": "password123"},
            )
            assert resp.status_code == 201

            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "ok@example.com", "password": "password123"},
            )
            assert resp.status_code == 200
