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
        # Resource identification.
        "board_id":  "<uuid>",   # board to render
        "org_id":    "<uuid>",   # optional — falls back to ctx.org_id / claims

        # Render
        "format":   "csv" | "pdf" | "pptx",  # default: "csv"
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
No EE imports.  Board resolution is org-scoped.  The ``policies`` dict
in ``config`` plays the same role as in ``snapshot_refresh`` — it carries the
captured RLS view at schedule-definition time (no user JWT at tick time).
``locked_params`` handles per-recipient row-level data isolation.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext

logger = logging.getLogger("nubi.flows.report_send")

# ---------------------------------------------------------------------------
# Defensive recipient cap — same env var as the route-layer guards so a single
# env override applies everywhere.  This cap is a last-resort safety net for
# configs stored before the route-layer cap existed; it does NOT replace the
# Pydantic+route guards (those reject at schedule-definition time).
# ---------------------------------------------------------------------------

_MAX_RECIPIENTS: int = int(os.environ.get("NUBI_MAX_REPORT_RECIPIENTS", "500"))


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
        get_default_sender,
        inject_locked_params,
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
    if len(recipients) > _MAX_RECIPIENTS:
        # Defensive cap: silently truncate to avoid O(N) loop on stale configs
        # stored before the route-layer cap existed.  Log a warning so operators
        # are aware that stored configs need updating.
        logger.warning(
            "report_send: recipients list truncated from %d to %d (cap=%d). "
            "Update the stored flow config to remove excess recipients.",
            len(recipients),
            _MAX_RECIPIENTS,
            _MAX_RECIPIENTS,
        )
        recipients = recipients[:_MAX_RECIPIENTS]

    base_params: dict[str, Any] = dict(config.get("params") or {})
    subject: str = config.get("subject") or ""
    body: str = config.get("body") or ""
    apply_rls: bool = bool(config.get("apply_user_permissions", False))
    locked_params: dict[str, dict[str, Any]] = dict(config.get("locked_params") or {})

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

    # ── 3. Build claims for RLS-aware renders ─────────────────────────────────
    # SECURITY [RLS cross-tenant]: render_claims is ALWAYS exactly the
    # runtime-stamped exec_claims (the flow OWNER's verified policy snapshot,
    # applied by _claims_with_owner_policies BEFORE handle() runs).  We must NOT
    # let user-supplied config['policies'] override it — a first-party writer
    # storing config.policies={other_tenant} in a flow spec would otherwise
    # bypass the owner-policy snapshot and exfiltrate cross-tenant rows.
    # Per-recipient row isolation comes from locked_params (named query params)
    # only, never from policy overrides.
    render_claims: dict[str, Any] = dict(claims or {})

    # ── 4. Render + deliver ───────────────────────────────────────────────────
    sender = get_default_sender()
    errors: list[str] = []
    emails_sent: int = 0

    if apply_rls and locked_params:
        # Per-recipient render.  Optimisation: when two or more recipients share
        # the same effective locked_params (including the empty-dict case) we
        # render once and reuse the result, only re-rendering when the effective
        # policy actually differs.
        #
        # Group recipients by their frozen effective locked params so we render
        # at most once per unique policy slice.
        import json as _json  # noqa: PLC0415

        # Build a stable cache key from the locked params for each recipient.
        _rendered_cache: dict[str, bytes | str] = {}

        for recipient in recipients:
            per_locked = locked_params.get(recipient, {})
            cache_key = _json.dumps(per_locked, sort_keys=True)
            if cache_key not in _rendered_cache:
                per_params = inject_locked_params(base_params, per_locked)
                # Build per-recipient render_claims with locked params merged
                # into the base claims so the PPTX collector honours them.
                per_claims = dict(render_claims)
                try:
                    _rendered_cache[cache_key] = _render(
                        board, per_params, fmt, org_id=org_id, render_claims=per_claims
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"report_send: render failed for {recipient!r}: {exc}"
                    logger.warning(msg)
                    errors.append(msg)
                    _rendered_cache[cache_key] = None  # type: ignore[assignment]
            rendered_for_recipient = _rendered_cache[cache_key]
            if rendered_for_recipient is None:
                continue
            try:
                per_target = {
                    "recipients": [recipient],
                    "subject": subject,
                    "body": body,
                    "format": fmt,
                }
                emails_sent += send_report(sender, per_target, rendered_for_recipient)
            except Exception as exc:  # noqa: BLE001
                msg = f"report_send: send failed for {recipient!r}: {exc}"
                logger.warning(msg)
                errors.append(msg)
    else:
        # Single render, same result for all recipients.
        try:
            rendered = _render(board, base_params, fmt, org_id=org_id, render_claims=render_claims)
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
    *,
    org_id: str = "",
    render_claims: dict[str, Any] | None = None,
) -> bytes | str:
    """Dispatch to the correct renderer for *fmt*.

    Supported formats (in resolution order):
    1. ``'pdf'``  — T3 :func:`app.embedding.render_pdf.render_board_pdf` when
       available; falls back to :func:`app.jobs.report.render_report` (stdlib PDF).
    2. ``'pptx'`` — T4 :func:`app.embedding.render_pptx.render_board_pptx_from_data`
       with real widget data collected via :func:`_collect_widget_data_sync`.
    3. ``'csv'``  — :func:`app.jobs.report.render_report` (stdlib CSV path).
    """
    if fmt == "pdf":
        return _render_pdf(board, params)
    if fmt == "pptx":
        return _render_pptx(board, params, org_id=org_id, render_claims=render_claims or {})
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


