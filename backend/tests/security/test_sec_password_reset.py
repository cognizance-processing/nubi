"""Self-service password reset: what it must do, and must not reveal or allow.

Runs against the in-memory fake database. Locking and races are covered on a
real Postgres in ``tests/test_password_reset_pg.py``.
"""

from __future__ import annotations

import asyncio
import re
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi import Request

from app import request_memo
from app.auth import deps
from app.auth.jwt import mint_access_token
from app.auth.verify import VerifiedIdentity
from app.errors import AppError
from app.jobs.report import NullSender, SmtpEmailSender
from app.routes import auth as auth_routes
from tests.conftest import FakeConnection

API = "/api/v1/auth"
EMAIL = "reset-me@example.com"
OLD, NEW = "old-password-1", "new-password-2"


class _Sender:
    """A configured mail transport that records what it is asked to send."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    def send(self, **kw) -> None:
        if self.fail:
            raise smtplib.SMTPServerDisconnected("down")
        self.sent.append(kw)


@pytest.fixture
def mail():
    sender = _Sender()
    with patch("app.jobs.report.get_default_sender", return_value=sender):
        yield sender


async def _settle() -> None:
    """Wait for the detached reset-email tasks."""
    while auth_routes._reset_email_tasks:
        await asyncio.gather(*auth_routes._reset_email_tasks)
        await asyncio.sleep(0)  # let the done-callbacks run


async def _register(client, email: str = EMAIL) -> dict:
    resp = await client.post(f"{API}/register", json={"email": email, "password": OLD})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _forgot(client, email: str = EMAIL):
    resp = await client.post(f"{API}/forgot-password", json={"email": email})
    await _settle()
    return resp


def _token(mail: _Sender, index: int = -1) -> str:
    return re.search(r"#token=(\S+)", mail.sent[index]["body"]).group(1)


async def _reset(client, token: str, password: str = NEW):
    return await client.post(f"{API}/reset-password", json={"token": token, "password": password})


async def _login(client, password: str, email: str = EMAIL):
    return await client.post(f"{API}/login", json={"email": email, "password": password})


# ── The flow ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forgot_reset_login(client, fake_db, mail):
    await _register(client)
    assert (await _forgot(client)).status_code == 200
    assert mail.sent[0]["to"] == EMAIL
    assert "http://localhost:3000/reset-password#token=" in mail.sent[0]["body"]
    assert "attachment_name" not in mail.sent[0]

    resp = await _reset(client, _token(mail))
    assert resp.status_code == 200, resp.text
    cookie = resp.headers.get("set-cookie", "")
    assert "nubi_refresh=" in cookie and "max-age=0" in cookie.lower()

    assert (await _login(client, OLD)).status_code == 401
    assert (await _login(client, NEW)).status_code == 200


@pytest.mark.asyncio
async def test_only_a_digest_of_the_token_is_stored(client, fake_db, mail):
    await _register(client)
    await _forgot(client)
    token = _token(mail)
    (row,) = fake_db.password_reset_tokens.values()
    assert token not in str(row)
    left = row["expires_at"] - datetime.now(tz=timezone.utc)
    assert timedelta(minutes=29) < left <= timedelta(minutes=30)


@pytest.mark.asyncio
async def test_link_works_once_and_voids_the_others(client, fake_db, mail):
    await _register(client)
    await _forgot(client)
    await _forgot(client)
    first, second = _token(mail, 0), _token(mail, 1)
    assert (await _reset(client, first)).status_code == 200
    for dead in (first, second):
        resp = await _reset(client, dead, "another-password-3")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_reset_token"
    assert (await _login(client, NEW)).status_code == 200


@pytest.mark.asyncio
async def test_unknown_and_expired_tokens_are_400_not_401(client, fake_db, mail):
    await _register(client)
    await _forgot(client)
    for row in fake_db.password_reset_tokens.values():
        row["expires_at"] = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
    for token in (_token(mail), "nonsense"):
        assert (await _reset(client, token)).status_code == 400
    assert (await _login(client, OLD)).status_code == 200


@pytest.mark.asyncio
async def test_short_password_is_refused_and_keeps_the_link(client, fake_db, mail):
    await _register(client)
    await _forgot(client)
    assert (await _reset(client, _token(mail), "short")).status_code == 422
    assert (await _reset(client, _token(mail))).status_code == 200


# ── What a reset ends, and what it leaves alone ─────────────────────────────


@pytest.mark.asyncio
async def test_old_access_token_cannot_mint_an_api_key(client, fake_db, mail):
    old_jwt = (await _register(client))["access_token"]
    await _forgot(client)
    assert (await _reset(client, _token(mail))).status_code == 200

    headers = {"Authorization": f"Bearer {old_jwt}"}
    assert (await client.post(f"{API}/api-keys", json={}, headers=headers)).status_code == 401
    assert (await client.get(f"{API}/me", headers=headers)).status_code == 401
    assert (await client.get(f"{API}/scope", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_api_key_mint_ignores_a_cached_user_row(client, fake_db):
    """Another worker changed the password; this worker's row is still cached."""
    body = await _register(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert (await client.get(f"{API}/me", headers=headers)).status_code == 200
    user = fake_db.users[body["user"]["id"]]
    # Replaced, not mutated: the cached copy must keep the old value.
    fake_db.users[body["user"]["id"]] = {
        **user, "password_changed_at": datetime.now(tz=timezone.utc)
    }
    assert (await client.get(f"{API}/me", headers=headers)).status_code == 200  # bounded staleness
    assert (await client.post(f"{API}/api-keys", json={}, headers=headers)).status_code == 401
    assert (await client.get(f"{API}/me", headers=headers)).status_code == 401  # and dropped here


@pytest.mark.asyncio
async def test_login_right_after_a_reset_works(client, fake_db, mail):
    """Same second: a whole-second ``iat`` would predate the change."""
    await _register(client)
    await _forgot(client)
    assert (await _reset(client, _token(mail))).status_code == 200
    jwt = (await _login(client, NEW)).json()["access_token"]
    resp = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_reset_revokes_credentials_but_no_bridges(client, fake_db, mail):
    user_id = (await _register(client))["user"]["id"]
    await _forgot(client)
    seen: list[str] = []
    real = FakeConnection.execute

    async def spy(self, query, *args):
        seen.append(query)
        return await real(self, query, *args)

    with (
        patch.object(FakeConnection, "execute", spy),
        patch("app.bridges.broker.get_broker") as broker,
        patch("app.auth.bridge_tokens.get_bridge_token_store") as bridge_tokens,
    ):
        assert (await _reset(client, _token(mail))).status_code == 200

    sql = " ".join(seen).lower()
    for table in ("sessions", "api_keys", "oauth_grants"):
        assert f"update {table} set revoked_at" in sql
    assert "bridge" not in sql
    broker.assert_not_called()
    bridge_tokens.assert_not_called()
    assert all(s["revoked_at"] for s in fake_db.sessions.values() if s["user_id"] == user_id)


@pytest.mark.asyncio
async def test_reset_does_not_verify_the_email(client, fake_db, mail):
    user_id = (await _register(client))["user"]["id"]
    await _forgot(client)
    await _reset(client, _token(mail))
    assert fake_db.users[user_id]["email_verified"] is False


@pytest.mark.asyncio
async def test_password_changed_at_is_not_exposed(client, fake_db, mail):
    await _register(client)
    await _forgot(client)
    await _reset(client, _token(mail))
    jwt = (await _login(client, NEW)).json()["access_token"]
    resp = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {jwt}"})
    assert "password_changed_at" not in resp.text


# ── Forgot: no enumeration, live cap, no row without an email ───────────────


@pytest.mark.asyncio
async def test_forgot_answers_every_address_alike(client, fake_db, mail):
    await _register(client)
    known, unknown = await _forgot(client), await _forgot(client, "nobody@example.com")
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert len(mail.sent) == 1


@pytest.mark.asyncio
async def test_forgot_does_not_wait_for_the_mail_server(client, fake_db):
    """The answer is out before the lookup or the send has happened."""
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(_email):
        started.set()
        await release.wait()

    with patch.object(auth_routes, "_send_password_reset", slow):
        resp = await asyncio.wait_for(
            client.post(f"{API}/forgot-password", json={"email": EMAIL}), timeout=5
        )
        assert resp.status_code == 200
        assert not release.is_set()
        release.set()
        await _settle()


@pytest.mark.asyncio
async def test_google_only_account_gets_no_link(client, fake_db, mail):
    user_id = (await _register(client))["user"]["id"]
    fake_db.users[user_id]["password_hash"] = None
    assert (await _forgot(client)).status_code == 200
    assert mail.sent == [] and fake_db.password_reset_tokens == {}


@pytest.mark.asyncio
async def test_link_is_dead_once_the_account_has_no_password(client, fake_db, mail):
    """A link sent before Google was linked must not set a password after."""
    user_id = (await _register(client))["user"]["id"]
    await _forgot(client)
    fake_db.users[user_id]["password_hash"] = None
    assert (await _reset(client, _token(mail))).status_code == 400
    assert fake_db.users[user_id]["password_hash"] is None


@pytest.mark.asyncio
async def test_at_most_three_live_links(client, fake_db, mail):
    await _register(client)
    for _ in range(5):
        assert (await _forgot(client)).status_code == 200
    assert len(mail.sent) == len(fake_db.password_reset_tokens) == 3

    # Only LIVE links count: an expired one makes room.
    next(iter(fake_db.password_reset_tokens.values()))["expires_at"] = datetime.now(
        tz=timezone.utc
    ) - timedelta(seconds=1)
    await _forgot(client)
    assert len(mail.sent) == 4


@pytest.mark.asyncio
async def test_no_row_without_smtp(client, fake_db):
    await _register(client)
    with patch("app.jobs.report.get_default_sender", return_value=NullSender()):
        assert (await _forgot(client)).status_code == 200
    assert fake_db.password_reset_tokens == {}


@pytest.mark.asyncio
async def test_failed_send_does_not_count(client, fake_db):
    await _register(client)
    broken = _Sender(fail=True)
    with patch("app.jobs.report.get_default_sender", return_value=broken):
        for _ in range(4):
            assert (await _forgot(client)).status_code == 200
    assert fake_db.password_reset_tokens == {}
    working = _Sender()
    with patch("app.jobs.report.get_default_sender", return_value=working):
        await _forgot(client)
    assert len(working.sent) == 1


# ── Rate limit ──────────────────────────────────────────────────────────────


@pytest.fixture
def limits(monkeypatch):
    from app.middleware import ratelimit

    monkeypatch.setattr(ratelimit._cfg, "enabled", True)
    monkeypatch.setattr(ratelimit, "get_redis", lambda: None)
    monkeypatch.setenv("NUBI_RATELIMIT_PASSWORD_RESET_PER_ACCOUNT_RPM", "2")
    ratelimit._buckets.clear()
    yield ratelimit
    ratelimit._buckets.clear()


def _drain(consume, email: str) -> None:
    for _ in range(100):
        if not consume(email)[0]:
            return
    raise AssertionError("never limited")


def test_reset_requests_have_their_own_bucket(limits):
    _drain(limits.consume_password_reset_request, EMAIL)
    assert limits.consume_login_attempt(EMAIL)[0] is True
    assert limits.consume_password_reset_request("other@example.com")[0] is True
    _drain(limits.consume_login_attempt, "third@example.com")
    assert limits.consume_password_reset_request("third@example.com")[0] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [True, False])
