"""Tests for the per-user + per-org rolling chat cost ceiling.

Coverage
--------
1. User under the ceiling proceeds (no 429).
2. User at/over the per-user ceiling gets 429 chat_budget_exceeded.
3. Org ceiling enforced independently of per-user ceiling.
4. Unset ceiling = unlimited (no regression to existing chat tests).
5. NullProvider turns aren't falsely blocked (cost_usd=0.0 never trips ceiling).
6. check_chat_budget: fail-open when metering store is unavailable.
7. record_chat_cost: fail-open on errors.
8. Two different users share an org ceiling but have separate user ceilings.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.compute.metering import InMemorySink, set_sink


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    return str(uuid.uuid4())


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _seed_user(fake_db, user_id: str, email: str) -> None:
    from datetime import datetime, timezone
    fake_db.users[user_id] = {
        "id": user_id,
        "email": email,
        "name": "Cost Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ceiling_client(app, fake_db):
    """HTTPX client + seeded user/org for ceiling tests."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)

    user_id = _uid()
    org_id = _uid()
    _seed_user(fake_db, user_id, "ceiling@example.com")
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    # Fresh in-memory sink for isolation.
    sink = InMemorySink()
    set_sink(sink)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, org_id, repo, sink

    set_repo(None)
    set_sink(None)


# ---------------------------------------------------------------------------
# Unit tests for check_chat_budget / record_chat_cost directly
# ---------------------------------------------------------------------------


