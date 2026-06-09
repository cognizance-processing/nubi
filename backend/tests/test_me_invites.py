"""Tests for GET /auth/me/invites — pending org invites for the current user.

Coverage
--------
1.  401 without a Bearer token.
2.  Empty case — no invites → {"invites": []}.
3.  A pending, unexpired invite for the user's email is returned with the full
    shape {id, org_id, org_name, role, token, created_at, expires_at}.
4.  Accepted / revoked invites are filtered out (status != 'pending').
5.  Expired invites are filtered out (route-level defensive filter).
6.  Email matching is case-insensitive in both directions.
7.  No org membership is required — an org-less user sees their invites.

Strategy
--------
Follows the established route-test style: ``httpx.AsyncClient`` against the
conftest ``app`` (FakeDB), with ``app.routes.auth.fetch`` patched to serve the
org_invites JOIN query from an in-test list. The fake emulates the SQL's
``lower(email)`` + ``status='pending'`` predicates; expiry filtering is left to
the route's defensive Python filter so the expired test exercises route logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from app.auth.jwt import mint_access_token

import app.routes.auth  # noqa: F401 — ensure routes self-register


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _user(user_id: str, email: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Invitee",
        "password_hash": None,
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc),
        "updated_at": datetime.now(tz=timezone.utc),
    }


def _invite(
    email: str,
    *,
    org_name: str = "Acme Corp",
    role: str = "member",
    status: str = "pending",
    expires_delta: timedelta = timedelta(days=7),
) -> dict[str, Any]:
    """Build a row in the shape of the org_invites ⋈ orgs SELECT."""
    now = datetime.now(tz=timezone.utc)
    return {
        "id": str(uuid.uuid4()),
        "org_id": str(uuid.uuid4()),
        "org_name": org_name,
        "role": role,
        "token": uuid.uuid4().hex,
        "status": status,
        "email": email,
        "created_at": now,
        "expires_at": now + expires_delta,
    }


def _patch_invites_fetch(invites: list[dict[str, Any]]):
    """Patch ``app.routes.auth.fetch`` to emulate the invites JOIN query.

    Mirrors the SQL's ``lower(i.email) = $1 AND i.status = 'pending'``
    predicates. Expiry is intentionally NOT filtered here — the route's
    defensive Python filter must handle it.
    """

    async def _fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.upper()
        if "ORG_INVITES" in q:
            email = str(args[0])
            return [
                dict(r)
                for r in invites
                if r["email"].lower() == email and r["status"] == "pending"
            ]
        return []

    return patch("app.routes.auth.fetch", side_effect=_fetch)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_me_invites_requires_auth(client):
    resp = await client.get("/api/v1/auth/me/invites")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_invites_empty(client, fake_db):
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = _user(user_id, "lonely@example.com")

    with _patch_invites_fetch([]):
        resp = await client.get("/api/v1/auth/me/invites", headers=_auth(user_id))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"invites": []}


@pytest.mark.asyncio
async def test_me_invites_returns_pending_invite_full_shape(client, fake_db):
    user_id = str(uuid.uuid4())
    email = "pat@example.com"
    fake_db.users[user_id] = _user(user_id, email)
    invite = _invite(email, org_name="Globex", role="admin")

    with _patch_invites_fetch([invite]):
        resp = await client.get("/api/v1/auth/me/invites", headers=_auth(user_id))

    assert resp.status_code == 200, resp.text
    invites = resp.json()["invites"]
    assert len(invites) == 1
    got = invites[0]
    assert got["id"] == invite["id"]
    assert got["org_id"] == invite["org_id"]
    assert got["org_name"] == "Globex"
    assert got["role"] == "admin"
    assert got["token"] == invite["token"]
    # Timestamps serialised to ISO strings.
    assert got["created_at"] == invite["created_at"].isoformat()
    assert got["expires_at"] == invite["expires_at"].isoformat()


@pytest.mark.asyncio
async def test_me_invites_filters_accepted_and_revoked(client, fake_db):
    user_id = str(uuid.uuid4())
    email = "selective@example.com"
    fake_db.users[user_id] = _user(user_id, email)
    pending = _invite(email)
    rows = [
        pending,
        _invite(email, status="accepted"),
        _invite(email, status="revoked"),
    ]

    with _patch_invites_fetch(rows):
        resp = await client.get("/api/v1/auth/me/invites", headers=_auth(user_id))

    assert resp.status_code == 200
    invites = resp.json()["invites"]
    assert [i["id"] for i in invites] == [pending["id"]]


@pytest.mark.asyncio
async def test_me_invites_filters_expired(client, fake_db):
    user_id = str(uuid.uuid4())
    email = "late@example.com"
    fake_db.users[user_id] = _user(user_id, email)
    fresh = _invite(email)
    expired = _invite(email, expires_delta=timedelta(days=-1))

    with _patch_invites_fetch([fresh, expired]):
        resp = await client.get("/api/v1/auth/me/invites", headers=_auth(user_id))

    assert resp.status_code == 200
    invites = resp.json()["invites"]
    assert [i["id"] for i in invites] == [fresh["id"]]


@pytest.mark.asyncio
async def test_me_invites_email_match_is_case_insensitive(client, fake_db):
    user_id = str(uuid.uuid4())
    # User registered with mixed case; invite addressed in different case.
    fake_db.users[user_id] = _user(user_id, "MiXeD@Example.com")
    invite = _invite("mixed@EXAMPLE.COM")

    with _patch_invites_fetch([invite]):
        resp = await client.get("/api/v1/auth/me/invites", headers=_auth(user_id))

    assert resp.status_code == 200
    invites = resp.json()["invites"]
    assert [i["id"] for i in invites] == [invite["id"]]


@pytest.mark.asyncio
async def test_me_invites_works_without_org_membership(client, fake_db):
    """An org-less user (e.g. fresh OAuth signup) can list their invites."""
    user_id = str(uuid.uuid4())
    email = "orgless@example.com"
    fake_db.users[user_id] = _user(user_id, email)
    assert not fake_db.org_members  # genuinely org-less
    invite = _invite(email)

    with _patch_invites_fetch([invite]):
        resp = await client.get("/api/v1/auth/me/invites", headers=_auth(user_id))

    assert resp.status_code == 200
    assert len(resp.json()["invites"]) == 1
