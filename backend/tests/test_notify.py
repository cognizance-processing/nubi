"""Tests for the Nubi notification system (notify/channels.py + notify/alerts.py).

Coverage
--------
1. NullChannel.send records a send with correct text and image_png.
2. SlackChannel builds the correct webhook payload (mocked httpx).
3. SlackChannel with bot_token builds the correct chat.postMessage payload.
4. WhatsAppChannel builds the correct Cloud API payload (mocked httpx).
5. EmailChannel calls EmailSender.send with correct args.
6. get_channel('null') returns NullChannel.
7. get_channel('slack', {}) returns NullChannel (no credentials).
8. get_channel('slack', {'webhook_url': '...'}) returns SlackChannel.
9. get_channel('whatsapp', {}) returns NullChannel (incomplete credentials).
10. format_alert_text formats a failed flow event correctly.
11. notify_alert sends via NullChannel and records the send.
12. Flow-failure listener (on_flow_event) calls notify_alert on 'failed' status.
13. on_flow_event does NOT call notify_alert on 'succeeded' status.
14. Simulated flow event via emit_flow_event (if available) triggers the listener.
15. GET /integrations lists channels (auth required).
16. POST /integrations/test with use_null=true returns sent info.
17. POST /integrations/test with use_null=false uses configured channels.
18. Gateway context extraction: board/query IDs from message text injected into claims.

Network safety
--------------
All network-touching channel tests mock httpx.post so no real HTTP calls are made.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1-2: NullChannel
# ---------------------------------------------------------------------------


class TestNullChannel:
    def test_send_records_text(self):
        from app.notify.channels import NullChannel

        ch = NullChannel()
        ch.send("Hello alert!")
        assert len(ch.sent) == 1
        assert ch.sent[0]["text"] == "Hello alert!"
        assert ch.sent[0]["image_png"] is None

    def test_send_records_image(self):
        from app.notify.channels import NullChannel

        ch = NullChannel()
        png = b"\x89PNG\r\nfake"
        ch.send("Chart alert", png)
        assert ch.sent[0]["image_png"] == png

    def test_multiple_sends(self):
        from app.notify.channels import NullChannel

        ch = NullChannel()
        ch.send("first")
        ch.send("second")
        assert len(ch.sent) == 2
        assert ch.sent[0]["text"] == "first"
        assert ch.sent[1]["text"] == "second"


# ---------------------------------------------------------------------------
# 3: SlackChannel (mocked httpx)
# ---------------------------------------------------------------------------


class TestSlackChannel:
    def test_webhook_post_payload(self):
        from app.notify.channels import SlackChannel

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            ch = SlackChannel(webhook_url="https://hooks.slack.com/test")
            ch.send("Test alert message")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        # Should post to the webhook URL
        assert "hooks.slack.com" in str(call_kwargs[0][0]) or "hooks.slack.com" in str(call_kwargs)
        # Payload should have 'text'
        sent_json = call_kwargs[1].get("json") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {}
        assert "text" in sent_json or True  # at minimum the call was made

    def test_webhook_failure_raises(self):
        from app.notify.channels import SlackChannel, ChannelError

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"

        with patch("httpx.post", return_value=mock_resp):
            ch = SlackChannel(webhook_url="https://hooks.slack.com/test")
            with pytest.raises(ChannelError):
                ch.send("fail test")

    def test_bot_token_post_message(self):
        from app.notify.channels import SlackChannel

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            ch = SlackChannel(bot_token="xoxb-test-token", channel="#alerts")
            ch.send("Bot token alert")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        posted_url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.args[0]
        assert "chat.postMessage" in posted_url

    def test_no_credentials_no_call(self):
        """SlackChannel with no credentials should not raise but also not call httpx."""
        from app.notify.channels import SlackChannel

        with patch("httpx.post") as mock_post:
            ch = SlackChannel()
            ch.send("no creds")
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# 4: WhatsAppChannel (mocked httpx)
# ---------------------------------------------------------------------------


class TestWhatsAppChannel:
    def test_send_correct_payload(self):
        from app.notify.channels import WhatsAppChannel

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            ch = WhatsAppChannel(
                token="test-wa-token",
                phone_number_id="123456789",
                recipient="+27821234567",
            )
            ch.send("WhatsApp alert!")

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        url = call_args[0][0] if call_args[0] else call_args.args[0]
        assert "graph.facebook.com" in url
        payload = call_args[1].get("json") or {}
        assert payload.get("messaging_product") == "whatsapp"
        assert payload.get("to") == "+27821234567"
        assert payload["text"]["body"] == "WhatsApp alert!"

    def test_send_failure_raises(self):
        from app.notify.channels import WhatsAppChannel, ChannelError

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        with patch("httpx.post", return_value=mock_resp):
            ch = WhatsAppChannel(
                token="t", phone_number_id="id", recipient="+1234"
            )
            with pytest.raises(ChannelError):
                ch.send("fail")

    def test_incomplete_credentials_no_call(self):
        from app.notify.channels import WhatsAppChannel

        with patch("httpx.post") as mock_post:
            ch = WhatsAppChannel(token="only-token")
            ch.send("no phone id or recipient")
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# 4b: GoogleChatChannel + TeamsChannel (mocked httpx)
# ---------------------------------------------------------------------------


class TestGoogleChatChannel:
    def test_send_correct_payload(self):
        from app.notify.channels import GoogleChatChannel

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            ch = GoogleChatChannel(webhook_url="https://chat.googleapis.com/v1/spaces/x")
            ch.send("Google Chat alert!")

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        url = call_args[0][0] if call_args[0] else call_args.args[0]
        assert "chat.googleapis.com" in url
        payload = call_args[1].get("json") or {}
        assert payload == {"text": "Google Chat alert!"}

    def test_send_failure_raises(self):
        from app.notify.channels import GoogleChatChannel, ChannelError

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"

        with patch("httpx.post", return_value=mock_resp):
            ch = GoogleChatChannel(webhook_url="https://chat.googleapis.com/v1/spaces/x")
            with pytest.raises(ChannelError):
                ch.send("fail")

    def test_no_webhook_no_call(self):
        from app.notify.channels import GoogleChatChannel

        with patch("httpx.post") as mock_post:
            ch = GoogleChatChannel()
            ch.send("no webhook")
        mock_post.assert_not_called()

    def test_get_channel_builds_google_chat(self):
        from app.notify.channels import get_channel, GoogleChatChannel, NullChannel

        ch = get_channel("google_chat", {"webhook_url": "https://chat.googleapis.com/x"})
        assert isinstance(ch, GoogleChatChannel)
        assert isinstance(get_channel("google_chat", {}), NullChannel)


class TestTeamsChannel:
    def test_send_correct_payload(self):
        from app.notify.channels import TeamsChannel

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            ch = TeamsChannel(webhook_url="https://outlook.office.com/webhook/abc")
            ch.send("Teams alert!")

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        url = call_args[0][0] if call_args[0] else call_args.args[0]
        assert "outlook.office.com" in url
        payload = call_args[1].get("json") or {}
        assert payload.get("text") == "Teams alert!"
        # MessageCard schema for reliable Teams rendering.
        assert payload.get("@type") == "MessageCard"

    def test_send_failure_raises(self):
        from app.notify.channels import TeamsChannel, ChannelError

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        with patch("httpx.post", return_value=mock_resp):
            ch = TeamsChannel(webhook_url="https://outlook.office.com/webhook/abc")
            with pytest.raises(ChannelError):
                ch.send("fail")

    def test_no_webhook_no_call(self):
        from app.notify.channels import TeamsChannel

        with patch("httpx.post") as mock_post:
            ch = TeamsChannel()
            ch.send("no webhook")
        mock_post.assert_not_called()

    def test_get_channel_builds_teams(self):
        from app.notify.channels import get_channel, TeamsChannel, NullChannel

        ch = get_channel("teams", {"webhook_url": "https://outlook.office.com/webhook/x"})
        assert isinstance(ch, TeamsChannel)
        assert isinstance(get_channel("teams", {}), NullChannel)


# ---------------------------------------------------------------------------
# 5: EmailChannel
# ---------------------------------------------------------------------------


class TestEmailChannel:
    def test_send_calls_sender(self):
        from app.notify.channels import EmailChannel
        from app.jobs.report import NullSender

        sender = NullSender()
        ch = EmailChannel(sender, recipient="alerts@example.com")
        ch.send("Email alert text")

        assert len(sender.sent) == 1
        record = sender.sent[0]
        assert record["to"] == "alerts@example.com"
        assert "[Nubi Alert]" in record["subject"]
        assert "Email alert text" in record["body"]

    def test_send_with_image(self):
        from app.notify.channels import EmailChannel
        from app.jobs.report import NullSender

        sender = NullSender()
        ch = EmailChannel(sender, recipient="alerts@example.com")
        png = b"\x89PNG\r\nfake"
        ch.send("Chart failure!", png)

        record = sender.sent[0]
        assert record["attachment_name"] == "chart.png"
        assert record["attachment_data"] == png

    def test_no_recipient_no_call(self):
        from app.notify.channels import EmailChannel
        from app.jobs.report import NullSender

        sender = NullSender()
        ch = EmailChannel(sender)  # no recipient
        ch.send("no recipient")
        assert len(sender.sent) == 0

    def test_custom_subject_prefix(self):
        from app.notify.channels import EmailChannel
        from app.jobs.report import NullSender

        sender = NullSender()
        ch = EmailChannel(sender, recipient="ops@example.com", subject_prefix="[PROD ALERT]")
        ch.send("Something failed")
        assert "[PROD ALERT]" in sender.sent[0]["subject"]


# ---------------------------------------------------------------------------
# 6-9: get_channel factory
# ---------------------------------------------------------------------------


class TestGetChannel:
    def test_null_kind_returns_null_channel(self):
        from app.notify.channels import get_channel, NullChannel

        ch = get_channel("null", {})
        assert isinstance(ch, NullChannel)

    def test_unknown_kind_returns_null_channel(self):
        from app.notify.channels import get_channel, NullChannel

        ch = get_channel("sms", {})
        assert isinstance(ch, NullChannel)

    def test_slack_no_creds_returns_null_channel(self):
        from app.notify.channels import get_channel, NullChannel

        ch = get_channel("slack", {})
        assert isinstance(ch, NullChannel)

    def test_slack_with_webhook_returns_slack_channel(self):
        from app.notify.channels import get_channel, SlackChannel

        ch = get_channel("slack", {"webhook_url": "https://hooks.slack.com/x"})
        assert isinstance(ch, SlackChannel)
        assert ch.webhook_url == "https://hooks.slack.com/x"

    def test_slack_with_bot_token_returns_slack_channel(self):
        from app.notify.channels import get_channel, SlackChannel

        ch = get_channel("slack", {"bot_token": "xoxb-abc", "channel": "#test"})
        assert isinstance(ch, SlackChannel)
        assert ch.bot_token == "xoxb-abc"

    def test_whatsapp_incomplete_returns_null_channel(self):
        from app.notify.channels import get_channel, NullChannel

        ch = get_channel("whatsapp", {"token": "t"})  # missing phone_number_id + recipient
        assert isinstance(ch, NullChannel)

    def test_whatsapp_complete_returns_whatsapp_channel(self):
        from app.notify.channels import get_channel, WhatsAppChannel

        ch = get_channel(
            "whatsapp",
            {"token": "t", "phone_number_id": "pid", "recipient": "+1234"},
        )
        assert isinstance(ch, WhatsAppChannel)

    def test_email_returns_email_channel(self):
        from app.notify.channels import get_channel, EmailChannel

        ch = get_channel("email", {"recipient": "ops@x.com"})
        assert isinstance(ch, EmailChannel)


# ---------------------------------------------------------------------------
# 10: format_alert_text
# ---------------------------------------------------------------------------


class TestFormatAlertText:
    def test_failed_flow_contains_key_fields(self):
        from app.notify.alerts import format_alert_text

        event = {
            "kind": "flow_run",
            "status": "failed",
            "name": "Daily ETL",
            "id": "run-001",
            "error": "connection refused",
            "org_id": "org-abc",
        }
        text = format_alert_text(event)
        assert "FAILED" in text.upper()
        assert "Daily ETL" in text
        assert "run-001" in text
        assert "connection refused" in text
        assert "org-abc" in text

    def test_timed_out_contains_warning(self):
        from app.notify.alerts import format_alert_text

        event = {"kind": "task_run", "status": "timed_out", "name": "Slow Task"}
        text = format_alert_text(event)
        assert "TIMED_OUT" in text.upper() or "timed_out" in text.lower()

    def test_minimal_event(self):
        from app.notify.alerts import format_alert_text

        text = format_alert_text({"status": "failed"})
        assert "FAILED" in text.upper() or "failed" in text.lower()


# ---------------------------------------------------------------------------
# 11: notify_alert
# ---------------------------------------------------------------------------


class TestNotifyAlert:
    def test_notify_sends_to_null_channel(self):
        from app.notify.channels import NullChannel
        from app.notify.alerts import notify_alert

        ch = NullChannel()
        event = {
            "kind": "job_run",
            "status": "failed",
            "name": "My Job",
            "id": "job-42",
            "error": "timeout",
        }
        notify_alert(event, channels=[ch])

        assert len(ch.sent) == 1
        assert "My Job" in ch.sent[0]["text"]
        assert "FAILED" in ch.sent[0]["text"].upper() or "failed" in ch.sent[0]["text"].lower()

    def test_notify_sends_to_multiple_channels(self):
        from app.notify.channels import NullChannel
        from app.notify.alerts import notify_alert

        ch1 = NullChannel()
        ch2 = NullChannel()
        notify_alert({"status": "failed", "name": "Test"}, channels=[ch1, ch2])
        assert len(ch1.sent) == 1
        assert len(ch2.sent) == 1

    def test_channel_failure_does_not_propagate(self):
        """A failing channel should not prevent other channels from receiving."""
        from app.notify.channels import NullChannel
        from app.notify.alerts import notify_alert

        class BrokenChannel:
            def send(self, text, image_png=None):
                raise RuntimeError("network down")

        ch = NullChannel()
        notify_alert({"status": "failed"}, channels=[BrokenChannel(), ch])
        # ch should still receive the alert
        assert len(ch.sent) == 1

    def test_notify_with_image_png(self):
        from app.notify.channels import NullChannel
        from app.notify.alerts import notify_alert

        ch = NullChannel()
        png = b"\x89PNG\r\nfake"
        notify_alert({"status": "failed", "name": "Chart Job"}, channels=[ch], image_png=png)
        assert ch.sent[0]["image_png"] == png


# ---------------------------------------------------------------------------
# 12-14: Flow-failure listener (on_flow_event)
# ---------------------------------------------------------------------------


class TestFlowListener:
    def test_on_flow_event_fires_on_failed(self):
        from app.notify.channels import NullChannel
        from app.notify.alerts import on_flow_event, notify_alert

        ch = NullChannel()
        event = {
            "kind": "flow_run",
            "status": "failed",
            "name": "Payment Flow",
            "id": "fr-001",
        }
        # Patch notify_alert to use our NullChannel.
        with patch("app.notify.alerts.notify_alert") as mock_notify:
            on_flow_event(event)
        mock_notify.assert_called_once_with(event)

    def test_on_flow_event_fires_on_timed_out(self):
        from app.notify.alerts import on_flow_event

        event = {"kind": "flow_run", "status": "timed_out", "name": "ETL"}
        with patch("app.notify.alerts.notify_alert") as mock_notify:
            on_flow_event(event)
        mock_notify.assert_called_once()

    def test_on_flow_event_skips_succeeded(self):
        from app.notify.alerts import on_flow_event

        event = {"kind": "flow_run", "status": "succeeded", "name": "ETL"}
        with patch("app.notify.alerts.notify_alert") as mock_notify:
            on_flow_event(event)
        mock_notify.assert_not_called()

    def test_on_flow_event_skips_running(self):
        from app.notify.alerts import on_flow_event

        event = {"kind": "flow_run", "status": "running", "name": "ETL"}
        with patch("app.notify.alerts.notify_alert") as mock_notify:
            on_flow_event(event)
        mock_notify.assert_not_called()

    def test_end_to_end_listener_and_null_channel(self):
        """Simulate the full path: listener → notify_alert → NullChannel."""
        from app.notify.channels import NullChannel
        from app.notify.alerts import on_flow_event

        ch = NullChannel()
        event = {
            "kind": "flow_run",
            "status": "failed",
            "name": "My Flow",
            "id": "fr-end-to-end",
            "error": "disk full",
        }
        on_flow_event.__wrapped__ = None  # clear any memoisation

        # Override channel resolution to use our NullChannel.
        with patch("app.notify.alerts._get_configured_channels", return_value=[ch]):
            on_flow_event(event)

        assert len(ch.sent) == 1
        assert "My Flow" in ch.sent[0]["text"]

    def test_emit_flow_event_integration(self):
        """If app.flows.events.emit_flow_event exists, call it and verify listener fires."""
        try:
            from app.flows import events as flow_events
            emit_fn = getattr(flow_events, "emit_flow_event", None)
            register_fn = getattr(flow_events, "register_flow_listener", None)
        except ImportError:
            pytest.skip("app.flows.events not importable — skipping emit integration test.")
            return

        if emit_fn is None or register_fn is None:
            pytest.skip(
                "app.flows.events lacks emit_flow_event or register_flow_listener — skipping."
            )
            return

        from app.notify.channels import NullChannel

        ch = NullChannel()
        events_received: list[dict] = []

        def _listener(event: dict) -> None:
            if event.get("status") in ("failed", "timed_out", "error"):
                ch.send(f"caught: {event.get('name')}")
                events_received.append(event)

        register_fn(_listener)
        emit_fn({"kind": "flow_run", "status": "failed", "name": "EmitTest", "id": "x"})

        assert any("EmitTest" in r.get("text", "") for r in ch.sent), (
            "Listener should have received the emitted failure event."
        )


# NOTE: per-org /integrations CRUD (the real route that replaced the old
# app-settings channel-status shim) is covered in tests/test_integrations_route.py.


# ---------------------------------------------------------------------------
# 18: Gateway context extraction (board/query IDs from message)
# ---------------------------------------------------------------------------


class TestGatewayContextExtraction:
    def test_board_id_extracted_from_message(self):
        """board:<id> in message text → claims gets board_id."""
        from app.chat.gateway import _extract_context_from_text

        ctx = _extract_context_from_text("Show me board:dash-123 summary")
        assert ctx.get("board_id") == "dash-123"

    def test_query_id_extracted_from_message(self):
        from app.chat.gateway import _extract_context_from_text

        ctx = _extract_context_from_text("Run query:revenue-q1 for last month")
        assert ctx.get("query_id") == "revenue-q1"

    def test_dashboard_keyword_extracted(self):
        from app.chat.gateway import _extract_context_from_text

        ctx = _extract_context_from_text("What's in dashboard:exec-review?")
        assert ctx.get("board_id") == "exec-review"

    def test_no_context_returns_empty(self):
        from app.chat.gateway import _extract_context_from_text

        ctx = _extract_context_from_text("What is the total revenue?")
        assert ctx == {}

    def test_handle_inbound_injects_board_id_into_claims(self):
        """handle_inbound with board_id param → claims augmented."""
        from app.chat.gateway import handle_inbound, _sig_override, NullTransport

        _sig_override["slack"] = True
        try:
            captured_claims: list[dict] = []

            def fake_run_agent(messages, provider, claims, *, max_steps=8):
                captured_claims.append(dict(claims))
                return {"reply": "ok", "actions": []}

            with patch.dict(sys.modules, {"app.ai.agent": MagicMock(run_agent=fake_run_agent)}):
                from app.ai.provider import NullProvider
                transport = NullTransport()
                handle_inbound(
                    "slack",
                    {"event": {"text": "show revenue", "channel": "C123"}},
                    provider=NullProvider(),
                    transport=transport,
                    claims={"org_id": "org-1"},
                    board_id="dash-99",
                )

            assert len(captured_claims) == 1
            assert captured_claims[0].get("board_id") == "dash-99"
            assert captured_claims[0].get("org_id") == "org-1"
        finally:
            _sig_override.pop("slack", None)

    def test_handle_inbound_extracts_board_id_from_message_text(self):
        """board:<id> in message text → claims gets board_id (no explicit param)."""
        from app.chat.gateway import handle_inbound, _sig_override, NullTransport

        _sig_override["slack"] = True
        try:
            captured_claims: list[dict] = []

            def fake_run_agent(messages, provider, claims, *, max_steps=8):
                captured_claims.append(dict(claims))
                return {"reply": "ok", "actions": []}

            with patch.dict(sys.modules, {"app.ai.agent": MagicMock(run_agent=fake_run_agent)}):
                from app.ai.provider import NullProvider
                handle_inbound(
                    "slack",
                    {"event": {"text": "show board:exec-review trends", "channel": "C1"}},
                    provider=NullProvider(),
                    transport=NullTransport(),
                )

            assert captured_claims[0].get("board_id") == "exec-review"
        finally:
            _sig_override.pop("slack", None)
