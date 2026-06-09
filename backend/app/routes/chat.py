"""Chat routes for Nubi.

Webhook gateway (M22-A)
-----------------------
POST /chat/slack
    Receive a Slack Events API webhook, process via the chat gateway, and
    reply 200 OK.  Returns 401 if the Slack signature is invalid.

POST /chat/whatsapp
    Receive a WhatsApp Cloud API webhook, process via the chat gateway, and
    reply 200 OK.  Returns 401 if the WhatsApp signature is invalid.

These two endpoints do NOT require a Nubi auth token — they are external
webhook entry points; signature verification is delegated to
``gateway.verify_signature``.

Streaming editor chat (Cursor-like)
-----------------------------------
GET  /chat/models                  → selectable Claude models.
POST /chat/stream                  → Server-Sent Events token/tool stream.
GET  /chat/conversations           → list conversations for the caller's org.
GET  /chat/conversations/{id}      → fetch one conversation with its messages.

These four endpoints are authenticated (first-party Bearer token) and
org-scoped.  See ``app/chat/llm.py`` (streaming + tool use), ``app/chat/tools.py``
(tools), ``app/chat/store.py`` (persistence), and ``app/chat/models.py``
(model list).

Wiring
------
The orchestrator wires this router in ``backend/app/main.py``::

    from app.routes.chat import router
    app.include_router(router, prefix="/api/v1")

Export: ``router``
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.auth.deps import current_user
from app.chat.gateway import handle_inbound
from app.errors import AppError

router = APIRouter(prefix="/chat", tags=["chat"])


async def _process(platform: str, request: Request) -> JSONResponse:
    """Shared handler: verify → handle → respond.

    Reads the raw request body ONCE (before JSON parsing) so that the
    HMAC signature verifier receives the exact bytes that were signed.
    Headers are normalised to lowercase for case-insensitive header lookup.

    Parameters
    ----------
    platform:
        ``"slack"`` or ``"whatsapp"``.
    request:
        The incoming FastAPI request.

    Returns
    -------
    JSONResponse
        ``200 {"ok": true}`` on success.

    Raises
    ------
    HTTPException(401)
        If the webhook signature is invalid.
    """
    # Read the raw body first — this is what was HMAC-signed by the platform.
    raw_body: bytes = await request.body()

    # Normalise headers to lowercase for case-insensitive lookup.
    headers: dict[str, str] = {k.lower(): v for k, v in request.headers.items()}

    try:
        import json as _json  # noqa: PLC0415
        payload: dict[str, Any] = _json.loads(raw_body) if raw_body else {}
    except Exception:
        payload = {}

    try:
        outbound = handle_inbound(platform, payload, raw_body=raw_body, headers=headers)
    except AppError as exc:
        if exc.status == 401:
            raise HTTPException(
                status_code=401,
                detail={"code": exc.code, "message": exc.message},
            )
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        )

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "text": outbound.text,
            "has_image": outbound.image_png is not None,
        },
    )


@router.post("/slack")
async def slack_webhook(request: Request) -> JSONResponse:
    """Receive a Slack Events API webhook.

    Verifies the Slack request signature (HMAC-SHA256 over the raw body using
    the ``SLACK_SIGNING_SECRET`` env var; permissive in tests when the secret
    is absent).

    Returns 200 on success, 401 if the signature is invalid.
    """
    return await _process("slack", request)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request) -> JSONResponse:
    """Receive a WhatsApp Cloud API webhook.

    Verifies the WhatsApp signature (HMAC-SHA256 ``X-Hub-Signature-256`` header
    using the ``WHATSAPP_APP_SECRET`` env var; permissive in tests when absent).

    Returns 200 on success, 401 if the signature is invalid.
    """
    return await _process("whatsapp", request)


# ---------------------------------------------------------------------------
# Streaming editor chat (authenticated + org-scoped)
# ---------------------------------------------------------------------------


async def _resolve_org_id(user: dict[str, Any]) -> str:
    """Resolve the caller's org_id (reuses the resources router's helper)."""
    from app.repos.provider import get_repo  # noqa: PLC0415
    from app.routes.resources import get_user_org  # noqa: PLC0415

    return await get_user_org(str(user["id"]), get_repo())


class ChatStreamRequest(BaseModel):
    """Request body for POST /chat/stream."""

    chat_id: str | None = None
    board_id: str | None = None
    model: str
    message: str


@router.get("/models")
async def chat_models(_user: dict[str, Any] = Depends(current_user)) -> list[dict[str, str]]:
    """Return the selectable Claude models — ``[{id, label}]``."""
    from app.chat.models import list_models  # noqa: PLC0415

    return list_models()


@router.post("/stream")
async def chat_stream(
    body: ChatStreamRequest,
    user: dict[str, Any] = Depends(current_user),
) -> StreamingResponse:
    """Stream an assistant turn as Server-Sent Events.

    Persists the user message and the full assistant turn (text + tool calls +
    any proposed dashboard spec).  Creates the chat row when ``chat_id`` is
    absent, deriving the title from the first user message.

    SSE ``data:`` payloads (one JSON object per line) carry a ``type`` field:
    ``token`` ``{text}``, ``tool_use`` ``{id, name, input}``, ``tool_result``
    ``{id, output}``, ``message`` (terminal ``{chat_id, message_id, spec?}``),
    ``error`` ``{message}``.
    """
    from starlette.concurrency import iterate_in_threadpool  # noqa: PLC0415

    from app.chat import store  # noqa: PLC0415
    from app.chat.llm import stream_chat  # noqa: PLC0415
    from app.chat.models import resolve_model  # noqa: PLC0415

    org_id = await _resolve_org_id(user)
    user_id = str(user["id"])
    model = resolve_model(body.model)
    message = (body.message or "").strip()

    if not message:
        raise AppError("validation_error", "message must not be empty.", 400)

    # ── BILLING: AI calls are metered (tiers.max_ai_calls_per_month) ─────────
    # Quota enforcement is a no-op in OSS builds (no EE checker registered).
    # The call is recorded up-front: a streamed turn consumes the call when
    # dispatched even if the client abandons the stream mid-flight.
    from app.compute.metering import record_usage  # noqa: PLC0415
    from app.features import enforce_quota  # noqa: PLC0415

    await enforce_quota(org_id, "ai_calls", amount=1.0)
    await record_usage(
        kind="ai_call", user_id=user_id, org_id=org_id, units=1.0, tier="chat_stream"
    )

    # Resolve / create the chat row (org-scoped).
    chat_id = body.chat_id
    board_id = body.board_id
    if chat_id:
        chat = await store.get_chat(org_id, chat_id)
        if chat is None:
            raise AppError("chat_not_found", f"No chat with id {chat_id!r}.", 404)
        board_id = chat.get("board_id") or board_id
        history = await store.load_history(chat_id)
    else:
        chat_id = await store.create_chat(org_id, user_id, board_id, message)
        history = []

    # Persist the user message and append it to the model history.
    await store.add_message(chat_id, "user", {"text": message})
    history.append({"role": "user", "content": message})

    def _sse(obj: dict[str, Any]) -> str:
        return "data: " + json.dumps(obj) + "\n\n"

    def _sync_events():
        last_turn = None
        errored = False
        for event, turn in stream_chat(history, model):
            last_turn = turn
            if event.get("type") == "error":
                errored = True
            yield _sse(event)

        # Persist the assistant turn + emit the terminal message event.
        try:
            assistant_content: dict[str, Any] = {
                "text": last_turn.text if last_turn else "",
                "tool_calls": last_turn.tool_calls if last_turn else [],
            }
            if last_turn and last_turn.spec is not None:
                assistant_content["spec"] = last_turn.spec

            import anyio  # noqa: PLC0415

            message_id = anyio.from_thread.run(
                store.add_message, chat_id, "assistant", assistant_content
            )
            anyio.from_thread.run(store.touch_chat, chat_id)

            final: dict[str, Any] = {
                "type": "message",
                "chat_id": chat_id,
                "message_id": message_id,
            }
            if last_turn and last_turn.spec is not None:
                final["spec"] = last_turn.spec
            yield _sse(final)
        except Exception as exc:  # noqa: BLE001
            if not errored:
                yield _sse({"type": "error", "message": f"persist_failed: {exc}"})

    async def _event_stream():
        async for chunk in iterate_in_threadpool(_sync_events()):
            yield chunk

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations")
async def chat_conversations(
    board_id: str | None = None,
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    """List conversations for the caller's org — ``[{id, title, updated_at}]``."""
    from app.chat import store  # noqa: PLC0415

    org_id = await _resolve_org_id(user)
    return await store.list_conversations(org_id, board_id)


@router.get("/conversations/{chat_id}")
async def chat_conversation(
    chat_id: str,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Fetch one conversation — ``{id, title, messages: [{role, content, created_at}]}``."""
    from app.chat import store  # noqa: PLC0415

    org_id = await _resolve_org_id(user)
    convo = await store.get_conversation(org_id, chat_id)
    if convo is None:
        raise AppError("chat_not_found", f"No chat with id {chat_id!r}.", 404)
    return convo


# Register on the shared api_router at import time (mirrors the other route
# modules) so `import app.routes.chat` in main.py mounts these endpoints.
from app.routes import api_router  # noqa: E402

api_router.include_router(router)
