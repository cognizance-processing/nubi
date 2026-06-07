"""Tests for the M22-A chat gateway (render, gateway, routes).

Coverage
--------
1. render_chart_png — returns bytes starting with PNG magic ``\\x89PNG``.
2. handle_inbound with a mocked agent returning a chart action →
   OutboundMessage has non-empty text AND image_png with PNG header.
3. NullTransport captured exactly one send.
4. POST /chat/slack with bad signature → 401.
5. POST /chat/slack with good signature → 200.
6. POST /chat/whatsapp with good signature → 200.

Network safety
--------------
All tests are offline:
- Agent is injected via the ``provider`` arg (NullProvider) and a module-level
  mock that returns a deterministic scripted result with a chart action.
- No Slack / WhatsApp network calls are made.
- ``_sig_override`` controls signature verification without a signing secret.

Local FastAPI app
-----------------
Endpoint tests build a local FastAPI app including ONLY the chat router::

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

This satisfies the "local app" requirement without importing the full main.py.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Import targets (must work without agent.py present)
# ---------------------------------------------------------------------------

from app.chat.render import render_chart_png
from app.chat.gateway import (
    OutboundMessage,
    NullTransport,
    handle_inbound,
    _sig_override,
)
from app.routes.chat import router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_local_app() -> tuple[FastAPI, TestClient]:
    """Build a minimal FastAPI app with only the chat router mounted."""
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    return app, client


def _scripted_run_agent(
    messages: list[dict],
    provider: Any,
    claims: dict,
    *,
    max_steps: int = 8,
) -> dict[str, Any]:
    """Deterministic agent stub: always returns a chart action."""
    return {
        "reply": "Here is the revenue by region chart.",
        "actions": [
            {
                "tool": "create_chart",
                "args": {
                    "chart_type": "bar",
                    "title": "Revenue by Region",
                    "x": "region",
                    "y": "revenue",
                },
                "result": {
                    "spec": {
                        "type": "bar",
                        "title": "Revenue by Region",
                        "x": "region",
                        "y": "revenue",
                    },
                    "data": [
                        {"region": "North", "revenue": 120},
                        {"region": "South", "revenue": 95},
                        {"region": "East", "revenue": 110},
                        {"region": "West", "revenue": 140},
                    ],
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. render_chart_png returns valid PNG bytes
# ---------------------------------------------------------------------------


class TestRenderChartPng:
    """render_chart_png must return PNG bytes starting with the magic header."""

    def test_bar_chart_returns_png(self):
        rows = [
            {"region": "North", "revenue": 120},
            {"region": "South", "revenue": 95},
        ]
        spec = {"type": "bar", "title": "Test", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, rows)
        assert isinstance(result, bytes)
        assert result[:4] == b"\x89PNG", f"Expected PNG magic, got {result[:4]!r}"
        assert len(result) > 100  # sanity — should be many bytes

    def test_line_chart_returns_png(self):
        rows = [{"x": i, "y": i * 2} for i in range(5)]
        spec = {"type": "line", "x": "x", "y": "y"}
        result = render_chart_png(spec, rows)
        assert result[:4] == b"\x89PNG"

    def test_pie_chart_returns_png(self):
        rows = [{"label": "A", "val": 30}, {"label": "B", "val": 70}]
        spec = {"type": "pie", "x": "label", "y": "val"}
        result = render_chart_png(spec, rows)
        assert result[:4] == b"\x89PNG"

    def test_scatter_chart_returns_png(self):
        rows = [{"x": float(i), "y": float(i) ** 2} for i in range(5)]
        spec = {"type": "scatter", "x": "x", "y": "y"}
        result = render_chart_png(spec, rows)
        assert result[:4] == b"\x89PNG"

    def test_empty_rows_returns_png(self):
        """An empty dataset should still produce a valid (possibly blank) chart."""
        spec = {"type": "bar", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, [])
        assert result[:4] == b"\x89PNG"

    def test_missing_columns_fallback(self):
        """When x/y are absent from spec, falls back to first two column names."""
        rows = [{"alpha": 1, "beta": 2}, {"alpha": 3, "beta": 4}]
        spec = {"type": "bar"}
        result = render_chart_png(spec, rows)
        assert result[:4] == b"\x89PNG"

    def test_returns_bytes_not_str(self):
        rows = [{"x": "A", "y": 10}]
        spec = {"type": "bar", "x": "x", "y": "y"}
        result = render_chart_png(spec, rows)
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# 2. handle_inbound with chart action → text + PNG
# ---------------------------------------------------------------------------


class TestHandleInbound:
    """End-to-end gateway test without network."""

    def test_chart_request_returns_text_and_png(self):
        """'make a chart of revenue by region' → OutboundMessage with text + PNG."""
        from app.ai.provider import NullProvider

        payload = {
            "event": {
                "channel": "C12345",
                "text": "make a chart of revenue by region",
            }
        }
        transport = NullTransport()

        with patch("app.chat.gateway.run_agent", _scripted_run_agent, create=True):
            # Patch the lazy import path used inside handle_inbound
            mock_agent = MagicMock()
            mock_agent.run_agent = _scripted_run_agent
            with patch.dict(sys.modules, {"app.ai.agent": mock_agent}):
                outbound = handle_inbound(
                    "slack",
                    payload,
                    provider=NullProvider(),
                    transport=transport,
                    claims={},
                )

        assert outbound.text, "Expected non-empty reply text"
        assert outbound.image_png is not None, "Expected PNG bytes to be attached"
        assert outbound.image_png[:4] == b"\x89PNG", "Expected PNG magic header"

    def test_null_transport_captures_send(self):
        """NullTransport must record exactly one send call."""
        from app.ai.provider import NullProvider

        payload = {
            "event": {
                "channel": "C99999",
                "text": "show me a bar chart",
            }
        }
        transport = NullTransport()

        mock_agent = MagicMock()
        mock_agent.run_agent = _scripted_run_agent
        with patch.dict(sys.modules, {"app.ai.agent": mock_agent}):
            handle_inbound(
                "slack",
                payload,
                provider=NullProvider(),
                transport=transport,
                claims={},
            )

        assert len(transport.sent) == 1, f"Expected 1 send, got {len(transport.sent)}"
        to, msg = transport.sent[0]
        assert to == "C99999"
        assert isinstance(msg, OutboundMessage)

    def test_no_chart_action_returns_text_only(self):
        """When the agent returns no chart actions, image_png must be None."""
        from app.ai.provider import NullProvider

        def _text_only_agent(messages, provider_, claims_, *, max_steps=8):
            return {"reply": "Just a text answer.", "actions": []}

        payload = {"event": {"channel": "C1", "text": "hello"}}
        transport = NullTransport()

        mock_agent = MagicMock()
        mock_agent.run_agent = _text_only_agent
        with patch.dict(sys.modules, {"app.ai.agent": mock_agent}):
            outbound = handle_inbound(
                "slack",
                payload,
                provider=NullProvider(),
                transport=transport,
            )

        assert outbound.text == "Just a text answer."
        assert outbound.image_png is None

    def test_whatsapp_payload_normalised(self):
        """WhatsApp payloads are normalised correctly."""
        from app.ai.provider import NullProvider

        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "+27821234567",
                                        "text": {"body": "revenue chart please"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        transport = NullTransport()

        mock_agent = MagicMock()
        mock_agent.run_agent = _scripted_run_agent
        with patch.dict(sys.modules, {"app.ai.agent": mock_agent}):
            outbound = handle_inbound(
                "whatsapp",
                payload,
                provider=NullProvider(),
                transport=transport,
            )

        assert outbound.to == "+27821234567"
        assert outbound.image_png is not None


# ---------------------------------------------------------------------------
# 3. Signature verification
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    """verify_signature rejects bad signatures and passes good ones."""

    def setup_method(self):
        """Ensure _sig_override is clean before each test."""
        _sig_override.clear()

    def teardown_method(self):
        """Clean up _sig_override after each test."""
        _sig_override.clear()

    def test_bad_sig_raises_401(self):
        from app.errors import AppError

        _sig_override["slack"] = False
        with pytest.raises(AppError) as exc_info:
            from app.chat.gateway import verify_signature
            verify_signature("slack", {})
        assert exc_info.value.status == 401
        assert exc_info.value.code == "invalid_signature"

    def test_good_sig_override_passes(self):
        from app.chat.gateway import verify_signature

        _sig_override["slack"] = True
        # Should not raise
        verify_signature("slack", {})

    def test_payload_bad_sig_field_raises(self):
        from app.errors import AppError
        from app.chat.gateway import verify_signature

        with pytest.raises(AppError) as exc_info:
            verify_signature("slack", {"_sig": "bad"})
        assert exc_info.value.status == 401

    def test_permissive_default_passes(self):
        from app.chat.gateway import verify_signature
        # No override, no _sig field → permissive
        verify_signature("slack", {"text": "hello"})


# ---------------------------------------------------------------------------
# 4 & 5. Endpoint tests — local FastAPI app (only chat router)
# ---------------------------------------------------------------------------


class TestChatEndpoints:
    """POST /chat/slack and /chat/whatsapp — using a LOCAL FastAPI TestClient."""

    def setup_method(self):
        _sig_override.clear()

    def teardown_method(self):
        _sig_override.clear()

    def _client(self) -> TestClient:
        _, client = _make_local_app()
        return client

    # ── Slack ────────────────────────────────────────────────────────────────

    def test_slack_bad_signature_returns_401(self):
        """A bad signature must return HTTP 401."""
        client = self._client()
        _sig_override["slack"] = False
        resp = client.post(
            "/chat/slack",
            json={"event": {"channel": "C1", "text": "hello"}, "_sig": "bad"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    def test_slack_good_request_returns_200(self):
        """A valid Slack webhook must return 200 with ok=true."""
        client = self._client()
        _sig_override["slack"] = True

        mock_agent = MagicMock()
        mock_agent.run_agent = _scripted_run_agent
        with patch.dict(sys.modules, {"app.ai.agent": mock_agent}):
            resp = client.post(
                "/chat/slack",
                json={"event": {"channel": "C1", "text": "make a chart"}},
            )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("ok") is True

    def test_slack_good_request_has_image(self):
        """A chart-producing agent action sets has_image=true in the response."""
        client = self._client()
        _sig_override["slack"] = True

        mock_agent = MagicMock()
        mock_agent.run_agent = _scripted_run_agent
        with patch.dict(sys.modules, {"app.ai.agent": mock_agent}):
            resp = client.post(
                "/chat/slack",
                json={"event": {"channel": "C1", "text": "make a chart of revenue by region"}},
            )

        assert resp.status_code == 200
        assert resp.json().get("has_image") is True

    # ── WhatsApp ─────────────────────────────────────────────────────────────

    def test_whatsapp_good_request_returns_200(self):
        """A valid WhatsApp webhook must return 200 with ok=true."""
        client = self._client()
        _sig_override["whatsapp"] = True

        mock_agent = MagicMock()
        mock_agent.run_agent = _scripted_run_agent
        with patch.dict(sys.modules, {"app.ai.agent": mock_agent}):
            resp = client.post(
                "/chat/whatsapp",
                json={
                    "entry": [
                        {
                            "changes": [
                                {
                                    "value": {
                                        "messages": [
                                            {
                                                "from": "+27820000000",
                                                "text": {"body": "chart please"},
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        assert resp.json().get("ok") is True

    def test_whatsapp_bad_signature_returns_401(self):
        """A WhatsApp webhook with a bad signature must return 401."""
        client = self._client()
        _sig_override["whatsapp"] = False

        resp = client.post(
            "/chat/whatsapp",
            json={"entry": []},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 6. OutboundMessage dataclass
# ---------------------------------------------------------------------------


class TestOutboundMessage:
    """Basic dataclass contract."""

    def test_defaults(self):
        msg = OutboundMessage(text="hello")
        assert msg.text == "hello"
        assert msg.image_png is None
        assert msg.to == ""

    def test_with_image(self):
        png = b"\x89PNG" + b"\x00" * 10
        msg = OutboundMessage(text="chart", image_png=png, to="C1")
        assert msg.image_png[:4] == b"\x89PNG"
        assert msg.to == "C1"


# ---------------------------------------------------------------------------
# 7. NullTransport Protocol compliance
# ---------------------------------------------------------------------------


class TestNullTransport:
    """NullTransport satisfies the ChatTransport Protocol."""

    def test_is_chat_transport(self):
        from app.chat.gateway import ChatTransport

        transport = NullTransport()
        assert isinstance(transport, ChatTransport)

    def test_send_records_messages(self):
        transport = NullTransport()
        msg1 = OutboundMessage(text="a", to="C1")
        msg2 = OutboundMessage(text="b", to="C2")
        transport.send("C1", msg1)
        transport.send("C2", msg2)
        assert len(transport.sent) == 2
        assert transport.sent[0] == ("C1", msg1)
        assert transport.sent[1] == ("C2", msg2)
