"""Tests for the Nubi notification system (notify/channels.py + notify/alerts.py).

Nubi ships EMAIL as its one outbound notify channel (+ the no-op NullChannel);
Slack/WhatsApp/Teams/Google Chat connectors were removed — the embedding host
owns any chat-platform notifications it wants.

Coverage
--------
1. NullChannel.send records a send with correct text and image_png.
2. EmailChannel calls EmailSender.send with correct args.
3. get_channel('null') / unknown kind returns NullChannel.
4. get_channel('email', ...) returns EmailChannel.
5. format_alert_text formats a failed flow event correctly.
6. notify_alert sends via NullChannel and records the send.
7. Flow-failure listener (on_flow_event) calls notify_alert on 'failed' status.
8. on_flow_event does NOT call notify_alert on 'succeeded' status.
9. Simulated flow event via emit_flow_event (if available) triggers the listener.

Network safety
--------------
No network calls are made anywhere in this suite (EmailChannel uses NullSender).
"""

from __future__ import annotations

from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1: NullChannel
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
# 2: EmailChannel
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
# 3-4: get_channel factory
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

    def test_removed_chat_kinds_return_null_channel(self):
        """Slack/WhatsApp/Teams/Google Chat are no longer recognised kinds."""
        from app.notify.channels import get_channel, NullChannel

        for kind in ("slack", "whatsapp", "teams", "google_chat", "webhook"):
            ch = get_channel(kind, {"webhook_url": "https://example.com/hook"})
            assert isinstance(ch, NullChannel), f"kind={kind!r} should degrade to NullChannel"

    def test_email_returns_email_channel(self):
        from app.notify.channels import get_channel, EmailChannel

        ch = get_channel("email", {"recipient": "ops@x.com"})
        assert isinstance(ch, EmailChannel)


# ---------------------------------------------------------------------------
# 5: format_alert_text
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
# 6: notify_alert
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
# 7-9: Flow-failure listener (on_flow_event)
# ---------------------------------------------------------------------------


class TestFlowListener:
    def test_on_flow_event_fires_on_failed(self):
        from app.notify.channels import NullChannel
        from app.notify.alerts import on_flow_event

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
        import pytest

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
