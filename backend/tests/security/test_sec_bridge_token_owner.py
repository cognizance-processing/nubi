"""A bridge token belongs to the person who created it and leaves with them."""

from __future__ import annotations

import pathlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
BRIDGE_1 = str(uuid.uuid4())
BRIDGE_2 = str(uuid.uuid4())


@pytest.fixture
def tokens():
    from app.auth.bridge_tokens import InMemoryBridgeTokenStore, set_bridge_token_store_for_tests

    store = InMemoryBridgeTokenStore()
    set_bridge_token_store_for_tests(store)
    yield store
    set_bridge_token_store_for_tests(None)


@pytest.mark.asyncio
async def test_a_token_records_who_created_it(tokens):
    from app.auth.bridge_tokens import _public_row

    user = str(uuid.uuid4())
    _raw, row = await tokens.mint(ORG_A, BRIDGE_1, "agent", created_by=user)
    assert row["created_by"] == user
    assert _public_row(row)["created_by"] == user
    listed = await tokens.list_for_bridge(ORG_A, BRIDGE_1)
    assert listed[0]["created_by"] == user


@pytest.mark.asyncio
async def test_rotation_hands_the_token_to_whoever_rotated_it(tokens):
    creator, rotator = str(uuid.uuid4()), str(uuid.uuid4())
    _raw, row = await tokens.mint(ORG_A, BRIDGE_1, "agent", created_by=creator)
    new_raw, new_row = await tokens.rotate(row["id"], ORG_A, BRIDGE_1, created_by=rotator)
    assert new_row["created_by"] == rotator
    # The rotator leaving takes the replacement with them; the creator leaving
    # takes the old one (still inside its grace window).
    assert await tokens.revoke_all_for_user(rotator, ORG_A) == [BRIDGE_1]
    assert await tokens.validate(new_raw) is None


@pytest.mark.asyncio
async def test_revoking_a_persons_tokens_leaves_everyone_elses(tokens):
    leaver, other = str(uuid.uuid4()), str(uuid.uuid4())
    raw_1, _ = await tokens.mint(ORG_A, BRIDGE_1, "a", created_by=leaver)
    raw_2, _ = await tokens.mint(ORG_A, BRIDGE_2, "b", created_by=leaver)
    raw_other_org, _ = await tokens.mint(ORG_B, BRIDGE_1, "c", created_by=leaver)
    raw_other_user, _ = await tokens.mint(ORG_A, BRIDGE_1, "d", created_by=other)
    raw_legacy, _ = await tokens.mint(ORG_A, BRIDGE_1, "legacy")

    assert sorted(await tokens.revoke_all_for_user(leaver, ORG_A)) == sorted([BRIDGE_1, BRIDGE_2])
    assert await tokens.validate(raw_1) is None
    assert await tokens.validate(raw_2) is None
    assert await tokens.validate(raw_other_org) is not None
    assert await tokens.validate(raw_other_user) is not None
    assert await tokens.validate(raw_legacy) is not None
    assert await tokens.revoke_all_for_user(leaver, ORG_A) == []


def _removal_patches(orgs, broker):
    grants = AsyncMock()
    grants.list_grants = AsyncMock(return_value=[])
    return (
        patch.object(orgs, "_require_manage", new=AsyncMock(return_value={"role": "owner"})),
        patch.object(orgs, "execute", new=AsyncMock(return_value="DELETE 1")),
        patch("app.auth.oauth.get_oauth_store", return_value=grants),
        patch("app.bridges.broker.get_broker", return_value=broker),
    )


@pytest.mark.asyncio
async def test_removing_a_member_revokes_their_bridge_tokens_and_drops_the_tunnel(tokens):
    from app.repos.memory import InMemoryRepo
    from app.routes import orgs

    repo = InMemoryRepo()
    manager, leaver = str(uuid.uuid4()), str(uuid.uuid4())
    repo.seed_org_member(ORG_A, manager, "owner")
    repo.seed_org_member(ORG_A, leaver, "admin")
    raw, _ = await tokens.mint(ORG_A, BRIDGE_1, "agent", created_by=leaver)
    kept, _ = await tokens.mint(ORG_A, BRIDGE_2, "agent", created_by=manager)
    broker = AsyncMock()

    p1, p2, p3, p4 = _removal_patches(orgs, broker)
    with p1, p2, p3, p4:
        await orgs.remove_member(ORG_A, leaver, user={"id": manager}, repo=repo)

    assert await tokens.validate(raw) is None
    assert await tokens.validate(kept) is not None
    broker.drop.assert_awaited_once_with(BRIDGE_1)


@pytest.mark.asyncio
@pytest.mark.parametrize(("new_role", "revoked"), [("member", True), ("viewer", True), ("admin", False), ("owner", False)])
async def test_losing_the_right_to_manage_bridges_revokes_the_tokens(tokens, new_role, revoked):
    from app.routes import orgs

    manager, target = str(uuid.uuid4()), str(uuid.uuid4())
    raw, _ = await tokens.mint(ORG_A, BRIDGE_1, "agent", created_by=target)
    broker = AsyncMock()
    body = orgs.UpdateMemberRoleIn(role=new_role)
    with (
        patch.object(orgs, "_require_manage", new=AsyncMock(return_value={"role": "owner"})),
        patch.object(orgs, "fetchrow", new=AsyncMock(return_value={"role": "admin", "name": "n", "email": "a@b.co"})),
        patch.object(orgs, "execute", new=AsyncMock(return_value="UPDATE 1")),
        patch.object(orgs, "_count_owners", new=AsyncMock(return_value=2)),
        patch("app.bridges.broker.get_broker", return_value=broker),
    ):
        await orgs.update_member_role(ORG_A, target, body, user={"id": manager})
    assert (await tokens.validate(raw) is None) is revoked


def test_schema_and_queries_carry_the_creator():
    root = pathlib.Path(__file__).resolve().parents[3]
    migrations = "\n".join(p.read_text() for p in sorted((root / "database" / "migrations").glob("*.sql")))
    assert "bridge_tokens" in migrations and "created_by" in migrations.split("bridge_tokens", 1)[1]
    source = (root / "backend" / "app" / "auth" / "bridge_tokens.py").read_text()
    assert "created_by = $1::uuid" in source  # the Postgres store revokes by creator
    routes = (root / "backend" / "app" / "routes" / "bridges.py").read_text()
    assert routes.count("created_by=str(user[\"id\"])") >= 2  # mint and rotate
