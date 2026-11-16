-- 0032_jwt_issuers_host_mode_columns.sql
-- 0006 declares host_mode and org_claim inside CREATE TABLE IF NOT EXISTS, so a
-- database whose jwt_issuers table predates those columns never received them,
-- and every JWT-issuer read or write there fails. Add them where missing; a
-- no-op on databases that already have them.

ALTER TABLE jwt_issuers ADD COLUMN IF NOT EXISTS host_mode boolean NOT NULL DEFAULT FALSE;
ALTER TABLE jwt_issuers ADD COLUMN IF NOT EXISTS org_claim text;