async def test_overflow_is_429_whether_or_not_the_account_exists(
    client, fake_db, mail, registered
):
    if registered:
        await _register(client)
    with patch(
        "app.middleware.ratelimit.consume_password_reset_request", return_value=(False, 7)
    ):
        resp = await _forgot(client)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    assert mail.sent == []


# ── Login racing a reset ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_fails_if_the_password_changed_while_verifying(client, fake_db):
    user_id = (await _register(client))["user"]["id"]
    sessions_before = len(fake_db.sessions)
    real = auth_routes.issue_refresh_for_password

    async def after_a_reset(*args, **kw):
        fake_db.users[user_id]["password_hash"] = "set-by-the-reset"
        return await real(*args, **kw)

    with patch.object(auth_routes, "issue_refresh_for_password", after_a_reset):
        resp = await _login(client, OLD)
    assert resp.status_code == 401
    assert len(fake_db.sessions) == sessions_before


# ── The access-token check ──────────────────────────────────────────────────


def test_iat_is_fractional_and_exp_is_not():
    at = datetime(2030, 1, 1, 0, 0, 0, 250_000, tzinfo=timezone.utc)
    claims = pyjwt.decode(
        mint_access_token("u", issued_at=at), options={"verify_signature": False}
    )
    assert claims["iat"] == at.timestamp()
    assert isinstance(claims["exp"], int)


