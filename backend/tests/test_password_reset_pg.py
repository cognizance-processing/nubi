"""Password reset on a real Postgres: what it revokes, locking, and races.

Opt-in, like ``test_pg_integration.py``.  Use a THROWAWAY database; the
migrations are applied to its public schema:

    RUN_PG_TESTS=1 DATABASE_URL=postgresql://... pytest tests/test_password_reset_pg.py -v

The race tests stop one side at a chosen point inside its transaction, start the
other, and assert how they end, so they do not depend on timing luck.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_PG_TESTS"),
    reason="Set RUN_PG_TESTS=1 and DATABASE_URL to run PG integration tests.",
)

API = "/api/v1/auth"
OLD, NEW = "old-password-1", "new-password-2"
#: Long enough for a query that is not blocked to finish.
BLOCKED_S = 0.5


class _Sender:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, **kw) -> None:
        self.sent.append(kw)


@pytest_asyncio.fixture
async def pool(pg_db):
    """The app's own pool, on the migrated test database."""
    import asyncpg

    import app.db as db
    from app.auth.api_keys import set_api_key_store_for_tests

    pool = await asyncpg.create_pool(dsn=os.environ["DATABASE_URL"], min_size=1, max_size=8)
    db._pool = pool
    set_api_key_store_for_tests(None)  # the real store
    try:
        yield pool
    finally:
        db._pool = None
        await pool.close()


@pytest_asyncio.fixture
async def client(pool):
    with (
        patch("app.db.init_db", AsyncMock()),
        patch("app.db.close_db", AsyncMock()),
    ):
        import main

        app = main.create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as ac:
            yield ac


@pytest.fixture
def mail():
    sender = _Sender()
    with patch("app.jobs.report.get_default_sender", return_value=sender):
        yield sender


class _Account:
    def __init__(self, email: str, user_id: str, jwt: str, refresh: str) -> None:
        self.email, self.user_id, self.jwt, self.refresh = email, user_id, jwt, refresh


@pytest_asyncio.fixture
async def account(client, pool):
    email = f"pg-{uuid.uuid4().hex[:10]}@example.com"
    resp = await client.post(f"{API}/register", json={"email": email, "password": OLD})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    acct = _Account(email, body["user"]["id"], body["access_token"], resp.cookies["nubi_refresh"])
    client.cookies.clear()
    yield acct
    await pool.execute(
        "DELETE FROM orgs WHERE id IN (SELECT org_id FROM org_members WHERE user_id = $1::uuid)",
        acct.user_id,
    )
    await pool.execute("DELETE FROM users WHERE id = $1::uuid", acct.user_id)


async def _link(acct: _Account, mail: _Sender) -> str:
    from app.routes.auth import _send_password_reset

    await _send_password_reset(acct.email)
    return re.search(r"#token=(\S+)", mail.sent[-1]["body"]).group(1)


async def _reset(client, token: str, password: str = NEW):
    return await client.post(f"{API}/reset-password", json={"token": token, "password": password})


async def _login(client, acct: _Account, password: str):
    resp = await client.post(f"{API}/login", json={"email": acct.email, "password": password})
    client.cookies.clear()
    return resp


async def _live_sessions(pool, acct: _Account) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM sessions WHERE user_id = $1::uuid AND revoked_at IS NULL",
        acct.user_id,
    )


async def _me(client, jwt: str) -> int:
    resp = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {jwt}"})
    return resp.status_code


async def _assert_blocked(task: asyncio.Task) -> None:
    done, _ = await asyncio.wait({task}, timeout=BLOCKED_S)
    assert not done, "expected to wait for the other transaction"


# ── What a reset does ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_end_to_end(client, pool, account, mail):
    auth = {"Authorization": f"Bearer {account.jwt}"}
    resp = await client.post(f"{API}/api-keys", json={"name": "ci"}, headers=auth)
    assert resp.status_code == 201, resp.text
    api_key = resp.json()["key"]
    org_id = await pool.fetchval(
        "SELECT org_id FROM org_members WHERE user_id = $1::uuid", account.user_id
    )
    client_id = f"c-{uuid.uuid4().hex}"
    await pool.execute(
        "INSERT INTO oauth_clients (id, client_name, redirect_uris) VALUES ($1, 'c', '{}')",
        client_id,
    )
    from app.auth.oauth import get_oauth_store

    grant_id = await pool.fetchval(
        "INSERT INTO oauth_grants (client_id, user_id, org_id, scope) "
        "VALUES ($1, $2::uuid, $3, 'mcp') RETURNING id",
        client_id, account.user_id, org_id,
    )
    oauth_access, _refresh = await get_oauth_store().issue_tokens(grant_id)
    assert await get_oauth_store().resolve_access(oauth_access)

    token = await _link(account, mail)
    assert (await _reset(client, token)).status_code == 200

    assert await _live_sessions(pool, account) == 0
    assert await get_oauth_store().resolve_access(oauth_access) is None
    assert await _me(client, api_key) == 401
    assert await _me(client, account.jwt) == 401
    resp = await client.post(f"{API}/api-keys", json={}, headers=auth)
    assert resp.status_code == 401
    resp = await client.post(f"{API}/refresh", headers={"Cookie": f"nubi_refresh={account.refresh}"})
    assert resp.status_code == 401

    assert (await _login(client, account, OLD)).status_code == 401
    resp = await _login(client, account, NEW)  # same second as the reset
    assert resp.status_code == 200
    assert await _me(client, resp.json()["access_token"]) == 200
    assert await pool.fetchval(
        "SELECT email_verified FROM users WHERE id = $1::uuid", account.user_id
    ) is False
    await pool.execute("DELETE FROM oauth_clients WHERE id = $1", client_id)


