"""Report job implementation — M17-A.

Provides the building blocks for ``kind='report'`` scheduled jobs:

1. ``render_report(board, params, format) -> bytes | str``
   Render a board's widgets to CSV or PDF.

   - ``format='csv'``:  For every widget in the board spec that has a
     ``query_id``, resolve it from the query registry, plan it (via the same
     planner/DuckDBConnector path used by ``_run_query_job``), and concatenate
     the results as one CSV section per widget.  Returns the complete CSV as a
     ``str``.

   - ``format='pdf'``:  A headless-render stub.  Returns placeholder bytes and
     leaves a ``TODO`` note.  Wire a real headless renderer (e.g. Playwright
     ``page.pdf()``) here when the rendering infrastructure is ready.

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
        For ``'pdf'``: placeholder ``bytes`` (stub — see TODO below).

    Raises
    ------
    ValueError
        If *format* is not ``'csv'`` or ``'pdf'``.
    """
    if format == "csv":
        return _render_csv(board, params)
    if format == "pdf":
        return _render_pdf_stub(board, params)
    raise ValueError(f"Unsupported report format: {format!r}. Expected 'csv' or 'pdf'.")


def _render_csv(board: dict[str, Any], params: dict[str, Any]) -> str:
    """Render all widget queries from *board* into a multi-section CSV string."""
    from app.queries.registry import get_query_registry
    from app.connectors import planner
    from app.connectors.duckdb_conn import DuckDBConnector
    from app.errors import AppError

    config: dict[str, Any] = board.get("config") or {}
    spec: dict[str, Any] | None = config.get("spec")

    output = io.StringIO()
    writer_helper = _CsvHelper(output)

    board_name = board.get("name", board.get("id", "report"))

    if spec is None:
        # Legacy HTML board — no widget queries available.
        writer_helper.write_comment(f"Board: {board_name} (no spec — CSV not available)")
        return output.getvalue()

    widgets: list[dict[str, Any]] = spec.get("widgets", [])
    if not widgets:
        writer_helper.write_comment(f"Board: {board_name} — no widgets")
        return output.getvalue()

    registry = get_query_registry()
    connector = DuckDBConnector()

    for widget in widgets:
        widget_id = widget.get("id", "?")
        query_id: str | None = widget.get("query_id")

        writer_helper.write_comment(f"Widget: {widget_id}")

        if not query_id:
            writer_helper.write_comment(f"  (no query_id for widget {widget_id!r})")
            continue

        rq = registry.get(query_id)
        if rq is None:
            writer_helper.write_comment(
                f"  query_id={query_id!r} not found in registry — skipped"
            )
            continue

        # Resolve named params: params override defaults; no token claims here
        # (per-recipient RLS is applied upstream by injecting locked_params).
        # Use the planner's own resolve_named_params if the SQL has {{name}} placeholders,
        # so we stay on the canonical path (NEVER string-concat values into SQL).
        import re as _re
        _PLACEHOLDER_RE = _re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
        sql = rq.sql
        positional_params: list[Any] = []

        if _PLACEHOLDER_RE.search(sql):
            # Build a resolved dict: caller params > declared defaults.
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
                writer_helper.write_comment(
                    f"  missing param {exc} for query_id={query_id!r} — skipped"
                )
                continue

        try:
            physical_plan = planner.plan(sql, params=positional_params if positional_params else None)
            table = connector.execute(physical_plan)
        except (AppError, Exception) as exc:
            writer_helper.write_comment(f"  ERROR executing {query_id!r}: {exc}")
            continue

        # Write Arrow table as CSV rows
        if table.num_rows == 0:
            writer_helper.write_comment(f"  (no rows for query_id={query_id!r})")
            continue

        # Serialise to CSV via stdlib csv module
        csv_writer = csv.writer(output)
        # Header row
        csv_writer.writerow(table.schema.names)
        # Data rows — convert each column to Python objects
        for row_idx in range(table.num_rows):
            row_values = [
                table.column(col_name)[row_idx].as_py()
                for col_name in table.schema.names
            ]
            csv_writer.writerow(row_values)

    return output.getvalue()


def _render_pdf_stub(board: dict[str, Any], params: dict[str, Any]) -> bytes:
    """Placeholder PDF render.

    TODO: replace this stub with a headless renderer (e.g. Playwright
    ``page.pdf()`` or WeasyPrint) once the rendering infrastructure is in
    place.  The pipeline is fully testable with this stub — the PDF bytes
    are returned to the caller and attached to the email exactly as a real
    renderer would produce them.

    Returns
    -------
    bytes
        UTF-8 encoded placeholder text that is NOT a valid PDF, clearly
        labelled so callers can detect the stub path.
    """
    board_name = board.get("name", board.get("id", "report"))
    placeholder = (
        f"[PDF STUB] Board: {board_name}\n"
        "This is a placeholder PDF produced by the stub renderer.\n"
        "TODO: wire a real headless renderer (Playwright/WeasyPrint) here.\n"
    )
    return placeholder.encode("utf-8")


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
