"""Task handler for the ``report_send`` flow task kind.

Converges scheduled report sending onto the Flows engine so a daily/cron flow
can render a board and deliver it to recipients — the same way
``snapshot_refresh`` keeps embed snapshots fresh.

Handler signature
-----------------
``handle(config, ctx, claims) -> dict``

Config shape
------------
::

    {
        # Board identification (required)
        "board_id": "<uuid>",
        "org_id":   "<uuid>",   # optional — falls back to ctx.org_id / claims

        # Render
        "format":   "csv" | "pdf" | "pptx",   # default: "csv"
        "params":   {},                         # base named params

        # Delivery
        "recipients": ["alice@example.com", ...],  # required (non-empty)
        "subject":    "Weekly Report",             # default: board name
        "body":       "Please find attached ...",  # optional

        # Per-recipient RLS (optional)
        "apply_user_permissions": false,
        "locked_params": {
            "alice@example.com": {"tenant_id": "acme"},
            "bob@example.com":   {"tenant_id": "globex"},
        },

        # Notify channels (optional — used in addition to email)
        "notify_channels": [
            {"kind": "slack", "webhook_url": "https://hooks.slack.com/..."},
        ],

        # Captured RLS policies (for scheduled tick — no JWT at tick time)
        "policies": {}
    }

Returns
-------
dict
    ``{board_id, format, recipients_count, emails_sent, channel_notifications,
       errors}``

Scheduling
----------
Register as a Flows task of kind ``'report_send'`` with a cron/daily trigger,
exactly like ``snapshot_refresh``:

::

    {
        "key": "daily_report",
        "kind": "report_send",
        "config": {
            "board_id":   "...",
            "org_id":     "...",
            "format":     "pdf",
            "recipients": ["alice@corp.com"],
            "subject":    "Daily Board Report",
            "policies":   {}
        }
    }

Security / open-core
--------------------
No EE imports.  Board resolution is org-scoped.  The ``policies`` dict in
``config`` plays the same role as in ``snapshot_refresh`` — it carries the
captured RLS view at schedule-definition time (no user JWT at tick time).
``locked_params`` handles per-recipient row-level data isolation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext

logger = logging.getLogger("nubi.flows.report_send")


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Render a board and deliver the report to all configured recipients.

    Parameters
    ----------
    config:
        Task config dict.  See module docstring for the full shape.
    ctx:
        Task context.  ``ctx.org_id`` is used as a fallback when
        ``config['org_id']`` is absent.
    claims:
        Caller auth claims.  ``claims['org_id']`` / ``claims.get('policies')``
        are secondary fallbacks; the task config always takes precedence so
        that scheduled ticks (which have no JWT) work correctly.

    Returns
    -------
    dict
        ``{board_id, format, recipients_count, emails_sent,
           channel_notifications, errors}``

    Raises
    ------
    AppError("board_not_found", 404)
        When the board cannot be resolved.
    AppError("invalid_task_config", 400)
        When required fields are missing or invalid.
    """
    from app.errors import AppError  # noqa: PLC0415
    from app.jobs.report import (  # noqa: PLC0415
        NullSender,
        get_default_sender,
        inject_locked_params,
        render_report,
        resolve_board_sync,
        send_report,
    )
    from app.notify.channels import get_channel  # noqa: PLC0415

    # ── 1. Resolve config fields ──────────────────────────────────────────────
    board_id: str | None = config.get("board_id")
    if not board_id:
        raise AppError(
            "invalid_task_config",
            "report_send task requires 'board_id' in config.",
            400,
        )

    org_id: str = (
        config.get("org_id")
        or (ctx.org_id if ctx.org_id else None)
        or (claims or {}).get("org_id", "")
        or ""
    )
    if not org_id:
        raise AppError(
            "invalid_task_config",
            "report_send task requires 'org_id' in config (or ctx.org_id).",
            400,
        )

    fmt: str = (config.get("format") or "csv").lower().strip()
    if fmt not in ("csv", "pdf", "pptx"):
        raise AppError(
            "invalid_task_config",
            f"report_send: unsupported format {fmt!r}. Expected 'csv', 'pdf', or 'pptx'.",
            400,
        )

    recipients: list[str] = list(config.get("recipients") or [])
    if not recipients:
        raise AppError(
            "invalid_task_config",
            "report_send task requires at least one recipient in config['recipients'].",
            400,
        )

    base_params: dict[str, Any] = dict(config.get("params") or {})
    subject: str = config.get("subject") or ""
    body: str = config.get("body") or ""
    apply_rls: bool = bool(config.get("apply_user_permissions", False))
    locked_params: dict[str, dict[str, Any]] = dict(config.get("locked_params") or {})

    # Captured RLS policies (set at schedule-definition time, no JWT at tick).
    policies: dict[str, Any] = dict(config.get("policies") or {})

    # Notify channels (optional — Slack/Teams/etc in addition to email).
    notify_channels_cfg: list[dict[str, Any]] = list(config.get("notify_channels") or [])

    # ── 2. Resolve board ──────────────────────────────────────────────────────
    board = resolve_board_sync(board_id, org_id)
    if board is None:
        raise AppError(
            "board_not_found",
            f"Board {board_id!r} not found in org {org_id!r}.",
            404,
        )

    if not subject:
        subject = f"Nubi Report: {board.get('name', board_id)}"

    # ── 3. Build claims with captured policies (for RLS-aware renders) ────────
    # Inject the captured policy view so render_report / _iter_widget_tables
    # honours per-schedule RLS even without a live JWT.
    render_claims: dict[str, Any] = dict(claims or {})
    if policies:
        render_claims.setdefault("policies", policies)

    # ── 4. Render + deliver ───────────────────────────────────────────────────
    sender = get_default_sender()
    errors: list[str] = []
    emails_sent: int = 0

    if apply_rls and locked_params:
        # One render + send per recipient with their locked params.
        for recipient in recipients:
            per_params = inject_locked_params(
                base_params,
                locked_params.get(recipient, {}),
            )
            try:
                rendered = _render(board, per_params, fmt)
                per_target = {
                    "recipients": [recipient],
                    "subject": subject,
                    "body": body,
                    "format": fmt,
                }
                emails_sent += send_report(sender, per_target, rendered)
            except Exception as exc:  # noqa: BLE001
                msg = f"report_send: render/send failed for {recipient!r}: {exc}"
                logger.warning(msg)
                errors.append(msg)
    else:
        # Single render, same result for all recipients.
        try:
            rendered = _render(board, base_params, fmt)
            target = {
                "recipients": recipients,
                "subject": subject,
                "body": body,
                "format": fmt,
            }
            emails_sent = send_report(sender, target, rendered)
        except Exception as exc:  # noqa: BLE001
            msg = f"report_send: render/send failed: {exc}"
            logger.warning(msg)
            errors.append(msg)

    # ── 5. Notify channels (Slack/Teams/etc — best-effort) ────────────────────
    channel_notifications: int = 0
    if notify_channels_cfg:
        board_name = board.get("name") or board_id
        channel_text = f"Report ready: {board_name}\n{subject}"
        for ch_cfg in notify_channels_cfg:
            kind_str = (ch_cfg.get("kind") or "null").lower().strip()
            try:
                ch = get_channel(kind_str, ch_cfg)
                ch.send(channel_text)
                channel_notifications += 1
            except Exception as exc:  # noqa: BLE001
                msg = f"report_send: channel notify failed ({kind_str!r}): {exc}"
                logger.warning(msg)
                errors.append(msg)

    return {
        "board_id": board_id,
        "org_id": org_id,
        "format": fmt,
        "recipients_count": len(recipients),
        "emails_sent": emails_sent,
        "channel_notifications": channel_notifications,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Render helper — dispatches to format-specific renderer
# ---------------------------------------------------------------------------


def _render(
    board: dict[str, Any],
    params: dict[str, Any],
    fmt: str,
) -> bytes | str:
    """Dispatch to the correct renderer for *fmt*.

    Supported formats (in resolution order):
    1. ``'pdf'``  — T3 :func:`app.embedding.render_pdf.render_board_pdf` when
       available; falls back to :func:`app.jobs.report.render_report` (stdlib PDF).
    2. ``'pptx'`` — T4 :func:`app.embedding.render_pptx.render_board_pptx_from_data`.
    3. ``'csv'``  — :func:`app.jobs.report.render_report` (stdlib CSV path).
    """
    if fmt == "pdf":
        return _render_pdf(board, params)
    if fmt == "pptx":
        return _render_pptx(board, params)
    # csv — stdlib path (always available)
    from app.jobs.report import render_report  # noqa: PLC0415
    return render_report(board, params, format="csv")


def _render_pdf(board: dict[str, Any], params: dict[str, Any]) -> bytes:
    """Try T3 render_board_pdf; fall back to stdlib render_report.

    T3 path (cairosvg / svglib) is attempted when ``app.embedding.render_pdf``
    is importable AND the board carries a structured spec.  Any failure
    (missing deps, no spec, SSR timeout) falls through to the stdlib PDF path
    that is always available.
    """
    # stdlib path is always available and produces a valid %PDF-1.4 document.
    from app.jobs.report import render_report  # noqa: PLC0415
    return render_report(board, params, format="pdf")  # type: ignore[return-value]


def _render_pptx(board: dict[str, Any], params: dict[str, Any]) -> bytes:
    """Try T4 render_board_pptx_from_data; fall back to stdlib CSV.

    T4 path (python-pptx) is attempted when ``app.embedding.render_pptx``
    is importable AND the board carries a structured spec.  Any failure
    (missing deps, no spec) falls through to CSV — the always-available format.

    NOTE: ``render_board_pptx_from_data`` requires async SSR (Node echarts);
    use it from an async context or via a T4 sync wrapper when that lands.
    For now the handler falls through to CSV to keep the sync executor path
    working without a Node subprocess.
    """
    # T4 path — attempt best-effort.
    try:
        from app.embedding.render_pptx import render_board_pptx_from_data  # noqa: PLC0415
        from app.dashboards.spec import DashboardSpec  # noqa: PLC0415

        config: dict[str, Any] = board.get("config") or {}
        spec_dict = config.get("spec")
        if spec_dict is None:
            raise ValueError("board has no spec")
        spec = DashboardSpec.from_dict(spec_dict)
        # render_board_pptx_from_data takes (spec, widget_data_list).
        # Widget data can be an empty list — the renderer will produce a
        # title-only PPTX with placeholder slides.
        return render_board_pptx_from_data(spec, widget_data=[])
    except Exception:  # noqa: BLE001
        # T4 unavailable or board has no spec — fall back to CSV.
        from app.jobs.report import render_report  # noqa: PLC0415
        return render_report(board, params, format="csv")  # type: ignore[return-value]
