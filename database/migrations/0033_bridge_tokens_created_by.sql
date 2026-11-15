-- 0033_bridge_tokens_created_by.sql
-- A bridge token is held by the person who minted (or last rotated) it, so it
-- has to leave with them. Record that person; tokens minted before this
-- migration have no creator and are unaffected until rotated.

ALTER TABLE bridge_tokens
    ADD COLUMN IF NOT EXISTS created_by uuid NULL REFERENCES users (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_bridge_tokens_created_by ON bridge_tokens (created_by);
