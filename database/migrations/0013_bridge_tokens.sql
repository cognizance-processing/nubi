-- 0013_bridge_tokens.sql
-- Bridge tokens — control-channel credentials for the reverse-tunnel agent (§7).
--
-- A bridge agent (``nubi bridge start --token nubi_br_…``) presents this token
-- on every tunnel handshake/heartbeat. It mirrors the proven API-key pattern
-- (``0010_api_keys.sql``) but is SCOPED TO A BRIDGE IDENTITY, not a user: a
-- token is bound to (org_id, bridge_id) at mint time, so it can only ever
-- authenticate that one bridge in that one org.
--
-- SECURITY: the raw token is shown to the caller EXACTLY ONCE at mint time and
-- is NEVER stored. Only its SHA-256 hex digest (``token_hash``) is persisted,
-- the same one-way storage discipline as ``api_keys`` / ``sessions``. The token
-- authenticates the CONTROL CHANNEL ONLY — by itself it reads no secrets and no
-- storage; read access to staged data is granted separately and ephemerally
-- (write-only, prefix-pinned, short-TTL grants — see app/lakehouse/grants.py).
--
-- The opaque token format is ``nubi_br_<43-char-base64url>`` (see
-- app/auth/bridge_tokens.py). The ``nubi_br_`` prefix distinguishes a bridge
-- token from a user API key (``nubi_ak_``) without a decode attempt.
--
-- ROTATION: ``rotate`` mints a new token and sets ``grace_until`` on the OLD one
-- (now + grace window). While ``grace_until`` is in the future BOTH tokens
-- validate, so a live agent can swap its token without a tunnel drop; once the
-- window elapses the old token stops validating. REVOKE sets ``revoked_at`` and
-- the broker drops the live tunnel on the next handshake/heartbeat.
--
-- NOTE: this table is ADDITIVE. The pre-existing bridge tunnel auth
-- (``bridges.config["token"]`` plaintext compare) keeps working; this is the
-- hashed, rotatable replacement the agent uses going forward.

CREATE TABLE IF NOT EXISTS bridge_tokens (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid        NOT NULL REFERENCES orgs    (id) ON DELETE CASCADE,
    bridge_id    uuid        NOT NULL REFERENCES bridges (id) ON DELETE CASCADE,
    -- SHA-256 hex digest of the opaque token. The raw token is never stored.
    token_hash   text        NOT NULL UNIQUE,
    -- Human label so an operator can tell a bridge's tokens apart.
    name         text        NOT NULL DEFAULT 'bridge token',
    -- Last 4 chars of the raw token, shown in listings without exposing it.
    last_four    text,
    -- Set by ``rotate`` on the OLD token: the deadline after which it stops
    -- validating. While in the future, both the old and the new token validate
    -- (grace window) so a running agent can swap without a tunnel drop.
    grace_until  timestamptz NULL,
    -- Set when revoked; a non-null value means the token is rejected on every
    -- subsequent handshake/heartbeat and the broker drops the live tunnel.
    revoked_at   timestamptz NULL,
    last_used_at timestamptz NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bridge_tokens_org_id     ON bridge_tokens (org_id);
CREATE INDEX IF NOT EXISTS idx_bridge_tokens_bridge_id  ON bridge_tokens (bridge_id);
CREATE INDEX IF NOT EXISTS idx_bridge_tokens_token_hash ON bridge_tokens (token_hash);
