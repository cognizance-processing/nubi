"""Tests for T6 — the ``report_send`` Flows task kind.

Coverage
--------
1. ``report_send`` is registered in the task-kind registry.
2. ``reset_for_tests`` re-registers ``report_send``.
3. Handler renders CSV and delivers via NullSender (once per recipient).
4. Per-recipient RLS: ``apply_user_permissions=True`` + ``locked_params``
   triggers one render+send per recipient.
5. Missing ``board_id`` raises AppError.
6. Missing ``recipients`` raises AppError.
7. Unknown ``format`` raises AppError.
8. Board not found → AppError.
9. ``notify_channels`` best-effort call (NullChannel).
10. Full Flows-engine scheduled run: drain_flow_run with a single
    ``report_send`` task reaches ``'success'`` and delivers (NullSender).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest

from app.errors import AppError
from app.flows.executor import TaskContext
from app.flows.handlers.report_send import handle as report_send_handle
from app.flows.registry import get_task_kind_registry, reset_for_tests
from app.jobs.report import NullSender


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _make_board(
    board_id: str | None = None,
    org_id: str = "org-test",
    widget_query_id: str = "demo_points_10k",
) -> dict[str, Any]:
    bid = board_id or str(uuid.uuid4())
    return {
        "id": bid,
        "org_id": org_id,
        "name": "Test Board",
        "config": {
            "spec": {
                "version": 1,
                "title": "Test Board",
                "layout": {"cols": 12, "row_height": 60},
                "widgets": [
                    {
                        "id": "w1",
                        "type": "table",
                        "query_id": widget_query_id,
                        "encoding": {},
                        "props": {},
                        # pos x/y must be >= 1 per DashboardSpec validation
                        "pos": {"x": 1, "y": 1, "w": 12, "h": 4},
                    }
                ],
            }
        },
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _ctx(org_id: str = "org-test") -> TaskContext:
    return TaskContext(org_id=org_id)


def _patch_board(board: dict[str, Any] | None):
    """Patch resolve_board_sync where it is imported (app.jobs.report)."""
    return patch(
        "app.jobs.report.resolve_board_sync",
        return_value=board,
    )


# ---------------------------------------------------------------------------
# 1. Registry: report_send is registered
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_report_send_is_registered(self):
        """``report_send`` must appear in the task kind registry."""
        reset_for_tests()
        reg = get_task_kind_registry()
        kinds = list(reg.all().keys())
        assert "report_send" in kinds

    def test_report_send_handler_is_callable(self):
        """The registered handler must be callable."""
        reset_for_tests()
        reg = get_task_kind_registry()
        handler = reg.get("report_send")
        assert callable(handler)

    def test_reset_for_tests_re_registers_report_send(self):
        """``reset_for_tests`` must re-register report_send (idempotent)."""
        reset_for_tests()
        reg = get_task_kind_registry()
        reg._handlers.pop("report_send", None)  # manually remove
        assert "report_send" not in reg.all()
        reset_for_tests()
        reg2 = get_task_kind_registry()
        assert "report_send" in reg2.all()

    def test_existing_kinds_still_registered(self):
        """Pre-existing kinds must not be removed when report_send is added."""
        reset_for_tests()
        reg = get_task_kind_registry()
        for kind in ("query", "python", "noop", "snapshot_refresh"):
            assert kind in reg.all(), f"{kind!r} missing after reset_for_tests"


# ---------------------------------------------------------------------------
# 2. Handler — CSV, single render for all recipients
# ---------------------------------------------------------------------------


class TestHandleCsv:
    def test_csv_delivers_to_all_recipients(self):
        """CSV render → NullSender called once per recipient."""
        board = _make_board()
        recipients = ["a@x.com", "b@x.com", "c@x.com"]
        sender = NullSender()

        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": recipients,
            "subject": "Weekly Report",
            "body": "See attached.",
        }

        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 3
        assert result["format"] == "csv"
        assert result["recipients_count"] == 3
        assert len(sender.sent) == 3
        sent_tos = {s["to"] for s in sender.sent}
        assert sent_tos == set(recipients)

    def test_csv_attachment_name(self):
        """Attachment name must be ``report.csv`` for CSV format."""
        board = _make_board()
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["a@x.com"],
        }
        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                report_send_handle(config, _ctx(), claims={})

        assert sender.sent[0]["attachment_name"] == "report.csv"

    def test_result_shape(self):
        """Return dict must carry all expected keys."""
        board = _make_board()
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["a@x.com"],
        }
        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                result = report_send_handle(config, _ctx(), claims={})

        for key in ("board_id", "org_id", "format", "recipients_count", "emails_sent",
                    "channel_notifications", "errors"):
            assert key in result, f"missing key {key!r} in result"


# ---------------------------------------------------------------------------
# 3. Per-recipient RLS
# ---------------------------------------------------------------------------


class TestPerRecipientRls:
    def test_one_send_per_recipient_with_locked_params(self):
        """apply_user_permissions=True → one render+send per recipient."""
        board = _make_board()
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["alice@x.com", "bob@x.com"],
            "apply_user_permissions": True,
            "locked_params": {
                "alice@x.com": {"tenant_id": "acme"},
                "bob@x.com": {"tenant_id": "globex"},
            },
        }
        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 2
        assert len(sender.sent) == 2
        assert {s["to"] for s in sender.sent} == {"alice@x.com", "bob@x.com"}

    def test_rls_without_locked_params_uses_base(self):
        """apply_user_permissions=True with no locked_params falls through to
        the single-render path (no locked_params → not apply_rls branch)."""
        board = _make_board()
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["a@x.com", "b@x.com"],
            "apply_user_permissions": True,
            "locked_params": {},
        }
        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 2


# ---------------------------------------------------------------------------
# 4. Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_missing_board_id_raises(self):
        """Missing board_id must raise AppError(invalid_task_config)."""
        config = {
            "org_id": "org-test",
            "format": "csv",
            "recipients": ["a@x.com"],
        }
        with pytest.raises(AppError) as exc_info:
            report_send_handle(config, _ctx(), claims={})
        assert exc_info.value.code == "invalid_task_config"

    def test_missing_recipients_raises(self):
        """Empty recipients list must raise AppError(invalid_task_config)."""
        config = {
            "board_id": str(uuid.uuid4()),
            "org_id": "org-test",
            "format": "csv",
            "recipients": [],
        }
        with pytest.raises(AppError) as exc_info:
            report_send_handle(config, _ctx(), claims={})
        assert exc_info.value.code == "invalid_task_config"

    def test_unknown_format_raises(self):
        """Unsupported format must raise AppError(invalid_task_config)."""
        config = {
            "board_id": str(uuid.uuid4()),
            "org_id": "org-test",
            "format": "xlsx",
            "recipients": ["a@x.com"],
        }
        with pytest.raises(AppError) as exc_info:
            report_send_handle(config, _ctx(), claims={})
        assert exc_info.value.code == "invalid_task_config"

    def test_board_not_found_raises(self):
        """Board not found → AppError(board_not_found)."""
        config = {
            "board_id": str(uuid.uuid4()),
            "org_id": "org-test",
            "format": "csv",
            "recipients": ["a@x.com"],
        }
        with _patch_board(None):
            with pytest.raises(AppError) as exc_info:
                report_send_handle(config, _ctx(), claims={})
        assert exc_info.value.code == "board_not_found"

    def test_missing_org_id_raises(self):
        """Missing org_id in config AND ctx → AppError(invalid_task_config)."""
        config = {
            "board_id": str(uuid.uuid4()),
            "format": "csv",
            "recipients": ["a@x.com"],
            # org_id intentionally absent
        }
        ctx = TaskContext(org_id=None)  # no org_id in ctx either
        with pytest.raises(AppError) as exc_info:
            report_send_handle(config, ctx, claims={})
        assert exc_info.value.code == "invalid_task_config"


# ---------------------------------------------------------------------------
# 5. org_id resolution order
# ---------------------------------------------------------------------------


class TestOrgIdResolution:
    def test_org_id_from_config(self):
        """org_id in config takes priority over ctx.org_id."""
        board = _make_board(org_id="config-org")
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "org_id": "config-org",
            "format": "csv",
            "recipients": ["a@x.com"],
        }
        ctx = TaskContext(org_id="ctx-org")
        with _patch_board(board) as mock_resolve:
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                report_send_handle(config, ctx, claims={})
        mock_resolve.assert_called_once_with(board["id"], "config-org")

    def test_org_id_from_ctx(self):
        """ctx.org_id used when config has no org_id."""
        board = _make_board(org_id="ctx-org")
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "format": "csv",
            "recipients": ["a@x.com"],
            # org_id absent — should fall back to ctx.org_id
        }
        ctx = TaskContext(org_id="ctx-org")
        with _patch_board(board) as mock_resolve:
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                report_send_handle(config, ctx, claims={})
        mock_resolve.assert_called_once_with(board["id"], "ctx-org")

    def test_org_id_from_claims(self):
        """claims['org_id'] used when config and ctx lack org_id."""
        board = _make_board(org_id="claims-org")
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "format": "csv",
            "recipients": ["a@x.com"],
        }
        ctx = TaskContext(org_id=None)
        with _patch_board(board) as mock_resolve:
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                report_send_handle(config, ctx, claims={"org_id": "claims-org"})
        mock_resolve.assert_called_once_with(board["id"], "claims-org")


# ---------------------------------------------------------------------------
# 6. Notify channels best-effort (NullChannel)
# ---------------------------------------------------------------------------


class TestNotifyChannels:
    def test_null_channel_counted(self):
        """A ``null`` channel must be called and counted."""
        board = _make_board()
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["a@x.com"],
            "notify_channels": [{"kind": "null"}],
        }
        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                result = report_send_handle(config, _ctx(), claims={})

        assert result["channel_notifications"] == 1

    def test_broken_channel_logged_not_raised(self):
        """A channel that raises must not propagate — recorded in errors."""
        from app.notify.channels import ChannelError

        board = _make_board()
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["a@x.com"],
            "notify_channels": [{"kind": "slack", "webhook_url": "http://bad"}],
        }

        def _raising_send(text, image_png=None):
            raise ChannelError("simulated channel failure")

        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch("app.notify.channels.get_channel") as mock_ch:
                    from unittest.mock import MagicMock
                    ch = MagicMock()
                    ch.send.side_effect = _raising_send
                    mock_ch.return_value = ch
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["channel_notifications"] == 0
        assert len(result["errors"]) > 0


# ---------------------------------------------------------------------------
# 7. PDF format delivers bytes attachment
# ---------------------------------------------------------------------------


class TestPdfFormat:
    def test_pdf_attachment_name(self):
        """PDF format must produce a ``report.pdf`` attachment."""
        board = _make_board()
        sender = NullSender()
        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "pdf",
            "recipients": ["a@x.com"],
        }
        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                result = report_send_handle(config, _ctx(), claims={})

        assert result["format"] == "pdf"
        assert result["emails_sent"] == 1
        assert sender.sent[0]["attachment_name"] == "report.pdf"
        # Must be bytes (PDF) not a str.
        assert isinstance(sender.sent[0]["attachment_data"], bytes)


# ---------------------------------------------------------------------------
# 8. Flows engine: drain_flow_run with report_send task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_flow_run_report_send():
    """A Flows run with a single ``report_send`` task reaches ``'success'``
    and triggers the configured email delivery (NullSender capture)."""
    from app.flows.runtime import drain_flow_run, materialize_flow_run
    from app.flows.store import InMemoryFlowStore

    reset_for_tests()
    store = InMemoryFlowStore()
    board = _make_board()
    sender = NullSender()

    flow = await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="scheduled_report_flow",
        spec={
            "version": 1,
            "name": "scheduled_report_flow",
            "tasks": [
                {
                    "key": "send_report",
                    "kind": "report_send",
                    "needs": [],
                    "config": {
                        "board_id": board["id"],
                        "org_id": board["org_id"],
                        "format": "csv",
                        "recipients": ["alice@example.com", "bob@example.com"],
                        "subject": "Scheduled Report",
                        "body": "Delivered by Flows.",
                    },
                }
            ],
        },
    )

    from datetime import datetime, timezone
    now = datetime(2025, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    flow_run = await materialize_flow_run(store, flow, {}, "schedule", now)

    with _patch_board(board):
        with patch("app.jobs.report.get_default_sender", return_value=sender):
            final_run = await drain_flow_run(
                store, flow_run["id"], now, claims={"org_id": "org-test"}
            )

    assert final_run["state"] == "success", (
        f"Expected flow_run state='success', got {final_run['state']!r}"
    )
    assert len(sender.sent) == 2
    sent_tos = {s["to"] for s in sender.sent}
    assert sent_tos == {"alice@example.com", "bob@example.com"}


# ---------------------------------------------------------------------------
# 9. SECURITY [RLS cross-tenant] — config.policies must NOT override the
#    runtime-stamped owner-policy snapshot.
# ---------------------------------------------------------------------------


class TestPoliciesOwnerSnapshotEnforced:
    """SECURITY: render_claims is ALWAYS the runtime-stamped exec_claims.

    The flow runtime stamps exec_claims['policies'] from the flow OWNER's
    VERIFIED token snapshot (via ``_claims_with_owner_policies``) BEFORE
    ``handle()`` is called.  A first-party writer who stores
    ``config.policies={other_tenant}`` in a flow spec must NOT be able to
    override that snapshot — doing so would bypass the owner-policy snapshot and
    exfiltrate cross-tenant rows via a scheduled report.  The handler therefore
    NEVER reads ``config['policies']``; render_claims == the incoming
    (owner-verified) exec_claims, unchanged.
    """

    def test_config_policies_do_not_override_owner_snapshot_board(self):
        """Board path: a foreign-tenant config.policies does NOT change the
        applied RLS — render_claims keeps the owner's verified policies."""
        board = _make_board()
        sender = NullSender()

        owner_policies = {"tenant_id": "owner_tenant"}
        foreign_policies = {"tenant_id": "other_tenant"}

        config: dict[str, Any] = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["a@x.com"],
            # Malicious: attempt to override owner RLS with a foreign tenant.
            "policies": foreign_policies,
        }
        # Runtime has already stamped exec_claims with the owner snapshot.
        exec_claims: dict[str, Any] = {
            "org_id": board["org_id"],
            "policies": owner_policies,
        }

        captured_claims: list[dict] = []

        def _capture_render(board_obj, params, fmt, *, org_id="", render_claims=None):
            captured_claims.append(dict(render_claims or {}))
            return b"col\nval\n"

        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render",
                    side_effect=_capture_render,
                ):
                    result = report_send_handle(config, _ctx(), claims=exec_claims)

        assert result["emails_sent"] == 1
        assert result["errors"] == []
        assert len(captured_claims) == 1
        assert captured_claims[0]["policies"] == owner_policies, (
            "config.policies must NOT override the owner-verified RLS snapshot "
            f"(cross-tenant exfil). Got {captured_claims[0]['policies']!r}, "
            f"expected {owner_policies!r}."
        )
        assert captured_claims[0]["policies"] != foreign_policies

    def test_owner_snapshot_preserved_when_no_config_policies(self):
        """When config has no policies, the owner snapshot in exec_claims is used."""
        board = _make_board()
        sender = NullSender()
        owner_policies = {"tenant_id": "owner_tenant"}

        config: dict[str, Any] = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["a@x.com"],
        }
        exec_claims: dict[str, Any] = {
            "org_id": board["org_id"],
            "policies": owner_policies,
        }

        captured_claims: list[dict] = []

        def _capture_render(board_obj, params, fmt, *, org_id="", render_claims=None):
            captured_claims.append(dict(render_claims or {}))
            return b"col\nval\n"

        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render",
                    side_effect=_capture_render,
                ):
                    result = report_send_handle(config, _ctx(), claims=exec_claims)

        assert result["emails_sent"] == 1
        assert len(captured_claims) == 1
        assert captured_claims[0]["policies"] == owner_policies


