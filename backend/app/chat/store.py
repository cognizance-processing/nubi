"""Persistence for streaming chat conversations (chats + chat_messages).

Uses the ``app.db`` async helpers directly (the same pattern as the orgs router),
since chats live in dedicated tables rather than the generic resource repo.

The canonical schema ships in ``database/migrations/0011_chats.sql``.  As a
best-effort safety net for environments where the migration runner has not run
(mirroring how other subsystems self-heal), :func:`ensure_tables` creates the
tables idempotently on first use.  It never raises — if the DB is unavailable
the caller's own query will surface the error.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.db import execute, fetch, fetchrow

# Guard so the idempotent DDL runs at most once per process.
_tables_ready = False

_DDL = """
CREATE TABLE IF NOT EXISTS chats (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL,
    user_id     uuid        NOT NULL,
    board_id    text,
    title       text        NOT NULL DEFAULT 'New chat',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chats_org_id_idx         ON chats (org_id);
CREATE INDEX IF NOT EXISTS chats_org_board_idx      ON chats (org_id, board_id);
CREATE INDEX IF NOT EXISTS chats_org_updated_at_idx ON chats (org_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id     uuid        NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role        text        NOT NULL,
    content     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_chat_id_idx ON chat_messages (chat_id, created_at);
"""


async def ensure_tables() -> None:
    """Best-effort idempotent creation of the chat tables.  Never raises."""
    global _tables_ready
    if _tables_ready:
        return
    try:
        await execute(_DDL)
    except Exception:  # noqa: BLE001
        # Migration runner may own the schema, or the statement may not be
        # supported in a single call on some drivers — don't block the request.
        pass
    _tables_ready = True


def _title_from_message(message: str) -> str:
    """Derive a short chat title from the first user message."""
    clean = " ".join((message or "").split()).strip()
    if not clean:
        return "New chat"
    return clean[:60] + ("…" if len(clean) > 60 else "")


async def create_chat(
    org_id: str,
    user_id: str,
    board_id: str | None,
    first_message: str,
) -> str:
    """Insert a new chat row and return its id."""
    await ensure_tables()
    chat_id = str(uuid.uuid4())
    title = _title_from_message(first_message)
    await execute(
        """
        INSERT INTO chats (id, org_id, user_id, board_id, title)
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5)
        """,
        chat_id,
        org_id,
        user_id,
        board_id,
        title,
    )
    return chat_id


async def get_chat(org_id: str, chat_id: str) -> dict[str, Any] | None:
    """Return the chat row (org-scoped) or None."""
    await ensure_tables()
    row = await fetchrow(
        """
        SELECT id, org_id, user_id, board_id, title, created_at, updated_at
        FROM chats
        WHERE id = $1::uuid AND org_id = $2::uuid
        """,
        chat_id,
        org_id,
    )
    return dict(row) if row is not None else None


async def touch_chat(chat_id: str) -> None:
    """Bump the chat's updated_at to now()."""
    await execute("UPDATE chats SET updated_at = now() WHERE id = $1::uuid", chat_id)


async def add_message(chat_id: str, role: str, content: dict[str, Any]) -> str:
    """Append a message to a chat.  *content* is stored as JSONB.  Returns its id."""
    await ensure_tables()
    message_id = str(uuid.uuid4())
    await execute(
        """
        INSERT INTO chat_messages (id, chat_id, role, content)
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb)
        """,
        message_id,
        chat_id,
        role,
        json.dumps(content),
    )
    return message_id


async def list_conversations(org_id: str, board_id: str | None) -> list[dict[str, Any]]:
    """List conversations for an org (optionally filtered by board), newest first."""
    await ensure_tables()
    if board_id:
        rows = await fetch(
            """
            SELECT id, title, updated_at
            FROM chats
            WHERE org_id = $1::uuid AND board_id = $2
            ORDER BY updated_at DESC
            """,
            org_id,
            board_id,
        )
    else:
        rows = await fetch(
            """
            SELECT id, title, updated_at
            FROM chats
            WHERE org_id = $1::uuid
            ORDER BY updated_at DESC
            """,
            org_id,
        )
    return [
        {"id": str(r["id"]), "title": r["title"], "updated_at": r["updated_at"].isoformat()}
        for r in rows
    ]


async def get_conversation(org_id: str, chat_id: str) -> dict[str, Any] | None:
    """Return the full conversation (chat + ordered messages), org-scoped, or None."""
    chat = await get_chat(org_id, chat_id)
    if chat is None:
        return None
    rows = await fetch(
        """
        SELECT role, content, created_at
        FROM chat_messages
        WHERE chat_id = $1::uuid
        ORDER BY created_at ASC, id ASC
        """,
        chat_id,
    )
    messages = []
    for r in rows:
        content = r["content"]
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:  # noqa: BLE001
                content = {"text": content}
        messages.append(
            {
                "role": r["role"],
                "content": content,
                "created_at": r["created_at"].isoformat(),
            }
        )
    return {
        "id": str(chat["id"]),
        "title": chat["title"],
        "messages": messages,
    }


async def load_history(chat_id: str) -> list[dict[str, Any]]:
    """Load prior messages as Anthropic-format ``{role, content}`` entries.

    Only ``user`` and ``assistant`` roles with text are replayed (tool-call
    bookkeeping from prior turns is not re-sent to the model — the assistant text
    is sufficient context for follow-ups).
    """
    rows = await fetch(
        """
        SELECT role, content
        FROM chat_messages
        WHERE chat_id = $1::uuid AND role IN ('user', 'assistant')
        ORDER BY created_at ASC, id ASC
        """,
        chat_id,
    )
    history: list[dict[str, Any]] = []
    for r in rows:
        content = r["content"]
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:  # noqa: BLE001
                content = {"text": content}
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        if text:
            history.append({"role": r["role"], "content": text})
    return history


__all__ = [
    "ensure_tables",
    "create_chat",
    "get_chat",
    "touch_chat",
    "add_message",
    "list_conversations",
    "get_conversation",
    "load_history",
]
