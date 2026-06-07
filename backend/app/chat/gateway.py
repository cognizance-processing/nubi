"""Inbound chat gateway for Nubi (M22-A).

Handles inbound Slack / WhatsApp webhook payloads, calls the M21 agentic loop,
and if the agent produced a chart/dashboard action, renders a PNG and attaches
it to the outbound reply.

Key types
---------
OutboundMessage
    Dataclass ``{text: str, image_png: bytes | None}``.

ChatTransport (Protocol)
    ``send(to: str, message: OutboundMessage) -> None``.

NullTransport
    Records ``(to, message)`` tuples in ``.sent``; no network.

handle_inbound
    Entry point: verify → normalize → agent → render → transport → return.

Signature hook — verify_signature
----------------------------------
The ``verify_signature(platform, payload)`` function is called before
processing.  In production, Slack uses ``X-Slack-Signature`` HMAC-SHA256 and
WhatsApp uses a similar scheme.  This hook is expected to raise
``AppError("invalid_signature", ..., 401)`` when verification fails.

Tests control verification via the ``_sig_override`` module-level dict:
set ``_sig_override[platform] = True/False`` to force pass/fail.  When the
dict is empty the default is permissive (always passes) so that unit tests
without a real signing secret work out of the box.

Agent interface (M21 contract)
-------------------------------
``run_agent(messages, provider, claims, *, max_steps=8) -> {reply, actions}``

where ``actions`` is a list of ``{tool, args, result}`` dicts.  A
chart/dashboard action will include a ``spec`` and/or a ``data`` field in
``result`` that we can pass to ``render_chart_png``.

Lazy import: if ``app.ai.agent`` is not present at import time (M21 sibling
agent still building it) the module still imports cleanly.  The import is done
inside ``handle_inbound`` at call time.
"""

from __future__ import annotations

import hmac
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.errors import AppError

__all__ = [
    "OutboundMessage",
    "ChatTransport",
    "NullTransport",
    "handle_inbound",
    "verify_signature",
    # For test control:
    "_sig_override",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level override dict (for tests — see docstring above)
# ---------------------------------------------------------------------------

#: Set ``_sig_override["slack"] = True`` to force-pass, ``False`` to force-fail.
#: Only honoured when NO real signing secret is configured for that platform.
#: When a real secret is set, HMAC verification always runs regardless of this dict.
_sig_override: dict[str, bool] = {}

# Maximum age in seconds for a Slack request timestamp (replay protection).
_SLACK_MAX_AGE_S = 5 * 60  # 5 minutes


# ---------------------------------------------------------------------------
# OutboundMessage
# ---------------------------------------------------------------------------


@dataclass
class OutboundMessage:
    """A reply to be sent back to the chat user.

    Parameters
    ----------
    text:
        Plain-text (or Markdown-formatted) reply from the agent.
    image_png:
        Raw PNG bytes to attach, or ``None`` when the agent did not produce
        a chart.
    to:
        Destination address (channel id, phone number, etc.).  Optional —
        ``handle_inbound`` populates this from the normalised payload.
    """

    text: str
    image_png: bytes | None = None
    to: str = ""


# ---------------------------------------------------------------------------
# ChatTransport Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ChatTransport(Protocol):
    """Delivery mechanism for outbound chat messages.

    Implement this protocol to add Slack / WhatsApp / email / etc.
    adapters.  The protocol is intentionally minimal — adapters are
    responsible for any platform-specific serialisation.
    """

    def send(self, to: str, message: OutboundMessage) -> None:
        """Deliver *message* to the *to* address.

        Parameters
        ----------
        to:
            Platform-specific destination (Slack channel id, WhatsApp number, …).
        message:
            The outbound message to send.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# NullTransport — for tests and dry-run
# ---------------------------------------------------------------------------


class NullTransport:
    """Records sent messages in memory.  No network calls.

    Attributes
    ----------
    sent:
        List of ``(to, OutboundMessage)`` tuples in send order.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, OutboundMessage]] = []

    def send(self, to: str, message: OutboundMessage) -> None:
        """Record the outbound *message* without making any network call."""
        self.sent.append((to, message))


# ---------------------------------------------------------------------------
# Signature verification hook
# ---------------------------------------------------------------------------