@pytest.mark.asyncio
async def test_reset_leaves_bridge_tokens_alone(client, pool, account, mail):
    async def snapshot():
        return await pool.fetch("SELECT * FROM bridge_tokens ORDER BY id")

    org_id = await pool.fetchval(
        "SELECT org_id FROM org_members WHERE user_id = $1::uuid", account.user_id
    )
    from app.auth.bridge_tokens import PgBridgeTokenStore

    bridge_id = await pool.fetchval(
        "INSERT INTO bridges (org_id, name, created_by) VALUES ($1, 'b', $2::uuid) RETURNING id",
        org_id, account.user_id,
    )
    await PgBridgeTokenStore().mint(
        str(org_id), str(bridge_id), "t", created_by=account.user_id
    )
    before = await snapshot()
    assert before
    with patch("app.bridges.broker.get_broker") as broker:
        assert (await _reset(client, await _link(account, mail))).status_code == 200
    assert await snapshot() == before
    broker.assert_not_called()


@pytest.mark.asyncio
async def test_google_only_account_gets_no_row(pool, account, mail):
    from app.routes.auth import _send_password_reset

    await pool.execute(
        "UPDATE users SET password_hash = NULL WHERE id = $1::uuid", account.user_id
    )
    await _send_password_reset(account.email)
    assert mail.sent == []
    assert await pool.fetchval(
        "SELECT count(*) FROM password_reset_tokens WHERE user_id = $1::uuid", account.user_id
    ) == 0


@pytest.mark.asyncio
async def test_failed_send_deletes_the_row(pool, account):
    from app.routes.auth import _send_password_reset

    class _Broken:
        def send(self, **kw):
            raise OSError("down")

    with patch("app.jobs.report.get_default_sender", return_value=_Broken()):
        await _send_password_reset(account.email)
    assert await pool.fetchval(
        "SELECT count(*) FROM password_reset_tokens WHERE user_id = $1::uuid", account.user_id
    ) == 0


@pytest.mark.asyncio
async def test_expired_link_is_refused_and_frees_the_cap(client, pool, account, mail):
    token = await _link(account, mail)
    await pool.execute(
        "UPDATE password_reset_tokens SET expires_at = $2 WHERE user_id = $1::uuid",
        account.user_id, datetime.now(tz=timezone.utc) - timedelta(seconds=1),
    )
    assert (await _reset(client, token)).status_code == 400
    for _ in range(4):
        await _link(account, mail)
    assert len(mail.sent) == 4  # the expired one, then three live


# ── Concurrency ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_link_cannot_be_used_twice_at_once(client, account, mail):
    token = await _link(account, mail)
    results = await asyncio.gather(
        *(_reset(client, token, f"new-password-{i}") for i in range(4))
    )
    assert sorted(r.status_code for r in results) == [200, 400, 400, 400]


class _Pause:
    """A point in one side's transaction where it stops until released."""

    def __init__(self) -> None:
        self.reached, self.release = asyncio.Event(), asyncio.Event()

    async def here(self) -> None:
        self.reached.set()
        await self.release.wait()

    async def wait_reached(self) -> None:
        await asyncio.wait_for(self.reached.wait(), timeout=10)

    @contextmanager
    def patching(self, target, name, replacement):
        """Patch, and release on the way out: a failed assertion must not leave
        a transaction (and its locks) open for the fixtures to wait on."""
        try:
            with patch.object(target, name, replacement):
                yield
        finally:
            self.release.set()


def _pause_after_session_insert(pause: _Pause):
    """Hold a sign-in or a refresh after it inserted its session, uncommitted."""
    from app.auth import sessions

    real = sessions.issue_refresh

    async def paused(*args, **kw):
        out = await real(*args, **kw)
        await pause.here()
        return out

    return pause.patching(sessions, "issue_refresh", paused)


def _pause_after_statement(pause: _Pause, marker: str):
    """Hold a transaction of ``app.routes.auth`` after the statement with *marker*."""
    from app.routes import auth

    real = auth.get_connection

    class _Conn:
        def __init__(self, conn) -> None:
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def execute(self, query, *args):
            out = await self._conn.execute(query, *args)
            if marker in query:
                await pause.here()
            return out

    @asynccontextmanager
    async def wrapped():
        async with real() as conn:
            yield _Conn(conn)

    return pause.patching(auth, "get_connection", wrapped)


def _pause_reset_after_password_update(pause: _Pause):
    """Hold a reset after it set the password, before it revokes sessions."""
    return _pause_after_statement(pause, "password_changed_at")


