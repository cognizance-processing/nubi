"""Tests for per-request model routing through the LLM provider layer.

Coverage
--------
1. ``resolve_model`` — None/empty → default; allowed id → that id; unknown id →
   ``AppError("model_not_allowed", 400)`` listing the allowed ids.
2. ``LLMProvider.complete`` model threading — a FAKE provider records the model
   it received, proving the param reaches every concrete ``complete`` call.
3. ``NullProvider`` ignores ``model`` entirely and still returns its scripted
   deterministic output (no network).
4. ``run_agent`` / ``generate_dashboard_spec`` thread ``model`` to the provider.
5. Routes thread ``body.model`` down: a /ai/chat (and /ai/ask) request with a
   bad model → 400 ``model_not_allowed``; a good/None model → 200.

Network safety
--------------
No real network calls are made.  Every test uses ``NullProvider`` or a local
``FakeProvider`` subclass; real-SDK providers are never constructed.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.ai.agent import run_agent
from app.ai.dashboard import generate_dashboard_spec
from app.ai.provider import (
    ALLOWED_MODELS,
    LLMProvider,
    NullProvider,
    resolve_model,
)
from app.auth.jwt import mint_access_token
from app.errors import AppError


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


#: Allowlist used by the fake provider in these tests.
_FAKE_ALLOWED = ["fake-default", "fake-alt"]


class FakeProvider(LLMProvider):
    """A non-Null provider that records the model it was asked to use.

    It mimics a real provider's ``complete``: it resolves the requested model
    against an allowlist (raising ``model_not_allowed`` for unknown ids) and
    records the effective model.  It never touches the network.
    """

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        effective = resolve_model(model, _FAKE_ALLOWED[0], _FAKE_ALLOWED)
        self.calls.append({"prompt": prompt, "system": system, "model": effective})
        # Return a plain-text reply so the agent treats it as a final answer
        # (no tool-call JSON) and terminates after one completion.
        return f"done with {effective}"


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "model-router@example.com",
        "name": "Model Router",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


@pytest_asyncio.fixture
async def ai_client(app, fake_db):
    """HTTPX async client with a pre-seeded user for AI endpoint tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id


def _claims(user_id: str = "u1") -> dict[str, Any]:
    return {
        "kind": "access",
        "sub": user_id,
        "policies": {},
        "scope": ["read:*", "write:*"],
    }


# ---------------------------------------------------------------------------
# 1. resolve_model
# ---------------------------------------------------------------------------


class TestResolveModel:
    def test_none_returns_default(self):
        assert resolve_model(None, "d", ["d", "x"]) == "d"

    def test_empty_string_returns_default(self):
        assert resolve_model("", "d", ["d", "x"]) == "d"

    def test_allowed_id_returns_that_id(self):
        assert resolve_model("x", "d", ["d", "x"]) == "x"

    def test_unknown_id_raises_model_not_allowed_400(self):
        with pytest.raises(AppError) as exc:
            resolve_model("nope", "d", ["d", "x"])
        assert exc.value.code == "model_not_allowed"
        assert exc.value.status == 400

    def test_error_lists_allowed_models(self):
        with pytest.raises(AppError) as exc:
            resolve_model("nope", "d", ["d", "x"])
        assert "d" in exc.value.message
        assert "x" in exc.value.message

    def test_allowlist_constant_has_known_providers(self):
        # Sanity: every real provider has a non-empty allowlist with the default
        # listed first.
        for name in ("anthropic", "openai", "gemini"):
            assert name in ALLOWED_MODELS
            assert len(ALLOWED_MODELS[name]) >= 1


# ---------------------------------------------------------------------------
# 2. FakeProvider.complete receives the threaded model
# ---------------------------------------------------------------------------


class TestProviderModelThreading:
    def test_default_when_no_model(self):
        p = FakeProvider()
        out = p.complete("hi")
        assert "fake-default" in out
        assert p.calls[-1]["model"] == "fake-default"

    def test_allowed_model_passes(self):
        p = FakeProvider()
        out = p.complete("hi", model="fake-alt")
        assert "fake-alt" in out
        assert p.calls[-1]["model"] == "fake-alt"

    def test_unknown_model_raises(self):
        p = FakeProvider()
        with pytest.raises(AppError) as exc:
            p.complete("hi", model="gpt-9000")
        assert exc.value.code == "model_not_allowed"
        assert exc.value.status == 400


# ---------------------------------------------------------------------------
# 3. NullProvider ignores model
# ---------------------------------------------------------------------------


class TestNullProviderIgnoresModel:
    def test_unknown_model_does_not_raise(self):
        # A bogus model id must NOT raise on NullProvider — it has no allowlist
        # and makes no network call.
        out = NullProvider().complete("show me orders", model="totally-made-up")
        assert isinstance(out, str)
        assert "[NullProvider]" in out

    def test_output_matches_no_model_call(self):
        p = NullProvider()
        a = p.complete("show me orders")
        b = p.complete("show me orders", model="anything")
        assert a == b


