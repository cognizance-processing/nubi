-- 0001_extensions.sql
-- Enable citext (case-insensitive text) and pgcrypto (gen_random_uuid()).
-- Both are bundled with Postgres/Neon — no external installs needed.

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
