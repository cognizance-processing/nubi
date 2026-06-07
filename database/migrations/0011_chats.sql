-- 0011_chats.sql
-- Streaming chat (Cursor-like) for the dashboard editor.
--
-- Two tables:
--   chats          — one row per conversation, org- and user-scoped, optionally
--                    bound to a board (dashboard) being edited.
--   chat_messages  — the full turn-by-turn transcript.  `content` is JSONB so an
--                    assistant turn can store text plus the tool calls/results it
--                    made (and any proposed dashboard spec) in one row.
--
-- Forward-only; never edit after applying.

-- 0006_domain_stubs.sql created a minimal placeholder `chats` table (name/config/
-- created_by) that predates this feature and lacks board_id/title/user_id.  The
-- application (app/chat/store.py) targets the schema below, so replace the stub.
-- Safe: the stub never carried real data (chat persistence ships with this table).
DROP TABLE IF EXISTS chats CASCADE;

CREATE TABLE IF NOT EXISTS chats (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs(id)  ON DELETE CASCADE,
    user_id     uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    board_id    text,
    title       text        NOT NULL DEFAULT 'New chat',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chats_org_id_idx          ON chats (org_id);
CREATE INDEX IF NOT EXISTS chats_org_board_idx       ON chats (org_id, board_id);
CREATE INDEX IF NOT EXISTS chats_org_updated_at_idx  ON chats (org_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id     uuid        NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role        text        NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_chat_id_idx ON chat_messages (chat_id, created_at);