# ---------------------------------------------------------------------------
# 11. Shared-policy recipients: collect once, not O(recipients × widgets)
# ---------------------------------------------------------------------------


class TestSharedPolicyCollectOnce:
    """When recipients share the same effective locked_params, collect ONCE.

    Distinct locked_params must still trigger separate renders (per-recipient RLS).
    """

    def test_shared_policy_renders_once(self):
        """Three recipients with the SAME locked_params → one render call."""
        board = _make_board()
        sender = NullSender()
        render_call_count = [0]

        def _counting_render(b, params, fmt, *, org_id="", render_claims=None):
            render_call_count[0] += 1
            return b"rendered"

        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["a@x.com", "b@x.com", "c@x.com"],
            "apply_user_permissions": True,
            "locked_params": {
                # All three recipients get the same slice → one render.
                "a@x.com": {"tenant_id": "acme"},
                "b@x.com": {"tenant_id": "acme"},
                "c@x.com": {"tenant_id": "acme"},
            },
        }

        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render",
                    side_effect=_counting_render,
                ):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 3
        assert render_call_count[0] == 1, (
            f"Expected 1 render for shared policy, got {render_call_count[0]}"
        )

    def test_distinct_policies_render_separately(self):
        """Two recipients with DIFFERENT locked_params → two separate renders."""
        board = _make_board()
        sender = NullSender()
        render_call_count = [0]

        def _counting_render(b, params, fmt, *, org_id="", render_claims=None):
            render_call_count[0] += 1
            return b"rendered"

        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["alice@x.com", "bob@x.com"],
            "apply_user_permissions": True,
            "locked_params": {
                "alice@x.com": {"tenant_id": "acme"},
                "bob@x.com": {"tenant_id": "globex"},  # different
            },
        }

        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render",
                    side_effect=_counting_render,
                ):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 2
        assert render_call_count[0] == 2, (
            f"Expected 2 renders for distinct policies, got {render_call_count[0]}"
        )

    def test_mixed_policies_render_per_unique_slice(self):
        """Four recipients: 2 share policy A, 2 share policy B → 2 renders."""
        board = _make_board()
        sender = NullSender()
        render_call_count = [0]

        def _counting_render(b, params, fmt, *, org_id="", render_claims=None):
            render_call_count[0] += 1
            return b"rendered"

        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["a@x.com", "b@x.com", "c@x.com", "d@x.com"],
            "apply_user_permissions": True,
            "locked_params": {
                "a@x.com": {"tenant_id": "acme"},
                "b@x.com": {"tenant_id": "acme"},   # same as a → reuse
                "c@x.com": {"tenant_id": "globex"},
                "d@x.com": {"tenant_id": "globex"},  # same as c → reuse
            },
        }

        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render",
                    side_effect=_counting_render,
                ):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 4
        assert render_call_count[0] == 2, (
            f"Expected 2 renders for 2 unique policies, got {render_call_count[0]}"
        )

    def test_rls_still_correct_per_recipient(self):
        """Each recipient must receive the render produced with their own policy."""
        board = _make_board()
        sender = NullSender()

        # Track which params each render was called with.
        render_params_log: list[dict] = []

        def _logging_render(b, params, fmt, *, org_id="", render_claims=None):
            render_params_log.append(dict(params))
            return f"data_for_{params.get('tenant_id', 'all')}".encode()

        config = {
            "board_id": board["id"],
            "org_id": board["org_id"],
            "format": "csv",
            "recipients": ["alice@x.com", "bob@x.com"],
            "apply_user_permissions": True,
            "locked_params": {
                "alice@x.com": {"tenant_id": "acme"},
                "bob@x.com": {"tenant_id": "globex"},
            },
        }

        with _patch_board(board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render",
                    side_effect=_logging_render,
                ):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 2
        # Each recipient gets the attachment rendered with their own params.
        sent_by_to = {s["to"]: s["attachment_data"] for s in sender.sent}
        assert sent_by_to["alice@x.com"] == b"data_for_acme"
        assert sent_by_to["bob@x.com"] == b"data_for_globex"
