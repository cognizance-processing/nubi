"""Tests for the Prefect-style flow-run alert path.

Covers the two pieces owned by the chat/gateway domain:

A. ``app.chat.notify`` — the outbound alert formatter + config resolution +
   dispatch (formatter content, ``resolve_alert_config`` precedence,
   ``should_alert`` gating, ``notify_flow_run`` best-effort delivery).

B. ``app.flows.runtime._fire_flow_alert`` — the post-run alert hook firing when
   a flow_run finalises, end-to-end against an in-memory flow store, with the
   notify channels swapped for a recording ``NullChannel``.

Everything is network-free: channels are ``NullChannel`` (record-only) and no
real provider/HTTP is touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# A1. format_flow_alert — content
# ---------------------------------------------------------------------------


class TestFormatFlowAlert:
    def test_failed_event_contains_key_fields(self):
        from app.chat.notify import format_flow_alert

        text = format_flow_alert(
            {
                "kind": "flow_run",
                "name": "Daily ETL",
                "state": "failed",
                "flow_run_id": "fr-001",
                "duration_s": 83,
                "link": "https://app.nubi.dev/flows/f1/runs/fr-001",
                "failed_task": "load_warehouse",
                "error": "connection refused",
                "org_id": "org-abc",
            }
        )
        assert "FAILED" in text.upper()
        assert "Daily ETL" in text
        assert "fr-001" in text
        assert "load_warehouse" in text
        assert "connection refused" in text
        assert "1m 23s" in text  # 83s humanised
        assert "https://app.nubi.dev/flows/f1/runs/fr-001" in text
        assert "org-abc" in text

    def test_success_event_omits_failure_fields(self):
        from app.chat.notify import format_flow_alert

        text = format_flow_alert(
            {"name": "Nightly", "state": "success", "duration_s": 5}
        )
        assert "SUCCESS" in text.upper()
        assert "Nightly" in text
        assert "5s" in text
        assert "Failed task" not in text
        assert "Error" not in text

    def test_minimal_event_does_not_raise(self):
        from app.chat.notify import format_flow_alert

        text = format_flow_alert({"state": "failed"})
        assert "FAILED" in text.upper()

    def test_duration_formatting(self):
        from app.chat.notify import _fmt_duration

        assert _fmt_duration(None) == "unknown"
        assert _fmt_duration(5) == "5s"
        assert _fmt_duration(83) == "1m 23s"
        assert _fmt_duration(3661) == "1h 1m"


# ---------------------------------------------------------------------------
# A2. resolve_alert_config + should_alert
# ---------------------------------------------------------------------------


class TestResolveAlertConfig:
    def test_flow_spec_alerts_used(self):
        from app.chat.notify import resolve_alert_config

        flow = {"spec": {"alerts": {"on": ["failed"], "slack_channel": "#ops"}}}
        cfg = resolve_alert_config(flow, None)
        assert cfg["on"] == ["failed"]
        assert cfg["slack_channel"] == "#ops"

    def test_flow_config_alerts_used_when_no_spec(self):
        from app.chat.notify import resolve_alert_config

        flow = {"config": {"alerts": {"on": ["success"]}}}
        cfg = resolve_alert_config(flow, None)
        assert cfg["on"] == ["success"]

    def test_flow_overrides_org_defaults(self):
        from app.chat.notify import resolve_alert_config

        flow = {"spec": {"alerts": {"on": ["failed"]}}}
        org_defaults = {"on": ["success"], "slack_channel": "#default"}
        cfg = resolve_alert_config(flow, org_defaults)
        # Flow's "on" wins; org's channel is inherited (not overridden).
        assert cfg["on"] == ["failed"]
        assert cfg["slack_channel"] == "#default"

    def test_org_defaults_only(self):
        from app.chat.notify import resolve_alert_config

        cfg = resolve_alert_config(None, {"on": ["failed"]})
        assert cfg["on"] == ["failed"]

    def test_no_config_is_empty(self):
        from app.chat.notify import resolve_alert_config

        assert resolve_alert_config(None, None) == {}
        assert resolve_alert_config({"spec": {}}, None) == {}

    def test_list_alerts_coerced_to_on(self):
        from app.chat.notify import resolve_alert_config

        cfg = resolve_alert_config({"spec": {"alerts": ["FAILED", "Success"]}}, None)
        assert cfg["on"] == ["failed", "success"]

    def test_true_alerts_means_all_states(self):
        from app.chat.notify import resolve_alert_config

        cfg = resolve_alert_config({"spec": {"alerts": True}}, None)
        assert "failed" in cfg["on"] and "success" in cfg["on"]


class TestShouldAlert:
    def test_empty_config_never_alerts(self):
        from app.chat.notify import should_alert

        assert should_alert({}, "failed") is False

    def test_state_in_on_fires(self):
        from app.chat.notify import should_alert

        assert should_alert({"on": ["failed", "success"]}, "success") is True
        assert should_alert({"on": ["failed"]}, "success") is False

    def test_default_events_when_on_absent(self):
        from app.chat.notify import should_alert, DEFAULT_ALERT_EVENTS

        cfg = {"slack_channel": "#x"}  # truthy config, no explicit "on"
        assert should_alert(cfg, "failed") is True
        assert should_alert(cfg, "success") is ("success" in DEFAULT_ALERT_EVENTS)

    def test_case_insensitive(self):
        from app.chat.notify import should_alert

        assert should_alert({"on": ["FAILED"]}, "failed") is True


# ---------------------------------------------------------------------------
# A3. notify_flow_run — dispatch (best-effort)
# ---------------------------------------------------------------------------


class TestNotifyFlowRun:
    def test_sends_to_explicit_channels(self):
        from app.chat.notify import notify_flow_run
        from app.notify.channels import NullChannel

        ch = NullChannel()
        sent = notify_flow_run(
            {"name": "ETL", "state": "failed"}, channels=[ch]
        )
        assert sent == 1
        assert "ETL" in ch.sent[0]["text"]

    def test_no_channels_is_noop(self):
        from app.chat.notify import notify_flow_run

        assert notify_flow_run({"name": "X", "state": "failed"}, channels=[]) == 0

    def test_channel_failure_does_not_propagate(self):
        from app.chat.notify import notify_flow_run
        from app.notify.channels import NullChannel

        class Broken:
            def send(self, text, image_png=None):
                raise RuntimeError("network down")

        ch = NullChannel()
        sent = notify_flow_run(
            {"state": "failed"}, channels=[Broken(), ch]
        )
        # The good channel still got it; the broken one is swallowed.
        assert sent == 1
        assert len(ch.sent) == 1

    def test_channels_for_empty_when_unconfigured(self):
        from app.chat.notify import channels_for

        # No overrides + (in test env) no settings creds → no channels.
        assert channels_for({}) == []


# ---------------------------------------------------------------------------
# B. _fire_flow_alert — the runtime hook, end-to-end
# ---------------------------------------------------------------------------


def _utc() -> datetime:
    return datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
class TestFireFlowAlertHook:
    async def _seed(self, alerts: Any, state: str = "failed"):
        """Create a flow + finalised flow_run + task_runs in an in-memory store."""
        from app.flows.store import InMemoryFlowStore

        store = InMemoryFlowStore()
        spec: dict[str, Any] = {"version": 1, "name": "alertable", "tasks": []}
        if alerts is not None:
            spec["alerts"] = alerts
        flow = await store.create_flow(
            org_id="org-1", created_by="u1", name="Daily ETL", spec=spec
        )
        run = await store.create_flow_run(
            flow_id=flow["id"], org_id="org-1", params={}, trigger="manual"
        )
        await store.update_flow_run(
            run["id"],
            {"state": state, "started_at": _utc() - timedelta(seconds=42)},
        )
        return store, flow, run

    async def test_hook_fires_for_failed_when_configured(self):
        from app.flows import runtime
        from app.notify.channels import NullChannel

        store, flow, run = await self._seed({"on": ["failed", "success"]})
        task_runs = [
            {"task_key": "load", "state": "failed", "error": "boom"},
        ]
        ch = NullChannel()
        now = _utc()
        with patch("app.chat.notify.channels_for", return_value=[ch]):
            await runtime._fire_flow_alert(store, run["id"], "failed", task_runs, now)

        assert len(ch.sent) == 1
        text = ch.sent[0]["text"]
        assert "FAILED" in text.upper()
        assert "Daily ETL" in text
        assert "load" in text
        assert "boom" in text

    async def test_hook_fires_for_success_when_opted_in(self):
        from app.flows import runtime
        from app.notify.channels import NullChannel

        store, flow, run = await self._seed({"on": ["success"]}, state="success")
        ch = NullChannel()
        with patch("app.chat.notify.channels_for", return_value=[ch]):
            await runtime._fire_flow_alert(store, run["id"], "success", [], _utc())

        assert len(ch.sent) == 1
        assert "SUCCESS" in ch.sent[0]["text"].upper()

    async def test_hook_noop_when_state_not_in_on(self):
        from app.flows import runtime
        from app.notify.channels import NullChannel

        store, flow, run = await self._seed({"on": ["failed"]}, state="success")
        ch = NullChannel()
        with patch("app.chat.notify.channels_for", return_value=[ch]):
            await runtime._fire_flow_alert(store, run["id"], "success", [], _utc())

        assert ch.sent == []

    async def test_hook_noop_when_no_alert_config(self):
        from app.flows import runtime
        from app.notify.channels import NullChannel

        store, flow, run = await self._seed(None, state="failed")
        ch = NullChannel()
        with patch("app.chat.notify.channels_for", return_value=[ch]):
            await runtime._fire_flow_alert(store, run["id"], "failed", [], _utc())

        assert ch.sent == []

    async def test_hook_never_raises_on_store_error(self):
        from app.flows import runtime

        class BrokenStore:
            async def get_flow_run(self, _):
                raise RuntimeError("db down")

        # Must not raise — best-effort.
        await runtime._fire_flow_alert(BrokenStore(), "fr-x", "failed", [], _utc())

    async def test_hook_includes_duration_and_link(self):
        from app.flows import runtime
        from app.notify.channels import NullChannel

        store, flow, run = await self._seed({"on": ["failed"]})
        ch = NullChannel()
        with patch("app.chat.notify.channels_for", return_value=[ch]), patch(
            "app.flows.runtime._flow_run_link", return_value="http://ui/flows/f/runs/r"
        ):
            await runtime._fire_flow_alert(store, run["id"], "failed", [], _utc())

        text = ch.sent[0]["text"]
        assert "42s" in text  # started_at = now-42s
        assert "http://ui/flows/f/runs/r" in text
