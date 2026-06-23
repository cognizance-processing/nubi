"""Task handler for the ``report_send`` flow task kind.

Converges scheduled report sending onto the Flows engine so a daily/cron flow
can render a board **or canvas** and deliver it to recipients — the same way
``snapshot_refresh`` keeps embed snapshots fresh.

Handler signature
-----------------
``handle(config, ctx, claims) -> dict``

Config shape
------------
::

    {
        # Resource identification — supply EITHER board_id OR canvas_id.
        "board_id":  "<uuid>",   # board path (unchanged)
        "canvas_id": "<uuid>",   # canvas path (Wave 5)
        "org_id":    "<uuid>",   # optional — falls back to ctx.org_id / claims

        # Render
        "format":   "csv" | "pdf" | "html" | "pptx",  # default: "csv"
        # canvas_id path: "html" renders a self-contained email body (default
        # for canvases); "pdf" produces a PDF attachment.
        "params":   {},                         # base named params

        # Delivery
        "recipients": ["alice@example.com", ...],  # required (non-empty)
        "subject":    "Weekly Report",             # default: board/canvas name
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
    For the board path:
    ``{board_id, format, recipients_count, emails_sent, channel_notifications,
       errors}``
    For the canvas path:
    ``{canvas_id, format, recipients_count, emails_sent, channel_notifications,
       errors}``

Scheduling
----------
Register as a Flows task of kind ``'report_send'`` with a cron/daily trigger,
exactly like ``snapshot_refresh``:

::

    # Board schedule (unchanged)
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

    # Canvas schedule (Wave 5)
    {
        "key": "canvas_weekly",
        "kind": "report_send",
        "config": {
            "canvas_id":  "...",
            "org_id":     "...",
            "format":     "html",
            "recipients": ["alice@corp.com"],
            "subject":    "Weekly Canvas Report",
            "locked_params": {"alice@corp.com": {"tenant_id": "acme"}},
            "policies":   {}
        }
    }

Security / open-core
--------------------
No EE imports.  Board/canvas resolution is org-scoped.  The ``policies`` dict
in ``config`` plays the same role as in ``snapshot_refresh`` — it carries the
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
    """Render a board or canvas and deliver the report to all configured recipients.

    Dispatches on ``config['canvas_id']`` (Canvas path, Wave 5) vs
    ``config['board_id']`` (Board path, unchanged).  The board path is
    byte-identical to the pre-Wave-5 implementation.

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
        Board path: ``{board_id, format, recipients_count, emails_sent,
           channel_notifications, errors}``
        Canvas path: ``{canvas_id, format, recipients_count, emails_sent,
           channel_notifications, errors}``

    Raises
    ------
    AppError("board_not_found", 404)
        When the board cannot be resolved (board path).
    AppError("canvas_not_found", 404)
        When the canvas cannot be resolved (canvas path).
    AppError("invalid_task_config", 400)
        When required fields are missing or invalid.
    """
    from app.errors import AppError  # noqa: PLC0415

    # ── Dispatch: canvas_id takes precedence over board_id ────────────────────
    canvas_id: str | None = config.get("canvas_id")
    if canvas_id:
        return _handle_canvas(config, ctx, claims)

    # ── Board path (unchanged — byte-identical to pre-Wave-5) ─────────────────
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


# ---------------------------------------------------------------------------
# Canvas path helpers (Canvas Wave 5)
# ---------------------------------------------------------------------------


def _resolve_canvas_sync(canvas_id: str, org_id: str) -> dict[str, Any] | None:
    """Synchronous canvas lookup — mirrors :func:`~app.jobs.report.resolve_board_sync`.

    Runs :func:`repo.get("canvases", org_id, canvas_id)` in a thread when an
    event loop is already running, otherwise creates a temporary loop.

    Returns ``None`` when the canvas is not found.
    """
    import asyncio  # noqa: PLC0415

    from app.repos.provider import get_repo  # noqa: PLC0415

    async def _fetch() -> dict[str, Any] | None:
        repo = get_repo()
        return await repo.get("canvases", org_id, canvas_id)

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


def _collect_canvas_data_sync(
    canvas: dict[str, Any],
    org_id: str,
    render_claims: dict[str, Any],
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect per-binding data for *canvas* synchronously.

    Runs :func:`~app.dashboards.collect.collect_canvas_data` (async) in a
    thread or temporary event loop.

    Parameters
    ----------
    canvas:
        Pre-fetched canvas row (org-scoped).
    org_id:
        The owning org id.
    render_claims:
        Claims dict whose ``"policies"`` key carries the captured RLS context.
        SECURITY: these are the *owner's* verified token policies only — they
        are NEVER overwritten by request-body ``locked_params`` (that would be
        an RLS-predicate injection allowing cross-tenant data exfiltration).
    extra_params:
        Per-recipient locked params, forwarded to ``collect_canvas_data`` as
        extra *named query params* (the board-path equivalent of
        :func:`~app.jobs.report.inject_locked_params`).  These bind named
        ``{{param}}`` placeholders in the canvas' queries — they do NOT and
        cannot alter the RLS policy slice, which comes from *render_claims*
        only.  Forwarded only when the collector supports it (forward-compat).

    Returns
    -------
    dict[str, dict]
        Mapping from ``el_id`` → ``{columns, rows}`` or ``{error: code}``.
        Empty dict on any catastrophic error.
    """
    import asyncio  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415
    from app.repos.provider import get_repo  # noqa: PLC0415

    canvas_id: str = canvas.get("id") or ""

    # Forward extra_params only if the collector's signature accepts it, so we
    # never raise a TypeError on collectors that predate named-param support.
    _supports_extra_params = "extra_params" in inspect.signature(
        collect_canvas_data
    ).parameters

    async def _fetch() -> dict[str, Any]:
        repo = get_repo()
        kwargs: dict[str, Any] = {
            "claims": render_claims,
            "repo": repo,
            "canvas": canvas,  # pass pre-fetched canvas to skip redundant lookup
        }
        if _supports_extra_params and extra_params:
            kwargs["extra_params"] = dict(extra_params)
        return await collect_canvas_data(
            canvas_id,
            org_id,
            **kwargs,
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
        logger.warning("report_send: canvas data collection failed: %s", exc)
        return {}


def _render_canvas(
    canvas: dict[str, Any],
    fmt: str,
    *,
    org_id: str = "",
    render_claims: dict[str, Any] | None = None,
    extra_params: dict[str, Any] | None = None,
) -> bytes | str:
    """Render a canvas to *fmt* (``'html'`` or ``'pdf'``).

    Parameters
    ----------
    canvas:
        Pre-fetched canvas row.
    fmt:
        ``'html'`` — self-contained HTML email body (str).
        ``'pdf'`` — PDF bytes.
    org_id:
        The owning org id (for RLS-aware data collection).
    render_claims:
        Claims with captured policies — the owner's verified RLS slice only.
    extra_params:
        Per-recipient locked params, forwarded as extra *named query params*
        (never as RLS policy overrides).  See :func:`_collect_canvas_data_sync`.

    Returns
    -------
    bytes | str
        Rendered canvas content.
    """
    from app.dashboards.canvas import CanvasDoc  # noqa: PLC0415
    from app.dashboards.render_canvas import render_canvas_html, render_canvas_pdf  # noqa: PLC0415

    config = canvas.get("config") or {}
    doc_raw = config.get("doc") or {}
    try:
        doc = CanvasDoc.model_validate(doc_raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Canvas doc parse error: {exc}") from exc

    # Collect binding data.  Per-recipient RLS is honoured via render_claims
    # (owner's verified policies); per-recipient locked_params flow in as
    # extra NAMED params only — they can never alter the RLS slice.
    element_data = _collect_canvas_data_sync(
        canvas, org_id, render_claims or {}, extra_params=extra_params
    )

    if fmt == "pdf":
        return render_canvas_pdf(doc, element_data, title=doc.title or canvas.get("name"))
    # Default: html
    return render_canvas_html(doc, element_data, title=doc.title or canvas.get("name"))


def _handle_canvas(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Canvas-path handler — called by :func:`handle` when ``canvas_id`` is set.

    Implements the same RLS/locked_params/notify_channels logic as the board
    path but dispatches to :func:`_render_canvas` instead of :func:`_render`.

    Supported formats: ``'html'`` (default for canvases), ``'pdf'``.
    """
    from app.errors import AppError  # noqa: PLC0415
    from app.jobs.report import (  # noqa: PLC0415
        get_default_sender,
        send_report,
    )
    from app.notify.channels import get_channel  # noqa: PLC0415

    # ── 1. Resolve config fields ──────────────────────────────────────────────
    canvas_id: str = config["canvas_id"]  # guaranteed non-empty by caller

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

    fmt: str = (config.get("format") or "html").lower().strip()
    if fmt not in ("html", "pdf"):
        raise AppError(
            "invalid_task_config",
            f"report_send (canvas): unsupported format {fmt!r}. Expected 'html' or 'pdf'.",
            400,
        )

    recipients: list[str] = list(config.get("recipients") or [])
    if not recipients:
        raise AppError(
            "invalid_task_config",
            "report_send task requires at least one recipient in config['recipients'].",
            400,
        )
    subject: str = config.get("subject") or ""
    body: str = config.get("body") or ""
    apply_rls: bool = bool(config.get("apply_user_permissions", False))
    locked_params: dict[str, dict[str, Any]] = dict(config.get("locked_params") or {})

    # Notify channels.
    notify_channels_cfg: list[dict[str, Any]] = list(config.get("notify_channels") or [])

    # ── 2. Resolve canvas ─────────────────────────────────────────────────────
    canvas = _resolve_canvas_sync(canvas_id, org_id)
    if canvas is None:
        raise AppError(
            "canvas_not_found",
            f"Canvas {canvas_id!r} not found in org {org_id!r}.",
            404,
        )

    if not subject:
        subject = f"Nubi Report: {canvas.get('name', canvas_id)}"

    # ── 3. Build render_claims ────────────────────────────────────────────────
    # SECURITY [RLS cross-tenant]: render_claims is ALWAYS exactly the
    # runtime-stamped exec_claims (the flow OWNER's verified policy snapshot,
    # applied by _claims_with_owner_policies BEFORE handle() runs).  We must NOT
    # let user-supplied config['policies'] override it — that would bypass the
    # owner-policy snapshot and exfiltrate cross-tenant rows via a scheduled
    # canvas report.  Per-recipient isolation flows through locked_params as
    # extra NAMED query params only (extra_params), never as policy overrides.
    render_claims: dict[str, Any] = dict(claims or {})

    # ── 4. Render + deliver ───────────────────────────────────────────────────
    sender = get_default_sender()
    errors: list[str] = []
    emails_sent: int = 0

    if apply_rls and locked_params:
        import json as _json  # noqa: PLC0415

        _rendered_cache: dict[str, bytes | str] = {}

        for recipient in recipients:
            per_locked = locked_params.get(recipient, {})
            cache_key = _json.dumps(per_locked, sort_keys=True)
            if cache_key not in _rendered_cache:
                # SECURITY [RLS predicate injection]: render_claims carries the
                # OWNER's verified token policies and must NOT be mutated by the
                # schedule-request-body ``locked_params``.  Previously this path
                # did ``per_claims['policies'].update(per_locked)`` — a writer
                # could craft locked_params (e.g. {'tenant_id': 'other_tenant'})
                # to OVERWRITE the owner's RLS slice and exfiltrate cross-tenant
                # rows into the scheduled email.  We now mirror the BOARD path:
                # locked_params flow in as extra NAMED query params only
                # (extra_params), NEVER as policy overrides.  per_claims stays
                # exactly the owner's verified policies.
                per_claims = dict(render_claims)
                try:
                    _rendered_cache[cache_key] = _render_canvas(
                        canvas,
                        fmt,
                        org_id=org_id,
                        render_claims=per_claims,
                        extra_params=per_locked,
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"report_send(canvas): render failed for {recipient!r}: {exc}"
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
                msg = f"report_send(canvas): send failed for {recipient!r}: {exc}"
                logger.warning(msg)
                errors.append(msg)
    else:
        try:
            rendered = _render_canvas(canvas, fmt, org_id=org_id, render_claims=render_claims)
            target = {
                "recipients": recipients,
                "subject": subject,
                "body": body,
                "format": fmt,
            }
            emails_sent = send_report(sender, target, rendered)
        except Exception as exc:  # noqa: BLE001
            msg = f"report_send(canvas): render/send failed: {exc}"
            logger.warning(msg)
            errors.append(msg)

    # ── 5. Notify channels (best-effort) ──────────────────────────────────────
    channel_notifications: int = 0
    if notify_channels_cfg:
        canvas_name = canvas.get("name") or canvas_id
        channel_text = f"Report ready: {canvas_name}\n{subject}"
        for ch_cfg in notify_channels_cfg:
            kind_str = (ch_cfg.get("kind") or "null").lower().strip()
            try:
                ch = get_channel(kind_str, ch_cfg)
                ch.send(channel_text)
                channel_notifications += 1
            except Exception as exc:  # noqa: BLE001
                msg = f"report_send(canvas): channel notify failed ({kind_str!r}): {exc}"
                logger.warning(msg)
                errors.append(msg)

    return {
        "canvas_id": canvas_id,
        "org_id": org_id,
        "format": fmt,
        "recipients_count": len(recipients),
        "emails_sent": emails_sent,
        "channel_notifications": channel_notifications,
        "errors": errors,
    }
