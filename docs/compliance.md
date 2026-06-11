# Compliance & Security Posture

**Status: posture document, not a certification.** Nubi is **not** currently SOC 2
audited, ISO 27001 certified, or formally attested under any framework. This page
documents the controls that **are implemented in the product today**, maps them to
the criteria a buyer's security review checks, and is **honest about the gaps** so an
embedded-analytics buyer (who is trusting us with their customers' data) can make an
informed decision. Where something is a roadmap item, it says so.

Last reviewed: 2026-06-11.

---

## 1. Why this matters for embedded analytics

When a SaaS embeds Nubi dashboards, **we touch their customers' data**. That makes
the buyer's compliance checklist a hard gate, not a nice-to-have. The two controls
that matter most — tenant isolation and credential protection — are enforced in the
product, not by policy alone:

- **Row-level security is enforced in the query planner**, from the *verified* token
  only (see §3). A tenant cannot read another tenant's rows even via a crafted query.
- **Connector credentials are encrypted at rest**; the master key never enters the
  database (see §4).
- **Dashboard *views* run in the browser kernel** and **agents never write raw SQL to
  production** (dev-authoring → human promote) — so the embedded surface has a small,
  auditable trust boundary.

---

## 2. Frameworks — current mapping

### SOC 2 (Trust Services Criteria) — *targeting, not yet audited*
| Criterion | Implemented control | Gap / roadmap |
|---|---|---|
| Security (CC6 access) | JWT auth, scoped tokens, planner-level RLS, rate limiting, env-scoped agent write tokens (dev-only, human promote) | No formal Type II audit; no documented access-review cadence yet |
| Confidentiality | AES-256-GCM (connector creds) + Fernet (named secrets) at rest; TLS in transit; master keys outside the DB | Field-level encryption beyond secrets not implemented |
| Availability (CC7) | Published SLO targets + `/ops/stats` latency percentiles ([observability.md](observability.md)); scale-to-zero heavy pool | No public status page / uptime history yet; per-process metrics not yet aggregated |
| Processing integrity | Output-shape contracts on queries; bound params (never string-concatenated); spec validation | — |
| Privacy | See POPIA/GDPR below | DPA template + sub-processor list pending |

### POPIA (South Africa — Protection of Personal Information Act)
Relevant because Nubi bills in ZAR and may process SA personal information.
- **Lawful processing / minimisation:** Nubi is a *processor* of the customer's data;
  we store query results transiently (cache TTL, default 5 min) and materialised
  rollups only as configured. We do not mine customer data.
- **Security safeguards (§19):** encryption at rest + in transit, RLS, access scopes.
- **Operator obligations (§21):** a Data Processing Agreement is **required** between
  Nubi and each customer — **template is a roadmap item** (see §6).
- **Breach notification (§22):** incident-response runbook is a roadmap item.

### GDPR (EU)
- **Lawful basis / controller-processor:** customer is controller, Nubi is processor.
- **Data subject rights:** access/erasure are served by the customer through their own
  data; Nubi's own account data (users, orgs) cascades on delete
  (`ON DELETE CASCADE` across the schema) so org deletion removes dependent records.
- **Art. 28 (processor):** a signed DPA + documented sub-processors are **required and
  pending** (§6). **Art. 32 (security):** covered by §4.
- **Transfers:** primary storage is Cloudflare R2; document the region + SCCs before
  serving EU controllers (roadmap).

---

## 3. Tenant isolation (RLS) — how it's actually enforced

- Every query is compiled by the planner (`app/connectors/planner.py`), which injects
  RLS predicates from `claims["policies"]` as AST-level `column = value` filters. The
  policies come **only from the verified JWT** — never from the request body or an
  unsigned claim.
- The **same gate covers** the read path (`/query`), the dry-run estimator
  (`/query/estimate`), the **metrics layer** (`/metrics/{id}/query`), pre-aggregation
  rollups (RLS keys stay in the rollup grain), and scheduled flows (the owner's RLS
  policies are snapshotted onto the flow so cron-tick drains stay tenant-scoped).
- Connectors that cannot enforce predicate-level RLS are **refused** at plan-build time
  rather than silently returning unfiltered rows.

## 4. Credential & secret protection

- **Connector credentials:** AES-256-GCM ciphertext (per-datastore), 12-byte unique
  nonce, `key_version` for rotation. Stored as ciphertext only.
- **Named secrets** (`{{ secrets.NAME }}`): Fernet (AES-128-CBC + HMAC-SHA256).
- **Master keys never enter the database** — encryption/decryption happen in the app
  layer; the DB holds only ciphertext + nonce.
- **Secrets are never written to git-synced files** (open-core invariant).
- Git PATs are delivered to subprocesses via a `GIT_ASKPASS` helper + env, **not on the
  command line** (no token in `ps`/process args).

## 5. Auth, sessions, transport

- Short-lived JWT access tokens + **HttpOnly, Secure refresh cookies** (rotation on
  refresh). Embed tokens are a distinct, restricted identity (allowlisted queries only,
  no raw SQL, required-scope gating).
- **Rate limiting** on auth/query/flow-run routes, keyed on the *trusted* client IP /
  verified org (never a forgeable `X-Forwarded-For` or unsigned claim); globally
  enforced via Redis when configured, per-process otherwise.
- TLS terminates at the edge (Fly proxy); `COOKIE_SECURE` enforced outside dev.

## 6. Honest gap list (what a buyer should ask about)

These are **not yet done** and are tracked as roadmap:
1. **Formal SOC 2 Type II audit** — not started; controls above are the readiness basis.
2. **Data Processing Agreement (DPA) template** + signed flow per customer.
3. **Sub-processor list** (Cloudflare R2, Fly.io, Paystack for EE billing, the LLM
   provider) with regions + a change-notification process.
4. **Incident-response + breach-notification runbook** (POPIA §22 / GDPR Art. 33).
5. **Data-retention & deletion policy** documented per data class (cache, rollups,
   usage events, audit logs) with configurable retention.
6. **Penetration-test cadence** + a published results summary.
7. **Access-review cadence** for internal/admin access; audit-log export.
8. **Public status page** / uptime history (the `/ops/stats` percentiles are
   per-process today; cross-process aggregation is pending).
9. **Data-residency / region pinning** for EU controllers + SCCs.

## 7. Sub-processors (current, to be formalised)
| Sub-processor | Purpose | Notes |
|---|---|---|
| Cloudflare R2 | Object storage (lakehouse Parquet, assets) | Region to be pinned/documented |
| Fly.io | Application + scale-to-zero compute | — |
| Paystack | Billing (EE only; ZAR) | Open-core: billing lives in `ee/`, out of OSS core |
| LLM provider (configurable) | AI authoring / answers (only when an API key is set) | NullProvider default = no external calls |

---

*This document reflects the posture as of the date above and will change as controls
are added. It is not legal advice and is not a warranty of compliance.*
