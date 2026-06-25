-- Migration 0022: access_grants — hierarchical RLS scope expansion tables.
--
-- Provides two tables that support the E.2 hierarchical scope expansion feature:
--
-- 1. ``access_hierarchy`` — org-scoped parent→child dimension value mappings.
--    Example: region="Western Cape" → [store_id=10, store_id=11, store_id=12]
--    The planner reads this table at RLS-injection time to expand a coarse grant
--    (region) into a precise IN-list predicate over the child dimension.
--
-- 2. ``access_grants`` — org-scoped explicit grants of (dimension, value) pairs
--    to subjects (user, role, embed token sub).  Optional companion to the
--    hierarchy table; may be used by token-issuance logic to populate policies.
--
-- SECURITY:
-- * All rows are org-scoped (org_id column + all queries MUST filter on org_id).
-- * The planner reads from access_hierarchy by org_id only — cross-org leakage
--   is prevented at the query level (never JOIN across orgs).
-- * Entries in access_hierarchy are trusted platform data, NOT user-supplied.
--   Admins may only manage entries within their own org_id.
-- * Deletion of a parent_value cascades to child references at the application
--   layer; no DB-level cascade is provided here to keep the migration simple.
--
-- Tenant isolation guarantee:
--   Every SELECT against access_hierarchy MUST include ``WHERE org_id = $1``
--   (the caller's org).  The HierarchyResolver in rls_hierarchy.py enforces this.

-- ---------------------------------------------------------------------------
-- access_hierarchy: org-scoped dimension parent→child value tree
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS access_hierarchy (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Org scoping. Every query MUST filter on org_id.
    org_id          uuid        NOT NULL,
    -- The dimension column name this mapping applies to.
    -- Example: 'region', 'store_id', 'department'
    dimension       text        NOT NULL,
    -- The coarse (parent) value that a token policy may carry.
    -- Example: 'Western Cape', 'EMEA'
    parent_value    text        NOT NULL,
    -- The fine-grained (child) value that the parent expands to.
    -- Example: '10', '11', '12' (store IDs), 'ZA', 'NG' (country codes)
    child_value     text        NOT NULL,
    -- Who created this mapping (audit trail).
    created_by      uuid        NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Uniqueness: one org cannot register the same parent→child pair twice.
    UNIQUE (org_id, dimension, parent_value, child_value)
);

-- Fast lookup by org + dimension + parent_value (the resolver hot-path).
CREATE INDEX IF NOT EXISTS idx_access_hierarchy_lookup
    ON access_hierarchy (org_id, dimension, parent_value);

-- ---------------------------------------------------------------------------
-- access_grants: org-scoped explicit grants of dimension values to subjects
-- ---------------------------------------------------------------------------
-- Optional companion table; used by token-issuance logic to decide which
-- policy values to embed in an embed token's ``policies`` claim.
-- The planner NEVER reads access_grants directly — it only uses the verified
-- token claims.  access_grants is for admin/issuance tooling only.
CREATE TABLE IF NOT EXISTS access_grants (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Org scoping.
    org_id          uuid        NOT NULL,
    -- The subject this grant applies to (user_id, role name, embed sub, etc.).
    subject_type    text        NOT NULL,   -- 'user' | 'role' | 'embed_sub'
    subject_id      text        NOT NULL,
    -- The dimension column and the value (or parent_value) being granted.
    dimension       text        NOT NULL,
    value           text        NOT NULL,
    -- Optional expiry; NULL means no expiry.
    expires_at      timestamptz NULL,
    created_by      uuid        NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- One org cannot grant the same (subject, dimension, value) twice.
    UNIQUE (org_id, subject_type, subject_id, dimension, value)
);

-- Fast lookup by org + subject (for grant listing / token issuance).
CREATE INDEX IF NOT EXISTS idx_access_grants_subject
    ON access_grants (org_id, subject_type, subject_id);

-- Fast lookup by org + dimension + value (for who-has-access queries).
CREATE INDEX IF NOT EXISTS idx_access_grants_dimension_value
    ON access_grants (org_id, dimension, value);