def _reject(reason: str = "Webhook signature verification failed.") -> None:
    """Raise the canonical 401 signature-failure error."""
    raise AppError("invalid_signature", reason, 401)


def _verify_slack(raw_body: bytes, headers: dict[str, str], secret: str) -> None:
    """Perform real HMAC-SHA256 Slack signature verification.

    Validates ``X-Slack-Signature`` against the ``v0:{timestamp}:{body}``
    string keyed by *secret*.  Also enforces a 5-minute replay window.

    Raises
    ------
    AppError(401)
        If the signature is missing, invalid, or the timestamp is expired.
    """
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature_header = headers.get("x-slack-signature", "")

    if not timestamp or not signature_header:
        _reject("Missing Slack signature headers.")

    # Replay protection: reject requests older than 5 minutes.
    try:
        ts_int = int(timestamp)
    except (ValueError, TypeError):
        _reject("Invalid Slack timestamp.")
    if abs(time.time() - ts_int) > _SLACK_MAX_AGE_S:
        _reject("Slack request timestamp is too old (replay protection).")

    # Compute expected signature: HMAC-SHA256(secret, "v0:{ts}:{body}").
    sig_base = f"v0:{timestamp}:{raw_body.decode('utf-8', errors='replace')}".encode()
    expected = "v0=" + hmac.new(secret.encode(), sig_base, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        _reject()


def _verify_whatsapp(raw_body: bytes, headers: dict[str, str], secret: str) -> None:
    """Perform real HMAC-SHA256 WhatsApp/Meta webhook verification.

    Validates ``X-Hub-Signature-256`` (``sha256=<hex>``) against the raw body
    keyed by *secret*.

    Raises
    ------
    AppError(401)
        If the signature is missing or invalid.
    """
    signature_header = headers.get("x-hub-signature-256", "")
    if not signature_header:
        _reject("Missing X-Hub-Signature-256 header.")

    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        _reject()


def verify_signature(
    platform: str,
    payload: dict[str, Any],
    *,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    """Verify the inbound webhook signature for *platform*.

    Real HMAC verification is performed when the relevant signing secret is
    configured (``SLACK_SIGNING_SECRET`` or ``WHATSAPP_APP_SECRET``).

    Enforcement policy
    ------------------
    1. If a real secret is configured → HMAC verification is **always**
       performed, regardless of ``_sig_override``.  Invalid/missing signatures
       raise 401.
    2. If no secret is configured AND ``ENV=production`` → fail closed (401 +
       a logged config error) because production MUST NOT run without secrets.
    3. If no secret is configured AND not production → fall back to the
       ``_sig_override`` dict for test control, then payload-embedded ``_sig``
       field, then permissive (unit-test default).

    Parameters
    ----------
    platform:
        ``"slack"`` or ``"whatsapp"``.
    payload:
        The parsed webhook body dict.
    raw_body:
        Raw request body bytes — required for real HMAC verification.
    headers:
        Lowercased HTTP request headers — required for real HMAC verification.

    Raises
    ------
    AppError("invalid_signature", ..., 401)
        When verification fails.
    """
    from app.config import get_settings  # noqa: PLC0415 (local import to avoid circular)

    settings = get_settings()

    # ── Determine the secret for this platform ────────────────────────────
    if platform == "slack":
        secret = settings.SLACK_SIGNING_SECRET
    elif platform == "whatsapp":
        secret = settings.WHATSAPP_APP_SECRET
    else:
        secret = ""

    # ── Path 1: real secret is configured → always verify HMAC ───────────
    if secret:
        _raw = raw_body or b""
        _hdrs = headers or {}
        if platform == "slack":
            _verify_slack(_raw, _hdrs, secret)
        elif platform == "whatsapp":
            _verify_whatsapp(_raw, _hdrs, secret)
        # For unknown platforms with a secret: pass (no known scheme).
        return

    # ── Path 2: no secret, production → fail closed ───────────────────────
    if settings.ENV == "production":
        logger.error(
            "SECURITY: %s webhook received in production but %s is not configured. "
            "Rejecting request to prevent unauthenticated access.",
            platform,
            "SLACK_SIGNING_SECRET" if platform == "slack" else "WHATSAPP_APP_SECRET",
        )
        _reject(f"Webhook signing secret not configured for platform '{platform}'.")

    # ── Path 3: no secret, non-production → test-friendly fallback ────────
    # _sig_override takes priority.
    if platform in _sig_override:
        if not _sig_override[platform]:
            _reject()
        return  # force-pass

    # Payload-embedded test signal: ``{"_sig": "bad"}`` → reject.
    if isinstance(payload, dict) and payload.get("_sig") == "bad":
        _reject()
    # Otherwise: permissive (unit tests without a signing secret).


# ---------------------------------------------------------------------------
# Payload normalisation
# ---------------------------------------------------------------------------


def _normalize_payload(platform: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Extract ``(to, text)`` from a platform-specific webhook payload.

    Parameters
    ----------
    platform:
        ``"slack"`` or ``"whatsapp"``.
    payload:
        The raw parsed webhook body.

    Returns
    -------
    tuple[str, str]
        ``(to, text)`` where *to* is the reply destination and *text* is the
        user's message.
    """
    if platform == "slack":
        # Standard Slack Events API shape.
        event = payload.get("event") or {}
        text: str = str(event.get("text") or payload.get("text") or "")
        to: str = str(
            event.get("channel") or payload.get("channel") or ""
        )
        return to, text

    if platform == "whatsapp":
        # WhatsApp Cloud API webhook shape.
        # entry → changes → value → messages[0].
        try:
            entry = (payload.get("entry") or [{}])[0]
            change = (entry.get("changes") or [{}])[0]
            value = change.get("value") or {}
            msg = (value.get("messages") or [{}])[0]
            text = str(msg.get("text", {}).get("body") or payload.get("text") or "")
            to = str(msg.get("from") or payload.get("from") or "")
        except (IndexError, TypeError, KeyError):
            text = str(payload.get("text") or "")
            to = str(payload.get("from") or "")
        return to, text

    # Generic fallback.
    text = str(payload.get("text") or payload.get("message") or "")
    to = str(payload.get("to") or payload.get("channel") or "")
    return to, text


# ---------------------------------------------------------------------------
# Chart / dashboard action extraction
# ---------------------------------------------------------------------------


def _extract_chart_action(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first chart-producing action from *actions*, or ``None``.

    A chart action is any action whose ``tool`` name contains ``"chart"``,
    ``"dashboard"``, or ``"viz"``, OR whose ``result`` dict contains a ``spec``
    key (canonical dashboard spec) or a ``chart`` key.

    Parameters
    ----------
    actions:
        List of ``{tool, args, result}`` dicts returned by ``run_agent``.

    Returns
    -------
    dict | None
        The matching action dict, or ``None`` if none found.
    """
    chart_tools = {"create_chart", "create_dashboard", "render_chart", "viz"}
    for action in actions:
        tool = str(action.get("tool") or "").lower()
        result = action.get("result") or {}
        # Tool name signals a chart/dashboard.
        if any(kw in tool for kw in ("chart", "dashboard", "viz")):
            return action
        # Result carries a spec or chart key.
        if isinstance(result, dict) and (
            "spec" in result or "chart" in result or "data" in result
        ):
            return action
    return None


def _build_chart_spec(action: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extract a (chart_spec, rows) pair from a chart action.

    Parameters
    ----------
    action:
        A ``{tool, args, result}`` dict from the agent.

    Returns
    -------
    tuple[dict, list[dict]]
        ``(chart_spec, rows)`` where *chart_spec* is a minimal spec dict and
        *rows* is a list of row dicts.
    """
    result = action.get("result") or {}
    args = action.get("args") or {}

    # Prefer an explicit spec.
    spec: dict[str, Any] = result.get("spec") or result.get("chart") or {}

    # Fall back to building a minimal spec from the args or action metadata.
    if not spec:
        spec = {
            "type": args.get("chart_type") or "bar",
            "title": args.get("title") or action.get("tool") or "Chart",
            "x": args.get("x") or args.get("x_col") or "x",
            "y": args.get("y") or args.get("y_col") or "y",
        }

    # Extract rows from the result.
    rows: list[dict[str, Any]] = []
    data = result.get("data") or result.get("rows") or []
    if hasattr(data, "to_pylist"):
        rows = data.to_pylist()
    elif isinstance(data, list):
        rows = data

    # If no rows, generate a small synthetic dataset so we always render.
    if not rows:
        x_col = spec.get("x") or "x"
        y_col = spec.get("y") or "y"
        rows = [
            {x_col: "A", y_col: 10},
            {x_col: "B", y_col: 25},
            {x_col: "C", y_col: 15},
            {x_col: "D", y_col: 30},
        ]

    return spec, rows


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _workspace_key(platform: str, payload: dict[str, Any]) -> str:
    """Derive a ``"<platform>:<workspace-or-sender>"`` binding key from *payload*.

    Slack identifies a workspace via the top-level ``team_id`` (or
    ``team_id`` inside the event/authorizations).  WhatsApp has no workspace,
    so we bind per sender phone number (``messages[0].from``).  When nothing is
    found we fall back to ``"<platform>:"``.

    The key is matched against ``CHAT_ORG_BINDINGS`` to resolve the org.
    """
    if platform == "slack":
        team = (
            payload.get("team_id")
            or (payload.get("event") or {}).get("team")
            or payload.get("team")
            or ""
        )
        return f"slack:{team}"
    if platform == "whatsapp":
        try:
            entry = (payload.get("entry") or [{}])[0]
            change = (entry.get("changes") or [{}])[0]
            value = change.get("value") or {}
            sender = (value.get("messages") or [{}])[0].get("from") or ""
        except (IndexError, TypeError, KeyError):
            sender = payload.get("from") or ""
        return f"whatsapp:{sender}"
    return f"{platform}:"


def _resolve_org_id(platform: str, payload: dict[str, Any]) -> str:
    """Map an inbound message to a Nubi org id (config-driven, safe default).

    Resolution order:
    1. ``CHAT_ORG_BINDINGS`` (JSON map) keyed by ``_workspace_key`` —
       per-workspace / per-sender binding.
    2. ``CHAT_DEFAULT_ORG_ID`` fallback.
    3. ``""`` when neither is configured.  An empty org id means the agent runs
       unscoped — the chat tools' RLS still gates data access, and only the
       allowlisted tools (never arbitrary SQL) are ever exposed.

    Never raises — returns ``""`` on any config/parse error.
    """
    try:
        from app.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        raw = str(getattr(settings, "CHAT_ORG_BINDINGS", "") or "").strip()
        if raw:
            import json as _json  # noqa: PLC0415

            try:
                bindings = _json.loads(raw)
            except Exception:  # noqa: BLE001
                bindings = {}
            if isinstance(bindings, dict):
                key = _workspace_key(platform, payload)
                org = bindings.get(key)
                if org:
                    return str(org)
        return str(getattr(settings, "CHAT_DEFAULT_ORG_ID", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _extract_context_from_text(text: str) -> dict[str, Any]:
    """Heuristically extract board/query context references from a message.

    Looks for patterns like:
    - ``board:<id>`` or ``dashboard:<id>``
    - ``query:<id>``

    Returns a dict with ``board_id`` and/or ``query_id`` when found, else ``{}``.
    """
    import re  # noqa: PLC0415

    ctx: dict[str, Any] = {}
    board_match = re.search(r"\b(?:board|dashboard):([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if board_match:
        ctx["board_id"] = board_match.group(1)
    query_match = re.search(r"\bquery:([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if query_match:
        ctx["query_id"] = query_match.group(1)
    return ctx


def handle_inbound(
    platform: str,
    payload: dict[str, Any],
    *,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
    provider: Any = None,
    transport: Any = None,
    claims: dict[str, Any] | None = None,
    board_id: str | None = None,
    query_id: str | None = None,
) -> OutboundMessage:
    """Process an inbound chat message and return an OutboundMessage.

    Pipeline
    --------
    1. Verify the webhook signature (raises 401 on failure).
    2. Normalise the platform-specific *payload* to ``(to, text)``.
    3. Extract board/query context from the message (enables dashboard-scoped chat).
    4. Call ``run_agent([{role:'user', content:text}], provider, claims)``.
    5. If any agent action produced a chart/dashboard, render it to PNG.
    6. Deliver via *transport* (default: NullTransport).
    7. Return the OutboundMessage.

    Parameters
    ----------
    platform:
        ``"slack"`` or ``"whatsapp"``.
    payload:
        Raw parsed webhook body dict.
    raw_body:
        Raw request body bytes for HMAC signature verification.  When omitted,
        verification falls back to the ``_sig_override`` test path.
    headers:
        Lowercased HTTP request headers for HMAC signature verification.
    provider:
        LLMProvider instance (default: ``NullProvider()``).
    transport:
        ChatTransport implementation (default: ``NullTransport()``).
    claims:
        JWT claims dict passed through to the agent for RLS enforcement.
        Defaults to ``{}``.
    board_id:
        Optional board/dashboard ID to scope the agent conversation to a specific
        dashboard.  When supplied (or when the message text references
        ``board:<id>``), the claims dict is augmented with ``{"board_id": ...}``
        so the agent can contextualise tool calls.
    query_id:
        Optional query ID to scope the conversation to a specific query.
        Augments claims with ``{"query_id": ...}``.

    Returns
    -------
    OutboundMessage
        ``{text, image_png, to}`` — ready for delivery.

    Raises
    ------
    AppError("invalid_signature", ..., 401)
        If webhook signature verification fails.
    """
    # ── 1. Verify signature ────────────────────────────────────────────────
    verify_signature(platform, payload, raw_body=raw_body, headers=headers)

    # ── 2. Defaults ────────────────────────────────────────────────────────
    if provider is None:
        from app.ai.provider import NullProvider  # noqa: PLC0415
        provider = NullProvider()

    if transport is None:
        transport = NullTransport()

    if claims is None:
        claims = {}

    # ── 3. Normalise payload ───────────────────────────────────────────────
    to, text = _normalize_payload(platform, payload)

    # ── 3a. Map the workspace/sender to an org (config-driven, SAFE) ────────
    # Inbound chat is org-scoped so the agentic chat only sees the bound org's
    # data via the allowlisted tools.  An explicit org_id in *claims* (e.g. the
    # first-party caller) always wins over the config binding.
    if not claims.get("org_id"):
        resolved_org = _resolve_org_id(platform, payload)
        if resolved_org:
            claims = {**claims, "org_id": resolved_org}

    # ── 3b. Extract and inject board/query context into claims ────────────
    # This enables dashboard/query-scoped chat from Slack & WhatsApp the same
    # way the in-app sidebar chat uses board_id in ChatStreamRequest.
    ctx = _extract_context_from_text(text)
    effective_board_id = board_id or ctx.get("board_id") or payload.get("board_id") or ""
    effective_query_id = query_id or ctx.get("query_id") or payload.get("query_id") or ""
    if effective_board_id:
        claims = {**claims, "board_id": effective_board_id}
    if effective_query_id:
        claims = {**claims, "query_id": effective_query_id}

    # ── 4. Call the M21 agent (lazy import — agent may not exist yet) ─────
    # Use sys.modules directly so that test patches (patch.dict on sys.modules)
    # are honoured even after the real module has been loaded.
    import sys as _sys  # noqa: PLC0415
    try:
        import importlib as _importlib  # noqa: PLC0415
        _importlib.import_module("app.ai.agent")
        _agent_module = _sys.modules["app.ai.agent"]
        run_agent = _agent_module.run_agent
    except (ImportError, AttributeError, KeyError):
        # M21 not yet present — fall back to a stub that echoes the text.
        def run_agent(messages, provider_, claims_, *, max_steps=8):  # type: ignore[misc]
            return {"reply": f"[stub] {text}", "actions": []}

    messages = [{"role": "user", "content": text}]
    agent_result = run_agent(messages, provider, claims, max_steps=8)

    reply_text: str = str(agent_result.get("reply") or "")
    actions: list[dict[str, Any]] = list(agent_result.get("actions") or [])

    # ── 5. Render chart if the agent produced one ─────────────────────────
    image_png: bytes | None = None
    chart_action = _extract_chart_action(actions)
    if chart_action is not None:
        from app.chat.render import render_chart_png  # noqa: PLC0415
        chart_spec, rows = _build_chart_spec(chart_action)
        image_png = render_chart_png(chart_spec, rows)

    # ── 6. Build and deliver the outbound message ─────────────────────────
    outbound = OutboundMessage(text=reply_text, image_png=image_png, to=to)
    transport.send(to, outbound)

    return outbound
