"""AI spend is capped by default, the cap fails closed, and background turns are bounded."""

from __future__ import annotations

import uuid

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

ORG = str(uuid.uuid4())
USER = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("NUBI_CHAT_USER_DAILY_USD", "NUBI_CHAT_ORG_DAILY_USD", "NUBI_AI_ASYNC_MAX_PER_USER"):
        monkeypatch.delenv(name, raising=False)


def test_a_ceiling_applies_when_nothing_is_configured():
    from app.ai import cost_ceiling

    assert cost_ceiling._user_daily_limit() and cost_ceiling._user_daily_limit() > 0
    assert cost_ceiling._org_daily_limit() and cost_ceiling._org_daily_limit() > 0


@pytest.mark.parametrize("value", ["0", "off", "unlimited"])
def test_an_operator_can_explicitly_lift_the_ceiling(monkeypatch, value):
    from app.ai import cost_ceiling

    monkeypatch.setenv("NUBI_CHAT_USER_DAILY_USD", value)
    monkeypatch.setenv("NUBI_CHAT_ORG_DAILY_USD", value)
    assert cost_ceiling._user_daily_limit() is None and cost_ceiling._org_daily_limit() is None


@pytest.mark.asyncio
async def test_spend_over_the_default_ceiling_is_refused(monkeypatch):
    from app.ai import cost_ceiling
    from app.errors import AppError

    async def _spend(org_id, user_id):
        return 10_000.0

    monkeypatch.setattr(cost_ceiling, "_spend_or_raise", _spend)
    with pytest.raises(AppError) as exc:
        await cost_ceiling.check_chat_budget(ORG, USER)
    assert exc.value.status == 429


@pytest.mark.asyncio
async def test_the_ceiling_fails_closed_when_spend_cannot_be_read(monkeypatch):
    from app.ai import cost_ceiling
    from app.errors import AppError

    def _boom():
        raise RuntimeError("metering store unavailable")

    monkeypatch.setattr("app.compute.metering.get_sink", _boom)
    with pytest.raises(AppError) as exc:
        await cost_ceiling.check_chat_budget(ORG, USER)
    assert exc.value.status == 503


def test_every_ai_route_checks_the_ceiling_and_records_cost():
    import inspect

    from app.routes import ai

    prepare = inspect.getsource(ai._prepare_ai_call)
    assert "check_chat_budget" in prepare
    assert "record_chat_cost" in inspect.getsource(ai._meter_ai_call)
    # Every route that completes with an LLM goes through the shared preparation.
    for name in ("ask", "create_dashboard", "ai_chat", "ai_chat_stream", "ai_chat_async", "generate_sql_endpoint"):
        assert "_prepare_ai_call" in inspect.getsource(getattr(ai, name)), name


@pytest.mark.asyncio
async def test_background_turns_are_capped_per_user(monkeypatch):
    from app.errors import AppError
    from app.routes import ai

    monkeypatch.setenv("NUBI_AI_ASYNC_MAX_PER_USER", "2")
    ai._ASYNC_JOBS.clear()
    ai._claim_async_slot(USER)
    ai._claim_async_slot(USER)
    with pytest.raises(AppError) as exc:
        ai._claim_async_slot(USER)
    assert exc.value.status == 429
    ai._claim_async_slot(str(uuid.uuid4()))  # another user is unaffected
    ai._release_async_slot(USER)
    ai._claim_async_slot(USER)
    ai._ASYNC_JOBS.clear()


def test_editor_chat_needs_write_access():
    import main  # noqa: F401
    from app.auth.roles import require_writer

    route = next(r for r in main.app.routes if getattr(r, "path", "") == "/api/v1/chat/stream")

    def deps(d, seen=None):
        seen = set() if seen is None else seen
        for x in d.dependencies:
            seen.add(x.call)
            deps(x, seen)
        return seen

    assert require_writer in deps(route.dependant)