@pytest.mark.parametrize("offset,ok", [(-1.0, False), (-0.001, False), (0.0, True), (0.5, True)])
def test_token_must_not_predate_the_password_change(offset, ok):
    changed = datetime(2030, 1, 1, 0, 0, 0, 500_000, tzinfo=timezone.utc)
    claims = {"sub": "u", "iat": changed.timestamp() + offset}
    row = {"password_changed_at": changed}
    if ok:
        deps.require_token_after_password_change(claims, row)
    else:
        with pytest.raises(AppError) as err:
            deps.require_token_after_password_change(claims, row)
        assert err.value.status == 401


def test_accounts_that_never_changed_password_are_unaffected():
    deps.require_token_after_password_change({"sub": "u", "iat": 1}, {"password_changed_at": None})
    deps.require_token_after_password_change({"sub": "u", "iat": 1}, {})


def _request(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": [],
                    "query_string": b"", "server": ("t", 80), "scheme": "http"})


@pytest.mark.parametrize("method,path,fresh", [
    ("GET", "/api/v1/auth/me", False),
    ("POST", "/api/v1/auth/api-keys", True),
    ("PATCH", "/api/v1/auth/me", True),
    ("POST", "/api/v1/query", False),
    ("POST", "/api/v1/query/", False),
    ("POST", "/api/v1/query/estimate", False),
    ("POST", "/api/v1/metrics/m1/query", False),
    ("POST", "/api/v1/boards/b1/providers/p1/data", False),
    # Next door to the reads, but writes:
    ("POST", "/api/v1/query/registry", True),
    ("DELETE", "/api/v1/query", True),
    ("POST", "/api/v1/metrics", True),
    ("POST", "/api/v1/queries/q1/parameterize", True),
    ("POST", "/evil/api/v1/query", True),
])
def test_which_requests_read_the_user_row_fresh(method, path, fresh):
    assert deps._needs_fresh_user(_request(method, path)) is fresh


