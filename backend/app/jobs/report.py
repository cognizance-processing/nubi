"""Report job implementation — M17-A.

Provides the building blocks for ``kind='report'`` scheduled jobs:

1. ``render_report(board, params, format) -> bytes | str``
   Render a board's widgets to CSV or PDF.

   - ``format='csv'``:  For every widget in the board spec that has a
     ``query_id``, resolve it from the query registry, plan it (via the same
     planner/DuckDBConnector path used by ``_run_query_job``), and concatenate
     the results as one CSV section per widget.  Returns the complete CSV as a
     ``str``.

   - ``format='pdf'``:  Renders a real, dependency-free A4 PDF (via
     ``app.pdf``) — a branded header plus one compact data table per widget.

2. ``EmailSender`` — provider interface (Protocol).

   ``NullSender`` — records calls in memory with no network I/O; used as the
   default in tests and when no SMTP/SES config is present.

   ``send_report(sender, target, rendered)`` — sends once per recipient in
   ``target["recipients"]``.

3. Per-recipient RLS (``apply_user_permissions``):

   When ``target["apply_user_permissions"]`` is ``True`` the report is
   rendered once per recipient with locked params injected for that recipient.

   The implementation uses ``target["locked_params"]`` if present — a mapping
   ``{recipient_email: {param_name: value}}`` supplied by the job author.
   This is the "inject via existing positional/claims path" approach.

   TODO: when M13 named-param RLS resolver lands, replace the locked_params
   dict approach with a proper ``claims/policies`` injection so the resolver
   precedence is honoured:
       ``token/RLS claims (locked) > body.params > default``

Boards
------
A board is a resource returned by ``repo.get("boards", org_id, board_id)``.
Its ``config`` dict may contain a ``spec`` key that is a canonical
``DashboardSpec`` dict (from ``app.dashboards.spec``).  When ``spec`` is
absent (legacy HTML boards) the CSV render falls back to an empty result and
records a warning in the message.

The board is resolved via ``repo.get("boards", org_id, board_id)``.  The
caller (executor) must pass in a ``repo`` when applicable; the route layer
uses the active repo from ``app.repos.provider.get_repo()``.
"""

from __future__ import annotations

import csv
import io
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# EmailSender protocol + NullSender
# ---------------------------------------------------------------------------


@runtime_checkable
class EmailSender(Protocol):
    """Minimal interface for sending report emails.

    Implementations must be synchronous (the executor is sync).  For async
    transports (e.g. SES via aiobotocore) wrap in a sync helper.
    """

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        attachment_name: str,
        attachment_data: bytes | str,
    ) -> None:
        """Send one email to *to* with the report as an attachment.

        Parameters
        ----------
        to:
            Recipient email address.
        subject:
            Email subject line.
        body:
            Plain-text body of the email.
        attachment_name:
            Filename for the attachment (e.g. ``"report.csv"``).
        attachment_data:
            The attachment bytes/str (CSV text or PDF bytes).
        """
        ...