class TestCheckChatBudget:
    """Direct unit tests — no HTTP layer."""

    @pytest.mark.asyncio
    async def test_under_user_ceiling_proceeds(self, monkeypatch):
        """Spend below the per-user ceiling must not raise."""
        monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", "1.00")
        monkeypatch.delenv("NUBI_CHAT_ORG_DAILY_USD", raising=False)

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost

        org_id, user_id = _uid(), _uid()
        sink = InMemorySink()
        set_sink(sink)
        try:
            # Record $0.50 — below the $1.00 limit.
            await record_chat_cost(org_id, user_id, 0.50)
            # Should NOT raise.
            await check_chat_budget(org_id, user_id)
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_over_user_ceiling_raises_429(self, monkeypatch):
        """Spend exceeding the per-user ceiling must raise AppError 429."""
        monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", "1.00")
        monkeypatch.delenv("NUBI_CHAT_ORG_DAILY_USD", raising=False)

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost
        from app.errors import AppError

        org_id, user_id = _uid(), _uid()
        sink = InMemorySink()
        set_sink(sink)
        try:
            # Record $1.50 — over the $1.00 limit.
            await record_chat_cost(org_id, user_id, 1.50)
            with pytest.raises(AppError) as exc_info:
                await check_chat_budget(org_id, user_id)
            assert exc_info.value.code == "chat_budget_exceeded"
            assert exc_info.value.status == 429
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_at_exact_user_ceiling_NOT_blocked(self, monkeypatch):
        """Spend exactly at the ceiling (not exceeding) must not raise."""
        monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", "1.00")
        monkeypatch.delenv("NUBI_CHAT_ORG_DAILY_USD", raising=False)

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost

        org_id, user_id = _uid(), _uid()
        sink = InMemorySink()
        set_sink(sink)
        try:
            await record_chat_cost(org_id, user_id, 1.00)
            # Exactly at limit → spend == limit, NOT > limit → no block.
            await check_chat_budget(org_id, user_id)
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_org_ceiling_enforced_independently(self, monkeypatch):
        """Org ceiling applies across all users; user ceiling is per-user."""
        monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", "10.00")  # generous per-user
        monkeypatch.setenv("NUBI_CHAT_ORG_DAILY_USD", "1.00")    # tight org cap

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost
        from app.errors import AppError

        org_id = _uid()
        user_a = _uid()
        user_b = _uid()

        sink = InMemorySink()
        set_sink(sink)
        try:
            # User A spends $0.60 — under their own $10 user cap.
            await record_chat_cost(org_id, user_a, 0.60)
            # User B spends $0.60 — under their own $10 user cap too.
            await record_chat_cost(org_id, user_b, 0.60)
            # Org total = $1.20 → over the $1.00 org cap.
            # User A's next turn should be blocked at org level.
            with pytest.raises(AppError) as exc_info:
                await check_chat_budget(org_id, user_a)
            assert exc_info.value.code == "chat_budget_exceeded"
            assert "organisation" in exc_info.value.message
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_unset_ceiling_is_unlimited(self, monkeypatch):
        """With no env vars set, no ceiling is enforced."""
        monkeypatch.delenv("NUBI_CHAT_USER_DAILY_USD", raising=False)
        monkeypatch.delenv("NUBI_CHAT_ORG_DAILY_USD", raising=False)

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost

        org_id, user_id = _uid(), _uid()
        sink = InMemorySink()
        set_sink(sink)
        try:
            # Record an absurdly large cost.
            await record_chat_cost(org_id, user_id, 99999.0)
            # No ceiling configured → must NOT raise.
            await check_chat_budget(org_id, user_id)
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_zero_env_var_is_unlimited(self, monkeypatch):
        """NUBI_CHAT_USER_DAILY_USD=0 means unlimited."""
        monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", "0")
        monkeypatch.setenv("NUBI_CHAT_ORG_DAILY_USD", "0")

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost

        org_id, user_id = _uid(), _uid()
        sink = InMemorySink()
        set_sink(sink)
        try:
            await record_chat_cost(org_id, user_id, 99999.0)
            await check_chat_budget(org_id, user_id)
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_null_provider_turns_not_falsely_blocked(self, monkeypatch):
        """NullProvider records cost_usd=0.0; many turns must never trip ceiling."""
        monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", "0.01")
        monkeypatch.delenv("NUBI_CHAT_ORG_DAILY_USD", raising=False)

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost

        org_id, user_id = _uid(), _uid()
        sink = InMemorySink()
        set_sink(sink)
        try:
            # Simulate 100 NullProvider turns (cost_usd=0.0 each).
            for _ in range(100):
                await record_chat_cost(org_id, user_id, 0.0)
            # Total spend = 0.0, well below $0.01 limit → not blocked.
            await check_chat_budget(org_id, user_id)
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_fail_open_on_metering_store_unavailable(self, monkeypatch):
        """When _get_spend raises, check_chat_budget fails OPEN (no exception)."""
        monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", "0.001")

        from app.ai.cost_ceiling import check_chat_budget

        org_id, user_id = _uid(), _uid()

        # Simulate a broken sink that raises on get_events().
        class BrokenSink(InMemorySink):
            def get_events(self):  # type: ignore[override]
                raise RuntimeError("metering store unavailable")

        set_sink(BrokenSink())
        try:
            # Should NOT raise AppError — must fail open.
            await check_chat_budget(org_id, user_id)
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_record_chat_cost_fail_open(self, monkeypatch):
        """record_chat_cost must not raise even when the sink is broken."""
        from app.ai.cost_ceiling import record_chat_cost

        class ExplodingSink(InMemorySink):
            async def record(self, **kwargs):  # type: ignore[override]
                raise RuntimeError("sink exploded")

        set_sink(ExplodingSink())
        try:
            # Must NOT raise.
            await record_chat_cost(_uid(), _uid(), 1.0)
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_two_users_separate_user_ceilings(self, monkeypatch):
        """Two users in the same org each have their own user ceiling."""
        monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", "1.00")
        monkeypatch.delenv("NUBI_CHAT_ORG_DAILY_USD", raising=False)

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost
        from app.errors import AppError

        org_id = _uid()
        user_a = _uid()
        user_b = _uid()

        sink = InMemorySink()
        set_sink(sink)
        try:
            # User A over the limit.
            await record_chat_cost(org_id, user_a, 1.50)
            # User B under the limit.
            await record_chat_cost(org_id, user_b, 0.50)

            # User A blocked.
            with pytest.raises(AppError) as exc_info:
                await check_chat_budget(org_id, user_a)
            assert exc_info.value.code == "chat_budget_exceeded"

            # User B NOT blocked.
            await check_chat_budget(org_id, user_b)
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_different_orgs_isolated(self, monkeypatch):
        """An org's spend must not affect another org's ceiling check."""
        monkeypatch.setenv("NUBI_CHAT_ORG_DAILY_USD", "1.00")
        monkeypatch.delenv("NUBI_CHAT_USER_DAILY_USD", raising=False)

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost

        org_a = _uid()
        org_b = _uid()
        user_a = _uid()
        user_b = _uid()

        sink = InMemorySink()
        set_sink(sink)
        try:
            # Org A is over the limit.
            await record_chat_cost(org_a, user_a, 5.00)
            # Org B is under the limit.
            await record_chat_cost(org_b, user_b, 0.10)

            # Org B must NOT be blocked.
            await check_chat_budget(org_b, user_b)
        finally:
            set_sink(None)

    @pytest.mark.asyncio
    async def test_pre_turn_cost_added_to_running_total(self, monkeypatch):
        """cost_usd arg to check_chat_budget is added to spend before comparison."""
        monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", "1.00")
        monkeypatch.delenv("NUBI_CHAT_ORG_DAILY_USD", raising=False)

        from app.ai.cost_ceiling import check_chat_budget, record_chat_cost
        from app.errors import AppError

        org_id, user_id = _uid(), _uid()
        sink = InMemorySink()
        set_sink(sink)
        try:
            # Existing spend = $0.80.
            await record_chat_cost(org_id, user_id, 0.80)
            # Pre-turn check with estimated turn cost of $0.30 → total = $1.10 > $1.00.
            with pytest.raises(AppError) as exc_info:
                await check_chat_budget(org_id, user_id, cost_usd=0.30)
            assert exc_info.value.code == "chat_budget_exceeded"
        finally:
            set_sink(None)