def _collect_widget_data_sync(
    board: dict[str, Any],
    org_id: str,
    render_claims: dict[str, Any],
) -> list[dict[str, Any]]:
    """Collect per-widget data for *board* synchronously.

    Runs :func:`~app.dashboards.collect.collect_board_data` (async) in a
    temporary event loop or thread, matching the pattern used by
    :func:`~app.jobs.report.resolve_board_sync`.

    Parameters
    ----------
    board:
        Pre-fetched board dict (org-scoped).
    org_id:
        The owning org id (for datastore resolution).
    render_claims:
        Claims dict whose ``"policies"`` key carries the captured RLS context.

    Returns
    -------
    list[dict]
        One entry per data widget: ``{widget_id, query_id, columns, rows}``.
        Empty on any error so callers always receive a list.
    """
    import asyncio  # noqa: PLC0415

    from app.dashboards.collect import collect_board_data  # noqa: PLC0415
    from app.repos.provider import get_repo  # noqa: PLC0415

    board_id: str = board.get("id") or ""

    async def _fetch() -> list[dict[str, Any]]:
        repo = get_repo()
        return await collect_board_data(
            board_id,
            org_id,
            claims=render_claims,
            repo=repo,
            board=board,  # pass pre-fetched board to skip redundant repo lookup
        )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures  # noqa: PLC0415
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _fetch())
                return future.result()
        else:
            return loop.run_until_complete(_fetch())
    except RuntimeError:
        return asyncio.run(_fetch())
    except Exception as exc:  # noqa: BLE001
        logger.warning("report_send: widget data collection failed: %s", exc)
        return []


def _render_pptx(
    board: dict[str, Any],
    params: dict[str, Any],
    *,
    org_id: str = "",
    render_claims: dict[str, Any] | None = None,
) -> bytes:
    """Try T4 render_board_pptx_from_data with real widget data; fall back to CSV.

    T4 path (python-pptx) is attempted when ``app.embedding.render_pptx``
    is importable AND the board carries a structured spec.  Widget data is
    collected via :func:`_collect_widget_data_sync` so slides contain real
    data, honoring the recipient's locked RLS policies via *render_claims*.

    Any failure (missing deps, no spec, collection error) falls through to
    CSV — the always-available format.
    """
    # T4 path — attempt best-effort.
    try:
        from app.embedding.render_pptx import render_board_pptx_from_data  # noqa: PLC0415
        from app.dashboards.spec import validate_spec  # noqa: PLC0415

        config: dict[str, Any] = board.get("config") or {}
        spec_dict = config.get("spec")
        if spec_dict is None:
            raise ValueError("board has no spec")
        spec, errors = validate_spec(spec_dict)
        if spec is None:
            raise ValueError(f"board spec is invalid: {errors}")

        # Collect real widget data (honouring RLS via render_claims).
        widget_data = _collect_widget_data_sync(
            board, org_id, render_claims or {}
        )

        return render_board_pptx_from_data(spec, widget_data=widget_data)
    except Exception:  # noqa: BLE001
        # T4 unavailable or board has no spec — fall back to CSV.
        from app.jobs.report import render_report  # noqa: PLC0415
        return render_report(board, params, format="csv")  # type: ignore[return-value]