class NullSender:
    """No-op email sender for tests and development.

    Captures all send calls in ``self.sent`` — a list of dicts, one per
    call.  No network I/O; no external dependencies.

    Usage in tests::

        sender = NullSender()
        send_report(sender, target, rendered)
        assert len(sender.sent) == len(target["recipients"])
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        attachment_name: str,
        attachment_data: bytes | str,
    ) -> None:
        """Record the send in ``self.sent`` (no actual sending)."""
        self.sent.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "attachment_name": attachment_name,
                "attachment_data": attachment_data,
            }
        )


class SmtpEmailSender:
    """Synchronous SMTP sender implementing the :class:`EmailSender` protocol.

    Used for both scheduled-report emails and billing invoice emails when
    ``settings.SMTP_HOST`` is configured.  Stdlib ``smtplib`` only — no
    external dependencies.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        from_addr: str = "",
        from_name: str = "",
        timeout: float = 20.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.from_addr = from_addr
        self.from_name = from_name
        self.timeout = timeout

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        attachment_name: str,
        attachment_data: bytes | str,
    ) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr((self.from_name or self.from_addr, self.from_addr))
        msg["To"] = to
        msg.set_content(body)

        data = attachment_data.encode("utf-8") if isinstance(attachment_data, str) else attachment_data
        maintype, subtype = ("application", "pdf") if attachment_name.endswith(".pdf") else ("application", "octet-stream")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=attachment_name)

        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout) as srv:
                self._auth_and_send(srv, msg)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as srv:
                if self.use_tls:
                    srv.starttls()
                self._auth_and_send(srv, msg)

    def _auth_and_send(self, srv: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.username:
            srv.login(self.username, self.password)
        srv.send_message(msg)


def get_default_sender(settings: Any | None = None) -> EmailSender:
    """Return the configured email transport.

    ``SmtpEmailSender`` when ``SMTP_HOST`` is set, otherwise a ``NullSender``
    (no real delivery — reports/invoices are still generated and recorded, so
    OSS builds and tests with no mail server keep working).
    """
    if settings is None:
        from app.config import get_settings  # noqa: PLC0415

        settings = get_settings()

    host = (getattr(settings, "SMTP_HOST", "") or "").strip()
    if not host:
        return NullSender()

    from_addr = (
        (getattr(settings, "SMTP_FROM", "") or "").strip()
        or (getattr(settings, "BILLING_EMAIL", "") or "").strip()
        or (getattr(settings, "COMPANY_EMAIL", "") or "").strip()
    )
    return SmtpEmailSender(
        host=host,
        port=int(getattr(settings, "SMTP_PORT", 587) or 587),
        username=(getattr(settings, "SMTP_USERNAME", "") or "").strip(),
        password=getattr(settings, "SMTP_PASSWORD", "") or "",
        use_tls=bool(getattr(settings, "SMTP_USE_TLS", True)),
        from_addr=from_addr,
        from_name=(getattr(settings, "COMPANY_NAME", "") or "Nubi").strip(),
    )


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------


def render_report(
    board: dict[str, Any],
    params: dict[str, Any],
    format: str,  # 'csv' | 'pdf'
) -> bytes | str:
    """Render *board* to the requested *format*.

    Parameters
    ----------
    board:
        A board resource dict (as returned by the repo).  Must have at least
        ``{"id": ..., "name": ..., "config": {...}}``.  The ``config`` should
        contain a ``spec`` key with the canonical DashboardSpec dict.
    params:
        Named param overrides (applied on top of query defaults).  May be
        ``{}``.
    format:
        ``'csv'`` or ``'pdf'``.

    Returns
    -------
    bytes | str
        For ``'csv'``: a UTF-8 ``str`` containing the full CSV (one section
        per widget with a ``# Widget: <id>`` header comment).
        For ``'pdf'``: a valid ``%PDF-1.4`` document as ``bytes``.

    Raises
    ------
    ValueError
        If *format* is not ``'csv'`` or ``'pdf'``.
    """
    if format == "csv":
        return _render_csv(board, params)
    if format == "pdf":
        return _render_pdf(board, params)
    raise ValueError(f"Unsupported report format: {format!r}. Expected 'csv' or 'pdf'.")


def _iter_widget_tables(board: dict[str, Any], params: dict[str, Any]):
    """Yield ``(widget_id, table, note)`` for each widget in *board*'s spec.

    ``table`` is a pyarrow Table when the widget's query executed and returned
    rows, else ``None`` with ``note`` carrying a human-readable reason (no
    query_id / not in registry / missing param / execution error / no rows).
    Shared by the CSV and PDF renderers so both walk the spec identically and
    stay on the canonical planner path (named params are NEVER string-concat'd
    into SQL).
    """
    from app.queries.registry import get_query_registry
    from app.connectors import planner
    from app.connectors.duckdb_conn import DuckDBConnector
    from app.errors import AppError
    import re as _re

    config: dict[str, Any] = board.get("config") or {}
    spec: dict[str, Any] | None = config.get("spec")
    if spec is None:
        return
    widgets: list[dict[str, Any]] = spec.get("widgets", [])
    if not widgets:
        return

    registry = get_query_registry()
    connector = DuckDBConnector()
    placeholder_re = _re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")

    for widget in widgets:
        widget_id = widget.get("id", "?")
        query_id: str | None = widget.get("query_id")

        if not query_id:
            yield widget_id, None, f"(no query_id for widget {widget_id!r})"
            continue

        rq = registry.get(query_id)
        if rq is None:
            yield widget_id, None, f"query_id={query_id!r} not found in registry — skipped"
            continue

        sql = rq.sql
        positional_params: list[Any] = []
        if placeholder_re.search(sql):
            resolved: dict[str, Any] = {}
            if rq.params:
                for p in rq.params:
                    if p.default is not None:
                        resolved[p.name] = p.default
            resolved.update(params)  # caller overrides defaults
            try:
                from app.connectors.planner import resolve_named_params
                sql, positional_params = resolve_named_params(sql, resolved)
            except KeyError as exc:
                yield widget_id, None, f"missing param {exc} for query_id={query_id!r} — skipped"
                continue

        try:
            physical_plan = planner.plan(sql, params=positional_params if positional_params else None)
            table = connector.execute(physical_plan)
        except (AppError, Exception) as exc:
            yield widget_id, None, f"ERROR executing {query_id!r}: {exc}"
            continue

        if table.num_rows == 0:
            yield widget_id, None, f"(no rows for query_id={query_id!r})"
            continue

        yield widget_id, table, None


def _render_csv(board: dict[str, Any], params: dict[str, Any]) -> str:
    """Render all widget queries from *board* into a multi-section CSV string."""
    output = io.StringIO()
    writer_helper = _CsvHelper(output)
    board_name = board.get("name", board.get("id", "report"))

    config: dict[str, Any] = board.get("config") or {}
    spec = config.get("spec")
    if spec is None:
        writer_helper.write_comment(f"Board: {board_name} (no spec — CSV not available)")
        return output.getvalue()
    if not (spec.get("widgets") or []):
        writer_helper.write_comment(f"Board: {board_name} — no widgets")
        return output.getvalue()

    for widget_id, table, note in _iter_widget_tables(board, params):
        writer_helper.write_comment(f"Widget: {widget_id}")
        if table is None:
            writer_helper.write_comment(f"  {note}")
            continue
        csv_writer = csv.writer(output)
        csv_writer.writerow(table.schema.names)
        for row_idx in range(table.num_rows):
            csv_writer.writerow([
                table.column(col_name)[row_idx].as_py()
                for col_name in table.schema.names
            ])

    return output.getvalue()


def _render_pdf(board: dict[str, Any], params: dict[str, Any]) -> bytes:
    """Render *board* to a real, dependency-free A4 PDF (one table per widget).

    Produces a valid ``%PDF-1.4`` document with a branded header band (board
    name + generated timestamp) and, for each widget query, a compact data
    table (header row + sampled rows, zebra-striped). Paginates automatically.
    """
    from datetime import datetime, timezone
    from app.pdf import (
        Pdf, text_width, truncate,
        PAGE_W, PAGE_H, MARGIN, CONTENT_W,
        NAVY, TEAL, INK, MUTED, HAIR, ZEBRA,
    )

    MAX_ROWS_PER_WIDGET = 30  # keep report PDFs bounded; CSV export has the full data
    board_name = str(board.get("name", board.get("id", "report")))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pdf = Pdf()

    def header_band() -> float:
        """Draw the top band; return the y baseline to start content below it."""
        pdf.rect_fill(0, PAGE_H - 92, PAGE_W, 92, NAVY)
        pdf.text(MARGIN, PAGE_H - 50, 20, board_name, bold=True, color=(1, 1, 1))
        pdf.text(MARGIN, PAGE_H - 72, 10.5, f"Scheduled report  ·  generated {generated}",
                 color=(0.78, 0.82, 0.88))
        return PAGE_H - 122

    y = header_band()

    def ensure_space(needed: float) -> None:
        nonlocal y
        if y - needed < MARGIN:
            pdf.new_page()
            y = PAGE_H - MARGIN

    config: dict[str, Any] = board.get("config") or {}
    spec = config.get("spec")
    if spec is None or not (spec.get("widgets") or []):
        msg = "No spec — nothing to render." if spec is None else "No widgets on this board."
        pdf.text(MARGIN, y, 11, msg, color=MUTED)
        return pdf.to_bytes()

    for widget_id, table, note in _iter_widget_tables(board, params):
        ensure_space(40)
        pdf.text(MARGIN, y, 12.5, f"Widget: {widget_id}", bold=True, color=NAVY)
        y -= 16
        if table is None:
            pdf.text(MARGIN + 8, y, 10, str(note), color=MUTED)
            y -= 22
            continue

        cols = list(table.schema.names)
        n_cols = max(1, len(cols))
        col_w = CONTENT_W / n_cols
        row_h = 16.0

        def draw_row(values, *, head: bool, idx: int = 0) -> None:
            nonlocal y
            ensure_space(row_h)
            if head:
                pdf.rect_fill(MARGIN, y - 4, CONTENT_W, row_h, TEAL)
            elif idx % 2 == 1:
                pdf.rect_fill(MARGIN, y - 4, CONTENT_W, row_h, ZEBRA)
            for ci, val in enumerate(values):
                cell = "" if val is None else str(val)
                cell = truncate(cell, col_w - 10, 9)
                pdf.text(MARGIN + ci * col_w + 5, y, 9, cell,
                         bold=head, color=((1, 1, 1) if head else INK))
            y -= row_h

        draw_row(cols, head=True)
        shown = min(table.num_rows, MAX_ROWS_PER_WIDGET)
        for r in range(shown):
            draw_row([table.column(c)[r].as_py() for c in cols], head=False, idx=r)
        if table.num_rows > shown:
            ensure_space(16)
            pdf.text(MARGIN + 5, y, 8.5,
                     f"... {table.num_rows - shown} more rows (full data in the CSV export)",
                     color=MUTED)
            y -= 14
        pdf.line(MARGIN, y + 4, PAGE_W - MARGIN, y + 4, color=HAIR)
        y -= 18

    return pdf.to_bytes()


class _CsvHelper:
    """Thin helper for writing comment lines to a StringIO CSV stream."""

    def __init__(self, stream: io.StringIO) -> None:
        self._stream = stream

    def write_comment(self, text: str) -> None:
        self._stream.write(f"# {text}\n")


# ---------------------------------------------------------------------------
# send_report
# ---------------------------------------------------------------------------


def send_report(
    sender: EmailSender,
    target: dict[str, Any],
    rendered: bytes | str,
) -> int:
    """Send the rendered report to all recipients in *target*.

    Per-recipient RLS
    -----------------
    When ``target["apply_user_permissions"]`` is ``True`` this function
    expects that *rendered* was already produced with the correct per-recipient
    locked params injected (the executor calls ``render_report`` once per
    recipient with the locked params applied).  This function then sends the
    per-recipient result to that one recipient.

    When ``apply_user_permissions`` is ``False`` (or absent) *rendered* is the
    same for all recipients and is sent as-is.

    Parameters
    ----------
    sender:
        Any :class:`EmailSender`-compatible object.
    target:
        The job's ``target`` dict.  Expected keys:
        ``recipients`` (list[str]), ``subject`` (str), ``body`` (str),
        ``format`` (``'csv'``|``'pdf'``).
    rendered:
        The already-rendered report bytes/str.

    Returns
    -------
    int
        The number of emails sent.
    """
    recipients: list[str] = target.get("recipients", [])
    subject: str = target.get("subject", "Nubi Report")
    body: str = target.get("body", "")
    fmt: str = target.get("format", "csv")
    attachment_name = f"report.{fmt}"

    for recipient in recipients:
        sender.send(
            to=recipient,
            subject=subject,
            body=body,
            attachment_name=attachment_name,
            attachment_data=rendered,
        )

    return len(recipients)


# ---------------------------------------------------------------------------
# resolve_board — repo helper
# ---------------------------------------------------------------------------


async def resolve_board_async(
    board_id: str,
    org_id: str,
) -> dict[str, Any]:
    """Resolve a board from the active repo.

    Used by the route layer when the report job fires via ``POST /jobs/{id}/run``.
    The executor path uses ``resolve_board_sync`` (below) so it can stay
    synchronous.

    Raises
    ------
    AppError("board_not_found", 404)
        If the board does not exist or belongs to a different org.
    """
    from app.repos.provider import get_repo
    from app.errors import AppError

    repo = get_repo()
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError(
            "board_not_found",
            f"Board {board_id!r} not found in org {org_id!r}.",
            404,
        )
    return board


def resolve_board_sync(
    board_id: str,
    org_id: str,
) -> dict[str, Any] | None:
    """Synchronous board lookup for use inside the synchronous executor.

    Because the executor (``execute_job``) is synchronous but the repo is
    async, this function uses ``asyncio.run()`` in a subprocess-safe way.
    If no event loop is running it creates a temporary one; if a loop is
    already running (e.g. FastAPI) it falls back to a thread executor.

    Returns ``None`` if the board is not found (the executor converts this
    to an error run).
    """
    import asyncio

    from app.repos.provider import get_repo
    from app.errors import AppError

    async def _fetch() -> dict[str, Any] | None:
        repo = get_repo()
        return await repo.get("boards", org_id, board_id)

    # Try to run in the existing event loop (sync tests use asyncio.run already
    # via pytest-asyncio) or create a fresh one.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context (FastAPI lifespan / test).
            # Use a ThreadPoolExecutor to run the coroutine without nesting loops.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _fetch())
                return future.result()
        else:
            return loop.run_until_complete(_fetch())
    except RuntimeError:
        return asyncio.run(_fetch())


# ---------------------------------------------------------------------------
# _inject_locked_params — per-recipient RLS helper
# ---------------------------------------------------------------------------


def inject_locked_params(
    params: dict[str, Any],
    locked: dict[str, Any],
) -> dict[str, Any]:
    """Return a new params dict with *locked* values overriding *params*.

    Locked params come from the job target's ``locked_params`` mapping for a
    specific recipient.  They take priority over any caller-supplied params
    (mirroring the token/RLS claim precedence: locked > body.params > default).

    TODO: when M13 named-param RLS resolver lands, replace this with a proper
    call to the named-param resolver so the full precedence chain is honoured
    and unknown param names are rejected.

    Parameters
    ----------
    params:
        The base named params dict from ``target["params"]``.
    locked:
        Per-recipient locked param overrides (e.g. ``{"tenant_id": "acme"}``).

    Returns
    -------
    dict
        Merged params with locked values taking precedence.
    """
    merged = dict(params)
    merged.update(locked)
    return merged