@pytest.mark.asyncio
async def test_fresh_load_skips_the_shared_row_but_asks_once_per_request():
    uid = str(uuid.uuid4())
    db = AsyncMock(return_value={"id": uid, "password_changed_at": None})
    with patch("app.auth.deps.fetchrow", db):
        for expected in (1, 1):  # second request: shared
            tok = request_memo._memo.set({})
            await deps.load_user_row(uid)
            request_memo._memo.reset(tok)
            assert db.await_count == expected
        tok = request_memo._memo.set({})
        await deps.load_user_row(uid, fresh=True)
        await deps.load_user_row(uid, fresh=True)
        request_memo._memo.reset(tok)
        assert db.await_count == 2


def _identity(sub: str, kind: str = "access", iat: float = 1.0) -> VerifiedIdentity:
    return VerifiedIdentity(
        kind=kind, user_id=sub, org=None, project=None, roles=[], policies={}, scope=[],
        embed_origin=None, datastore=None, raw_claims={"sub": sub, "iat": iat},
    )


WRITE = ("POST", "/api/v1/flows")
READ = ("POST", "/api/v1/query")


@pytest.mark.asyncio
async def test_verified_identity_refuses_a_token_from_before_the_change():
    uid = str(uuid.uuid4())
    row = {"id": uid, "password_changed_at": datetime.now(tz=timezone.utc)}
    with patch("app.auth.deps.fetchrow", AsyncMock(return_value=row)):
        for req in (WRITE, READ):
            with pytest.raises(AppError) as err:
                await deps._require_current_access_token(_request(*req), _identity(uid))
            assert err.value.status == 401


@pytest.mark.asyncio
async def test_non_uuid_subject_is_not_looked_up():
    db = AsyncMock()
    with patch("app.auth.deps.fetchrow", db):
        await deps._require_current_access_token(_request(*WRITE), _identity("dev-user"))
    db.assert_not_awaited()


