-- Migration 0014: Add host_mode and org_claim columns to jwt_issuers.
--
-- host_mode (bool, default false):
--   When true, the issuer is trusted as an "embedded host" -- the caller's org
--   is resolved from the named claim in org_claim, bypassing the org_members
--   lookup.  Opt-in per issuer; all other issuers keep requiring membership.
--
-- org_claim (text, nullable):
--   The JWT claim name whose value is treated as the caller's org_id when
--   host_mode is enabled.  Required when host_mode = true (enforced by
--   application logic).

ALTER TABLE jwt_issuers
    ADD COLUMN IF NOT EXISTS host_mode  boolean NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS org_claim  text;