# ---------------------------------------------------------------------------
# 4. run_agent / generate_dashboard_spec thread the model
# ---------------------------------------------------------------------------


class TestAgentThreadsModel:
    def test_run_agent_passes_model_to_provider(self):
        p = FakeProvider()
        result = run_agent(
            [{"role": "user", "content": "hello there"}],
            p,
            _claims(),
            model="fake-alt",
        )
        assert result["reply"]  # non-empty
        # The agent's real-provider loop called complete with our model.
        assert p.calls, "provider.complete was never called"
        assert all(c["model"] == "fake-alt" for c in p.calls)

    def test_run_agent_unknown_model_raises(self):
        p = FakeProvider()
        with pytest.raises(AppError) as exc:
            run_agent(
                [{"role": "user", "content": "hello there"}],
                p,
                _claims(),
                model="bad-model",
            )
        assert exc.value.code == "model_not_allowed"

    def test_run_agent_null_provider_ignores_model(self):
        # NullProvider scripted path: a bogus model must not raise; output is
        # the deterministic scripted reply.
        result = run_agent(
            [{"role": "user", "content": "show me orders"}],
            NullProvider(),
            _claims(),
            model="bogus-model",
        )
        assert isinstance(result["reply"], str)
        assert result["reply"]

    def test_generate_dashboard_spec_threads_model(self):
        from app.ai.grounding import build_catalog  # noqa: PLC0415

        p = FakeProvider()
        catalog = build_catalog()
        # FakeProvider returns "done with ..." which is not valid dashboard JSON,
        # so the repair loop runs to exhaustion and raises dashboard_generation_failed.
        # That is fine — we only need to prove complete() was reached with our model.
        with pytest.raises(AppError):
            generate_dashboard_spec("a dashboard of orders", catalog, p, model="fake-alt")
        assert p.calls, "provider.complete was never called"
        assert all(c["model"] == "fake-alt" for c in p.calls)

    def test_generate_dashboard_spec_unknown_model_raises_not_allowed(self):
        from app.ai.grounding import build_catalog  # noqa: PLC0415

        p = FakeProvider()
        catalog = build_catalog()
        with pytest.raises(AppError) as exc:
            generate_dashboard_spec("a dashboard of orders", catalog, p, model="bad")
        assert exc.value.code == "model_not_allowed"


# ---------------------------------------------------------------------------
# 5. Routes thread body.model down
# ---------------------------------------------------------------------------


class TestChatRouteThreadsModel:
    @pytest.mark.asyncio
    async def test_chat_null_provider_good_model_returns_200(self, ai_client):
        ac, user_id = ai_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "show me orders"}],
                "model": ALLOWED_MODELS["anthropic"][0],
            },
            headers=_auth_headers(user_id),
        )
        # Default provider is NullProvider (no API keys) → model ignored → 200,
        # and the requested model is echoed back.
        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] == ALLOWED_MODELS["anthropic"][0]

    @pytest.mark.asyncio
    async def test_chat_none_model_returns_200(self, ai_client):
        ac, user_id = ai_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={"messages": [{"role": "user", "content": "show me orders"}]},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        assert resp.json()["model"] is None

    @pytest.mark.asyncio
    async def test_chat_bad_model_returns_400_with_real_provider(
        self, ai_client, monkeypatch
    ):
        # Swap in a FakeProvider (non-Null) so the model IS gated. A bad model
        # must surface as a 400 model_not_allowed through the AppError handler.
        ac, user_id = ai_client
        monkeypatch.setattr("app.routes.ai.get_provider", lambda: FakeProvider())
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "show me orders"}],
                "model": "definitely-not-allowed",
            },
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "model_not_allowed"

    @pytest.mark.asyncio
    async def test_chat_good_model_with_real_provider_returns_200(
        self, ai_client, monkeypatch
    ):
        ac, user_id = ai_client
        monkeypatch.setattr("app.routes.ai.get_provider", lambda: FakeProvider())
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "show me orders"}],
                "model": "fake-alt",
            },
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200


class TestAskRouteThreadsModel:
    @pytest.mark.asyncio
    async def test_ask_good_model_returns_200(self, ai_client):
        ac, user_id = ai_client
        resp = await ac.post(
            "/api/v1/ai/ask",
            json={
                "question": "show me orders",
                "model": ALLOWED_MODELS["anthropic"][0],
            },
            headers=_auth_headers(user_id),
        )
        # NullProvider default ignores the model → 200.
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_ask_bad_model_returns_400_with_real_provider(
        self, ai_client, monkeypatch
    ):
        ac, user_id = ai_client
        monkeypatch.setattr("app.routes.ai.get_provider", lambda: FakeProvider())
        resp = await ac.post(
            "/api/v1/ai/ask",
            json={"question": "show me orders", "model": "nope-not-allowed"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "model_not_allowed"

    @pytest.mark.asyncio
    async def test_ask_requires_auth(self, ai_client):
        ac, _ = ai_client
        resp = await ac.post(
            "/api/v1/ai/ask",
            json={"question": "show me orders", "model": "anything"},
        )
        assert resp.status_code == 401