@pytest.mark.asyncio
async def test_subject_without_a_user_row_stays_valid():
    with patch("app.auth.deps.fetchrow", AsyncMock(return_value=None)):
        await deps._require_current_access_token(_request(*WRITE), _identity(str(uuid.uuid4())))


@pytest.mark.asyncio
async def test_lookup_failure_refuses_writes_and_allows_reads():
    identity = _identity(str(uuid.uuid4()))
    with patch("app.auth.deps.fetchrow", AsyncMock(side_effect=OSError("db down"))):
        await deps._require_current_access_token(_request(*READ), identity)
        with pytest.raises(AppError) as err:
            await deps._require_current_access_token(_request(*WRITE), identity)
    assert err.value.status == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,checked", [("access", True), ("embed", False)])
async def test_only_first_party_tokens_are_checked(kind, checked):
    check = AsyncMock()
    creds = MagicMock(credentials="a.b.c")
    with (
        patch.object(deps, "verify_token_async", AsyncMock(return_value=_identity("s", kind))),
        patch.object(deps, "_resolve_opaque_credential", AsyncMock(return_value=False)),
        patch.object(deps, "_require_current_access_token", check),
        patch.object(deps, "require_board_bound_route", MagicMock()),
        patch.object(deps, "_maybe_pin_host_mode_org", AsyncMock()),
    ):
        await deps.verified_identity(_request(*READ), creds)
    assert check.await_count == int(checked)


# ── Google link that clears a password ──────────────────────────────────────


@pytest.mark.asyncio
async def test_first_google_link_outdates_earlier_access_tokens():
    uid = str(uuid.uuid4())
    execute = AsyncMock(return_value="OK")

    async def fetchrow(sql, *args):
        return None if "oauth_accounts" in sql else {"password_hash": "h"}

    with (
        patch.object(auth_routes, "fetchrow", fetchrow),
        patch.object(auth_routes, "execute", execute),
        patch.object(auth_routes, "forget_user_row") as forget,
        patch("app.auth.sessions.revoke_all_for_user", AsyncMock()),
    ):
        assert await auth_routes._secure_account_on_first_provider_link(uid, "google") is True
    sql, user_id, changed_at = execute.await_args_list[0].args
    assert "password_hash = NULL" in sql and "password_changed_at = $2" in sql
    assert user_id == uid
    assert abs((datetime.now(tz=timezone.utc) - changed_at).total_seconds()) < 10
    forget.assert_called_once_with(uid)


# ── SMTP transport ──────────────────────────────────────────────────────────


def _smtp(**kw) -> SmtpEmailSender:
    return SmtpEmailSender(host="mail.example.com", from_addr="nubi@example.com", **kw)


@pytest.mark.parametrize("port,cls", [(587, "SMTP"), (465, "SMTP_SSL")])
@pytest.mark.parametrize("verify", [True, False])
def test_tls_verifies_the_certificate_unless_opted_out(port, cls, verify):
    with patch(f"app.jobs.report.smtplib.{cls}") as server:
        _smtp(port=port, tls_verify=verify).send("a@example.com", "s", "b")
    conn = server.return_value.__enter__.return_value
    context = (
        server.call_args.kwargs["context"] if port == 465
        else conn.starttls.call_args.kwargs["context"]
    )
    assert context.verify_mode == (ssl.CERT_REQUIRED if verify else ssl.CERT_NONE)
    assert context.check_hostname is verify
    conn.send_message.assert_called_once()


def test_verification_is_the_default(monkeypatch):
    from app.config import get_settings
    from app.jobs.report import get_default_sender

    monkeypatch.setenv("SMTP_HOST", "mail.example.com")
    get_settings.cache_clear()
    assert get_default_sender().tls_verify is True
    monkeypatch.setenv("SMTP_TLS_VERIFY", "false")
    get_settings.cache_clear()
    assert get_default_sender().tls_verify is False


@pytest.mark.parametrize("attachment,count", [((), 0), (("r.csv", "a,b"), 1)])
def test_attachment_is_optional(attachment, count):
    with patch("app.jobs.report.smtplib.SMTP") as server:
        _smtp(use_tls=False).send("a@example.com", "s", "b", *attachment)
    msg = server.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert len(list(msg.iter_attachments())) == count
    NullSender().send("a@example.com", "s", "b", *attachment)
