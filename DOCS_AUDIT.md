# Nubi Docs Audit — wave9/docs-audit

**Date:** 2026-06-25  
**Branch merged before audit:** `feat/embed-bi-substrate`  
**Auditor:** automated agent (wave9)

---

## Audience 1 — End Users

| Area | Status | Action taken / recommended |
|---|---|---|
| Getting started (sign-up, connect source, run query, build dashboard) | OK | `docs/getting-started.md` covers all steps end-to-end |
| Watches / alerts | OK | Listed in sidebar nav table in `getting-started.md`; full coverage in `docs/notifications-and-integrations.md` |
| KPI targets | OK | Fully documented in `docs/metrics-reference.md` (MetricTarget shape, RAG semantics, example) |
| `/explain` root-cause analysis | OK | Fully documented in `docs/metrics-reference.md` and `docs/semantic-and-data-apps.md` |
| Embedding (end-user perspective) | OK | `docs/embedding.md` covers full flow; `docs/embed-api.md` is the versioned component reference |
| New features discoverable from docs index | FIXED | `docs/index.md` was missing a link to `docs/embed-api.md` — added |
| Semantic layer / metrics | OK | `docs/semantic-and-data-apps.md` and `docs/metrics-reference.md` cover declaration, querying, KPI targets, and explain |
| Flows, canvas, dashboards, queries | OK | Each has a dedicated doc; `docs/how-to.md` has worked examples |

---

## Audience 2 — OSS Contributors

| Area | Status | Action taken / recommended |
|---|---|---|
| CONTRIBUTING.md at repo root | FIXED | Missing — created `CONTRIBUTING.md` with dev setup, test commands, migration convention, issuer convention, connector/metric/web-component how-to, PR checklist |
| Local dev setup (env vars, venv, npm, seed) | OK | `docs/development.md` and `docs/getting-started.md` both cover this |
| `npm run db:reset` | OK | `npm run db:reset:demo` documented in `docs/development.md` |
| Running backend + frontend separately | OK | Both docs cover the two-server dev path |
| Running tests — `pytest` | OK | `docs/development.md` testing table |
| Running tests — `test:embed` | FIXED | `npm run test:embed` and `npm run test:e2e:embed` were missing from the `docs/development.md` testing table — added |
| Running tests — `test:dash` | OK | Already in testing table |
| Architecture (planner→executor, conformance, engine boundary, connectors, metrics→queries) | OK | `docs/architecture-and-economics.md`, `docs/conformance.md`, `docs/cache-key-spec.md`, `docs/connectors.md`, `docs/semantic-and-data-apps.md` |
| Migration convention documented | FIXED | `docs/development.md` said "Never edit an applied migration — add a new one" (ALTER-based / incremental framing). **Fixed** to describe the actual in-place convention: `CREATE TABLE IF NOT EXISTS`, fold schema into the table's file, reset from scratch, no `ALTER`/`DROP` |
| How to add a connector | FIXED | Not in `docs/development.md` — added to new `CONTRIBUTING.md` |
| How to add a metric | FIXED | Not in `docs/development.md` — added to new `CONTRIBUTING.md` |
| How to add a web component | FIXED | Not in `docs/development.md` — added to new `CONTRIBUTING.md` |

---

## Audience 3 — Cloud / EE Users (host operators)

| Area | Status | Action taken / recommended |
|---|---|---|
| Open-core / EE split documented | OK | `docs/open-core.md`, `docs/architecture-open-core.md`, `docs/self-host.md` all cover the CE/EE split |
| Enabling EE (`--ee` / `NUBI_CLOUD=1`) | OK | `docs/architecture-open-core.md` and `docs/open-core.md` document the flag |
| EE migrations | OK | Both docs list the four EE migration files and the `--ee` / `NUBI_CLOUD=1` / `NUBI_EE=1` flags |
| Host-mode tenancy (claim-native `org_claim`) | OK | `docs/semantic-and-data-apps.md` §"Enabling host mode" covers `host_mode: true` + `org_claim` on the issuer |
| Templated datastores + secret resolver | OK | `docs/semantic-and-data-apps.md` §"Templated datastores and the secret resolver" covers `claim_template.py` + `secret_resolver.py` |
| Outbound webhooks (five events) | OK | `docs/semantic-and-data-apps.md` §"Outbound webhooks" correctly lists all five events including `query_executed` |
| Embed API v1 contract | OK | `docs/embed-api.md` documents all components, attributes, events, theme tokens, capability gating |
| Declarative provisioning (`nubi apply`) | OK | `docs/semantic-and-data-apps.md` §"Declarative provisioning" covers bundle layout, `nubi apply`, `nubi plan` |
| Billing / ZAR / FX | OK | `docs/billing-and-usage.md`, `docs/cloud.md` cover pricing, ZAR billing, FX, wallet |
| Issuer registration via DB-backed API | OK | `docs/embedding.md` and `README.md` correctly point to `POST /api/v1/security/jwt-issuers` |

---

## Known-Stale Items Fixed

| Risk | Location | Fix applied |
|---|---|---|
| Migration convention described as "never edit, add new file" (ALTER-based framing) | `docs/development.md` | Replaced with accurate in-place description: `CREATE TABLE IF NOT EXISTS`, fold into existing file, no `ALTER`/`DROP`, reset with `npm run db:reset:demo` |
| `secrets` table attributed to "migration 0015" (does not exist; it's in `0003_resources.sql`) | `docs/secrets.md` | Fixed reference |
| `secrets` table attributed to "migration 0015" | `docs/files-as-code.md` | Fixed reference |
| `test:embed` and `test:e2e:embed` commands not in testing table | `docs/development.md` | Added both commands (plus MCP and CLI test rows) |
| `docs/embed-api.md` not linked from docs index | `docs/index.md` | Added link under the Embedding section |
| No root-level `CONTRIBUTING.md` | repo root | Created `CONTRIBUTING.md` |

---

## Items Verified Correct (no changes needed)

- `README.md` issuer registration already correctly says `POST /api/v1/security/jwt-issuers` (DB-backed) — not `app/auth/issuers.py`
- `docs/semantic-and-data-apps.md` already says "five platform events" and lists all five including `query_executed`
- No references to deleted migrations 0014 or 0015 were found beyond the `0003_resources.sql` misattribution (fixed above)
- `docs/embedding.md` correctly describes the asymmetric-only embed path and `EMBED_DEV_TOKEN_ENABLED` warning
- EE migrations correctly listed as `0017`, `0018`, `0022`, `0027` (no 0014/0015 in EE tree either)

---

## Scaffolded / Recommended (not yet fully written)

| Gap | Recommendation |
|---|---|
| `docs/development.md` has no "How to add a connector / metric / web component" section | The how-to is now in `CONTRIBUTING.md`; consider a forward link from `docs/development.md` to `CONTRIBUTING.md` |
| No dedicated "Watches" user guide | Currently scattered across `getting-started.md` sidebar table and `notifications-and-integrations.md`; consider `docs/watches.md` for end-user coverage |
