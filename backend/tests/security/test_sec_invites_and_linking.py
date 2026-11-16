"""Invites are accepted with their secret link by the invited address, and
linking a sign-in provider ends any earlier password access to the account."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

ORG = str(uuid.uuid4())
NOW = datetime.now(timezone.utc)


def _invite(**over):
    row = {
        "id": uuid.uuid4(), "org_id": ORG, "role": "member", "status": "pending",
        "email": "invitee@example.com", "expires_at": NOW + timedelta(days=3),
        "token": "secret-token", "invited_by": None, "created_at": NOW, "org_name": "Acme",
    }
    row.update(over)
    return row


def _user(email="invitee@example.com", verified=False):
    return {"id": str(uuid.uuid4()), "email": email, "email_verified": verified}


async def _accept(invite, user, existing_role=None):
    from app.routes import orgs

    execute = AsyncMock(return_value="OK")
    with (
        patch.object(orgs, "fetchrow", new=AsyncMock(return_value=invite)),
        patch.object(orgs, "execute", new=execute),
        patch.object(orgs, "_get_org_name", new=AsyncMock(return_value="Acme")),
        patch.object(orgs, "_member_role", new=AsyncMock(return_value=existing_role)),
    ):
        out = await orgs.accept_invite("secret-token", user=user)
    return out, [c.args for c in execute.await_args_list]


@pytest.mark.asyncio
async def test_accept_needs_the_invited_address():
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        await _accept(_invite(), _user(email="someone-else@example.com"))
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_accept_refuses_expired_invites():
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        await _accept(_invite(expires_at=NOW - timedelta(minutes=1)), _user())
    assert exc.value.status in (409, 410)


@pytest.mark.asyncio
async def test_accept_joins_with_the_invited_role():
    out, calls = await _accept(_invite(role="admin"), _user(email="Invitee@Example.com"))
    assert out.role == "admin"
    assert any("INSERT INTO org_members" in c[0] for c in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("existing,invited", [("owner", "viewer"), ("admin", "owner"), ("member", "admin")])
async def test_accepting_never_changes_an_existing_members_role(existing, invited):
    out, calls = await _accept(_invite(role=invited), _user(), existing_role=existing)
    assert out.role == existing
    assert not any("org_members" in c[0] for c in calls)
    assert any("UPDATE org_invites" in c[0] for c in calls)  # the invite is still consumed


@pytest.mark.asyncio
async def test_org_invite_listing_does_not_return_tokens():
    from app.routes import orgs

    with (
        patch.object(orgs, "_require_manage", new=AsyncMock(return_value={"role": "owner"})),
        patch.object(orgs, "fetch", new=AsyncMock(return_value=[_invite()])),
    ):
        out = await orgs.list_invites(ORG, user={"id": str(uuid.uuid4())})
    assert "secret-token" not in out.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("verified,expect_token", [(False, False), (True, True)])
async def test_my_invites_only_carry_the_link_for_a_verified_address(verified, expect_token):
    from app.routes import auth

    with patch.object(auth, "fetch", new=AsyncMock(return_value=[_invite()])):
        out = await auth.my_invites(user=_user(verified=verified))
    assert len(out["invites"]) == 1
    assert ("secret-token" in str(out)) is expect_token


# ---------------------------------------------------------------------------
# Linking a sign-in provider
# ---------------------------------------------------------------------------


async def _link(password_hash, already_linked=False):
    from app.routes import auth

    user_id = str(uuid.uuid4())

    async def fetchrow(sql, *args):
        if "oauth_accounts" in sql:
            return {"ok": 1} if already_linked else None
        return {"password_hash": password_hash}

    execute = AsyncMock(return_value="OK")
    sessions = AsyncMock()
    keys = AsyncMock()
    keys.revoke_all_for_user = AsyncMock(return_value=2)
    with (
        patch.object(auth, "fetchrow", new=fetchrow),
        patch.object(auth, "execute", new=execute),
        patch("app.auth.sessions.revoke_all_for_user", new=sessions),
        patch("app.auth.api_keys.get_api_key_store", return_value=keys),
    ):
        locked = await auth._secure_account_on_first_provider_link(user_id, "google")
    return locked, user_id, [c.args[0] for c in execute.await_args_list], sessions, keys


@pytest.mark.asyncio
async def test_first_provider_link_ends_earlier_password_access():
    locked, user_id, sqls, sessions, keys = await _link("hash-set-by-someone")
    assert locked is True
    assert any("password_hash = NULL" in s for s in sqls)
    sessions.assert_awaited_once_with(user_id)
    keys.revoke_all_for_user.assert_awaited_once_with(user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("password_hash,already_linked", [(None, False), ("hash", True)])
async def test_later_logins_and_passwordless_accounts_are_untouched(password_hash, already_linked):
    locked, _uid, sqls, sessions, keys = await _link(password_hash, already_linked)
    assert locked is False
    assert not any("password_hash = NULL" in s for s in sqls)
    sessions.assert_not_awaited()
    keys.revoke_all_for_user.assert_not_awaited()


def test_google_callback_secures_the_account_before_linking():
    import inspect

    from app.routes import auth

    src = inspect.getsource(auth.google_callback)
    assert src.index("_secure_account_on_first_provider_link") < src.index("INSERT INTO oauth_accounts")
