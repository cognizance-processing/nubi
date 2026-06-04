-- 0002_users.sql
-- Core user accounts.
-- password_hash is nullable: OAuth-only users have no password.

CREATE TABLE IF NOT EXISTS users (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    email           citext      NOT NULL UNIQUE,
    password_hash   text        NULL,
    email_verified  boolean     NOT NULL DEFAULT false,
    name            text,
    avatar_url      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