@pytest.mark.asyncio
async def test_cap_holds_under_concurrent_requests(pool, account, mail):
    """The third link is uncommitted when the request for a fourth counts them."""
    from app.routes.auth import _send_password_reset

    await _link(account, mail)
    await _link(account, mail)
    pause = _Pause()
    with _pause_after_statement(pause, "INSERT INTO password_reset_tokens"):
        third = asyncio.create_task(_send_password_reset(account.email))
        await pause.wait_reached()
        fourth = asyncio.create_task(_send_password_reset(account.email))
        await _assert_blocked(fourth)
        pause.release.set()
        await asyncio.gather(third, fourth)
    assert len(mail.sent) == 3
    assert await pool.fetchval(
        "SELECT count(*) FROM password_reset_tokens WHERE user_id = $1::uuid", account.user_id
    ) == 3


@pytest.mark.asyncio
async def test_refresh_first_then_reset_revokes_the_new_session(client, pool, account, mail):
    """The rotated-in session is uncommitted when the reset starts."""
    token = await _link(account, mail)
    pause = _Pause()
    with _pause_after_session_insert(pause):
        refresh = asyncio.create_task(
            client.post(f"{API}/refresh", headers={"Cookie": f"nubi_refresh={account.refresh}"})
        )
        await pause.wait_reached()
        # Inserted in the rotation's own transaction, so not visible yet.
        assert await pool.fetchval(
            "SELECT count(*) FROM sessions WHERE user_id = $1::uuid", account.user_id
        ) == 1
        reset = asyncio.create_task(_reset(client, token))
        await _assert_blocked(reset)
        pause.release.set()
        refreshed = await refresh
        assert (await reset).status_code == 200

    assert await _live_sessions(pool, account) == 0
    # The refresh itself succeeded; what it handed out is dead all the same.
    assert refreshed.status_code == 200
    assert await _me(client, refreshed.json()["access_token"]) == 401


@pytest.mark.asyncio
async def test_reset_first_then_refresh_is_refused(client, pool, account, mail):
    """No deadlock either: the refresh must not hold the session row while waiting."""
    token = await _link(account, mail)
    pause = _Pause()
    with _pause_reset_after_password_update(pause):
        reset = asyncio.create_task(_reset(client, token))
        await pause.wait_reached()
        refresh = asyncio.create_task(
            client.post(f"{API}/refresh", headers={"Cookie": f"nubi_refresh={account.refresh}"})
        )
        await _assert_blocked(refresh)
        pause.release.set()
        assert (await reset).status_code == 200
        assert (await refresh).status_code == 401
    assert await _live_sessions(pool, account) == 0


@pytest.mark.asyncio
async def test_login_first_then_reset_revokes_its_session(client, pool, account, mail):
    token = await _link(account, mail)
    pause = _Pause()
    with _pause_after_session_insert(pause):
        login = asyncio.create_task(_login(client, account, OLD))
        await pause.wait_reached()
        reset = asyncio.create_task(_reset(client, token))
        await _assert_blocked(reset)
        pause.release.set()
        logged_in = await login
        assert (await reset).status_code == 200

    assert await _live_sessions(pool, account) == 0
    assert logged_in.status_code == 200
    assert await _me(client, logged_in.json()["access_token"]) == 401


@pytest.mark.asyncio
async def test_reset_first_then_login_with_the_old_password_fails(client, pool, account, mail):
    """The sign-in read and verified the OLD hash; the reset commits after that."""
    token = await _link(account, mail)
    pause = _Pause()
    with _pause_reset_after_password_update(pause):
        reset = asyncio.create_task(_reset(client, token))
        await pause.wait_reached()
        # The reset is uncommitted, so this still reads and verifies the old hash.
        login = asyncio.create_task(_login(client, account, OLD))
        await _assert_blocked(login)
        pause.release.set()
        assert (await reset).status_code == 200
        assert (await login).status_code == 401
    assert await _live_sessions(pool, account) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["rotate_refresh", "issue_refresh_for_password"])
async def test_reset_between_the_session_and_the_access_token(client, account, mail, step):
    """The session committed, the reset ran, and only then is the JWT minted."""
    from app.routes import auth

    token = await _link(account, mail)
    real = getattr(auth, step)

    async def then_reset(*args, **kw):
        out = await real(*args, **kw)
        assert (await _reset(client, token)).status_code == 200
        return out

    with patch.object(auth, step, then_reset):
        if step == "rotate_refresh":
            resp = await client.post(
                f"{API}/refresh", headers={"Cookie": f"nubi_refresh={account.refresh}"}
            )
        else:
            resp = await _login(client, account, OLD)
    assert resp.status_code == 200
    assert await _me(client, resp.json()["access_token"]) == 401


@pytest.mark.asyncio
async def test_refresh_still_rotates(client, pool, account):
    resp = await client.post(
        f"{API}/refresh", headers={"Cookie": f"nubi_refresh={account.refresh}"}
    )
    assert resp.status_code == 200
    assert await _me(client, resp.json()["access_token"]) == 200
    assert await _live_sessions(pool, account) == 1
