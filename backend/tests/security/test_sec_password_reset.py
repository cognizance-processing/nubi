"""Password reset: no account enumeration, single-use links, and no access survives a reset."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

FORGOT = "/api/v1/auth/forgot-password"
RESET = "/api/v1/auth/reset-password"
LOGIN = "/api/v1/auth/login"
EMAIL = "owner@example.com"
OLD = "old-password-1"
NEW = "new-password-2"


def _add_user(fake_db, email=EMAIL, password: str | None = OLD) -> str:
    from app.auth.passwords import hash_password

    uid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    fake_db.users[uid] = {
        "id": uid, "email": email, "name": "O", "avatar_url": None, "email_verified": False,
        "password_hash": hash_password(password) if password else None,
        "created_at": now, "updated_at": now,
    }
    return uid


@pytest.fixture
def mail():
    """The emails sent, captured by a NullSender standing in for SMTP."""
    from app.jobs.report import NullSender

    sender = NullSender()
    with patch("app.jobs.report.get_default_sender", return_value=sender):
        yield sender.sent


async def _request_token(client, mail, email=EMAIL) -> str:
    resp = await client.post(FORGOT, json={"email": email})
    assert resp.status_code == 200
    return mail[-1]["body"].split("#token=")[1].split()[0]


async def _reset(client, token, password=NEW):
    return await client.post(RESET, json={"token": token, "password": password})


async def _login(client, password):
    return (await client.post(LOGIN, json={"email": EMAIL, "password": password})).status_code


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forgot_reset_login(client, fake_db, mail):
    _add_user(fake_db)
    token = await _request_token(client, mail, "Owner@Example.com")
    assert mail[0]["to"] == EMAIL
    assert f"http://localhost:3000/reset-password#token={token}" in mail[0]["body"]

    assert (await _reset(client, token)).status_code == 200
    assert await _login(client, OLD) == 401
    assert await _login(client, NEW) == 200


# ── Bad tokens ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_used_and_expired_tokens_answer_alike(client, fake_db, mail):
    _add_user(fake_db)
    expired = await _request_token(client, mail)
    used = await _request_token(client, mail)
    digest = hashlib.sha256(expired.encode()).hexdigest()
    fake_db.password_reset_tokens[digest]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    replies = [await _reset(client, "no-such-token"), await _reset(client, expired)]
    assert (await _reset(client, used)).status_code == 200
    replies.append(await _reset(client, used, "third-password-3"))

    assert {r.status_code for r in replies} == {400}
    assert len({r.content for r in replies}) == 1
    assert await _login(client, NEW) == 200


@pytest.mark.asyncio
async def test_a_reset_spends_every_outstanding_link(client, fake_db, mail):
    _add_user(fake_db)
    first = await _request_token(client, mail)
    second = await _request_token(client, mail)
    # A newer link does not break the older one, but a reset ends them all.
    assert (await _reset(client, first)).status_code == 200
    assert (await _reset(client, second)).status_code == 400


@pytest.mark.asyncio
async def test_a_short_password_is_400_and_keeps_the_link(client, fake_db, mail):
    _add_user(fake_db)
    token = await _request_token(client, mail)
    resp = await _reset(client, token, "short")
    assert resp.status_code == 400
    assert "8 characters" in resp.json()["error"]["message"]
    assert (await _reset(client, token)).status_code == 200


# ── Accounts without a password ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_only_accounts_get_no_link(client, fake_db, mail):
    _add_user(fake_db, password=None)
    assert (await client.post(FORGOT, json={"email": EMAIL})).status_code == 200
    assert mail == []
    assert fake_db.password_reset_tokens == {}


@pytest.mark.asyncio
async def test_linking_a_provider_voids_an_outstanding_link(client, fake_db, mail):
    from app.routes import auth

    uid = _add_user(fake_db)
    token = await _request_token(client, mail)
    assert await auth._secure_account_on_first_provider_link(uid, "google") is True

    resp = await _reset(client, token)
    unknown = await _reset(client, "no-such-token")
    assert (resp.status_code, resp.content) == (400, unknown.content)
    assert fake_db.users[uid]["password_hash"] is None


# ── Enumeration and limits ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forgot_answers_alike_for_any_address(client, fake_db, mail):
    _add_user(fake_db)
    _add_user(fake_db, "google@example.com", password=None)
    replies = [
        await client.post(FORGOT, json={"email": e})
        for e in (EMAIL, "google@example.com", "nobody@example.com")
    ]
    assert {r.status_code for r in replies} == {200}
    assert len({r.content for r in replies}) == 1
    assert len(mail) == 1


@pytest.mark.asyncio
async def test_forgot_does_no_account_work_before_answering():
    """The lookup, token and SMTP send only ever run as a background task."""
    from fastapi import BackgroundTasks

    from app.routes import auth

    background = BackgroundTasks()
    # No DB is patched in: any lookup here would raise.
    await auth.forgot_password(auth.ForgotPasswordIn(email=EMAIL), background)
    assert [t.func for t in background.tasks] == [auth._send_password_reset]


@pytest.mark.asyncio
async def test_at_most_three_links_an_hour(client, fake_db, mail):
    _add_user(fake_db)
    for _ in range(5):
        assert (await client.post(FORGOT, json={"email": EMAIL})).status_code == 200
    assert len(mail) == 3
    assert len(fake_db.password_reset_tokens) == 3


@pytest.mark.asyncio
async def test_reset_requests_have_their_own_rate_bucket(client, fake_db, mail, monkeypatch):
    from app.middleware import ratelimit

    monkeypatch.setattr(ratelimit._cfg, "_loaded", True, raising=False)
    monkeypatch.setattr(ratelimit._cfg, "enabled", True, raising=False)
    monkeypatch.setattr(ratelimit._cfg, "burst_factor", 1.0, raising=False)
    monkeypatch.setenv("NUBI_RATELIMIT_PASSWORD_RESET_PER_ACCOUNT_RPM", "2")
    monkeypatch.setenv("NUBI_RATELIMIT_LOGIN_PER_ACCOUNT_RPM", "2")
    ratelimit._buckets.clear()
    try:
        allowed = [ratelimit.consume_password_reset_request(e)[0] for e in (EMAIL, EMAIL.upper(), EMAIL)]
        assert allowed == [True, True, False]
        # Exhausting it leaves the sign-in allowance whole, and other addresses alone.
        assert ratelimit.consume_login_attempt(EMAIL)[0] is True
        assert ratelimit.consume_password_reset_request("nobody@example.com")[0] is True

        # The route enforces it whether or not the address has an account.
        monkeypatch.setattr(ratelimit._cfg, "enabled", False, raising=False)
        with patch("app.middleware.ratelimit.consume_password_reset_request", return_value=(False, 20)):
            resp = await client.post(FORGOT, json={"email": "nobody@example.com"})
        assert resp.status_code == 429
    finally:
        ratelimit._buckets.clear()


# ── Nothing survives a reset ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_revokes_sessions_keys_and_grants_of_that_user_only(client, fake_db, mail):
    uid = _add_user(fake_db)
    other = _add_user(fake_db, "other@example.com")
    assert await _login(client, OLD) == 200
    for owner in (uid, other):
        for table in (fake_db.api_keys, fake_db.oauth_grants):
            table[f"row-{owner}"] = {"user_id": owner, "revoked_at": None}

    token = await _request_token(client, mail)
    assert (await _reset(client, token)).status_code == 200

    assert fake_db.sessions and all(s["revoked_at"] for s in fake_db.sessions.values())
    for table in (fake_db.api_keys, fake_db.oauth_grants):
        assert table[f"row-{uid}"]["revoked_at"] is not None
        assert table[f"row-{other}"]["revoked_at"] is None


@pytest.mark.asyncio
async def test_a_failed_reset_revokes_nothing(client, fake_db, mail):
    _add_user(fake_db)
    assert await _login(client, OLD) == 200
    assert (await _reset(client, "no-such-token")).status_code == 400
    assert all(s["revoked_at"] is None for s in fake_db.sessions.values())
    assert await _login(client, OLD) == 200


# ── The raw token stays in the email ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_the_hash_is_stored_and_nothing_logged(client, fake_db, mail, caplog):
    caplog.set_level(logging.DEBUG)
    _add_user(fake_db)
    token = await _request_token(client, mail)
    assert list(fake_db.password_reset_tokens) == [hashlib.sha256(token.encode()).hexdigest()]
    assert token not in str(fake_db.password_reset_tokens)
    await _reset(client, token)
    assert token not in caplog.text


@pytest.mark.asyncio
async def test_a_failed_send_is_logged_without_the_link(client, fake_db, caplog):
    sent: list[str] = []

    class Broken:
        def send(self, to, subject, body, attachment_name, attachment_data):
            sent.append(body)
            raise OSError(f"refused {to}: {body}")

    caplog.set_level(logging.DEBUG)
    _add_user(fake_db)
    with patch("app.jobs.report.get_default_sender", return_value=Broken()):
        resp = await client.post(FORGOT, json={"email": EMAIL})
    assert resp.status_code == 200
    assert "password reset email failed: OSError" in caplog.text
    assert sent[0].split("#token=")[1].split()[0] not in caplog.text


@pytest.mark.parametrize("name,parts", [("", 0), ("report.csv", 1)])
def test_smtp_sender_attaches_only_when_there_is_an_attachment(name, parts):
    from app.jobs.report import SmtpEmailSender

    with patch("app.jobs.report.smtplib.SMTP") as smtp:
        SmtpEmailSender(host="localhost", use_tls=False, from_addr="n@example.com").send(
            "to@example.com", "s", "body", name, b"x"
        )
    msg = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert len(list(msg.iter_attachments())) == parts
