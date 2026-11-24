-- 0035_users_password_changed_at.sql
-- When the password last changed. Access JWTs issued before this moment are
-- refused, so a password reset also ends access tokens that are still within
-- their lifetime. NULL = never changed since this column exists; every token
-- is accepted. Apply before starting an API build that reads the column.

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at timestamptz NULL;
