# Billing Model

Nubi's billing is designed around one principle: **only meter things that map to
a real cost-of-goods-sold (COGS) line** we actually pay.  Dimensions with near-zero
marginal COGS (seats, connector count, dashboards, saved queries, flow definitions)
are never metered.

Nubi does not host a warehouse — there is no server-side bytes-scanned,
compute-unit, or storage dimension.  The only real marginal COGS left are CDN
egress + per-request edge compute for **embedded sessions** and Anthropic
token pass-through for **AI calls**, plus a small **agent runs** meter (an
on-demand remote-kernel escape hatch).

Authoritative values live in `backend/app/ee/billing/tiers.py`.  This document
is a human-readable summary of what is in that file.

---

## Currency and FX

- Prices are **set in US dollars (USD)** and converted to **South African rand
  (ZAR)** at billing time.
- Conversion formula: `ceil_to_nearest_10(usd × fx_rate × 1.02)`.  The 2% FX
  buffer absorbs intraday drift and protects margin during ZAR weakness.
- The live mid-market rate is fetched daily (07:00 SAST) from frankfurter.app
  (fallback: open.er-api.com) and cached.  If no fresh rate exists within
  72 hours the hardcoded emergency fallback (R16.26) is used and the result is
  flagged as stale.
- The **USD anchor price is fixed for the duration of a subscription plan**.
  The ZAR amount charged each cycle varies slightly as the exchange rate changes.
- The current rate and its freshness are visible in billing settings.
- Payment processor: **Paystack** (ZA-local cards: 3.41% effective rate
  including 15% VAT on the Paystack fee).

---

## Tiers

Five tiers (June 2026 reference amounts at R16.26 + 2% FX buffer):

| Tier | USD/mo | ZAR/mo (ref) | Annual (USD) | Gross margin |
|------|--------|--------------|--------------|--------------|
| **Free** | $0 | R0 | $0 | — (acquisition) |
| **Starter** | $9 | R150 | $90 | 86.6% |
| **Team** | $49 | R820 | $490 | 85.6% |
| **Pro** | $149 | R2,480 | $1,490 | 79.7% |
| **Enterprise** | $1,000 floor | R16,590 floor | $10,000 floor | 75.5% |

Annual pricing: 10 months charged, 2 months free (e.g. Pro = $1,490/yr).

All tiers meet or exceed the ≥75% gross-margin target.

---

## Seat Policy

**All tiers provide unlimited seats and viewers.** There is no per-seat pricing
at any tier.  One additional user = one database row + one auth check ≈
R0.001/month incremental COGS.  Rate-limiting is enforced by the metered usage
quotas (embedded sessions / AI calls / agent runs), not user count.

---

## Metered Dimensions

Nubi has no hosted warehouse — there is no server-side bytes-scanned,
compute-unit, or storage dimension left to price.  Nubi meters only the
dimensions that map to real COGS lines:

| Metric | COGS line | Overage rate (ZAR) |
|--------|-----------|--------------------|
| **Embedded sessions/10K** | CDN egress + per-request edge compute | R50/10K sessions |
| **AI calls** | Anthropic Claude API token pass-through | R5.00/call |
| **Agent runs** | On-demand remote-kernel escape hatch (container compute) | R2.00/run |

Overage rates are drawn from the org's **usage wallet** (prepaid credit balance).
Overages are available from Starter tier upward.  The Free tier has hard stops;
overages require an upgrade.

---

## Dimensions That Are NOT Metered

The following have near-zero marginal COGS and are never charged:

- Seats and viewers (1 DB row + 1 auth check ≈ R0.001/user/month)
- Connector count (1 DB row + connection pool entry ≈ R0.002/connector/month)
- Dashboard count (1 DB row + JSON blob ≈ R0.001/dashboard/month)
- Saved query count (1 DB row ≈ R0.001/query/month)
- Flow definition count (1 DB row + JSON spec ≈ R0.001/flow/month)

---

## Resource Limits by Tier

| Limit | Free | Starter | Team | Pro | Enterprise |
|-------|------|---------|------|-----|------------|
| Seats / viewers | Unlimited | Unlimited | Unlimited | Unlimited | Unlimited |
| Connectors | 3 | 5 | 15 | Unlimited | Unlimited |
| Max query rows | 10K | 100K | 1M | 5M | Unlimited |
| Dashboards | 5 | 10 | 30 | 100 | Unlimited |
| Flows | 2 | 3 | 8 | 20 | Unlimited |
| Embedded sessions/mo | 0 | 1,000 | 5,000 | 25,000 | Unlimited |
| Agent runs/mo | 0 | 0 | 10 | 50 | 1,000 |
| AI calls/mo | 0 | 5 | 15 | 50 | 500 |

---

## Usage Wallet

Overages beyond a tier's included quota are drawn from the org's **usage wallet**
— a prepaid credit balance held in USD cents and charged in ZAR at billing time
via Paystack.

### Wallet mechanics

- **Balance** — USD-cent integer stored server-side; ZAR equivalent shown in the
  UI at the current FX rate.
- **Manual topup** — org admin buys credits through the billing UI; Paystack
  processes the ZAR charge and the wallet is credited on `charge.success`.
- **Auto-topup** — when the balance drops below a configurable **threshold**
  (default $10.00 = 1,000 cents; UI-allowed range **$1–$10,000**), the system
  automatically charges the saved Paystack card for the configured **topup
  amount** (default $50.00 = 5,000 cents; UI-allowed range **$5–$100,000**).
  The auto-topup is fire-and-forget (non-blocking) — it never delays the debit
  that triggered it.
- **Monthly topup cap** — optional ceiling (**$5–$100,000**) on the total
  auto-topup credits in a calendar month.  A *soft* limit: prevents runaway
  automatic spending while manual top-ups keep working.
- **Spend cap** — optional ceiling (**$1–$100,000**) on total monthly usage
  credits added.  A *hard* limit, enforced before each auto-topup attempt.
- **Zero-balance hard stop** — if the balance reaches zero and the tier's included
  quota is exhausted, the metered action is blocked with a `wallet_balance_insufficient`
  error.  The org must top up to continue.
- **In-flight guard** — an auto-topup attempt atomically claims a
  `topup_in_flight` lock before charging the card, so two concurrent debits
  crossing the threshold at once can never double-charge. A claim abandoned by
  a crashed process self-heals after `TOPUP_IN_FLIGHT_TTL_SECONDS` (600s / 10
  minutes), so a later debit can still trigger a fresh attempt.
- **3-D Secure (paused charges)** — if Paystack requires 3DS for the saved
  card, the unattended charge cannot complete: it is recorded as a
  `TOPUP_FAILED` ledger entry with `reason: "3ds_required"` (no balance
  change) rather than erroring. A manual top-up (which the customer completes
  interactively) is required to keep the card usable for future auto-topups.
- **Idempotency** — every topup and debit carries a unique `ref_id`; duplicate
  Paystack webhook deliveries are silently skipped.

### Ledger entry types

| Type | Meaning |
|------|---------|
| `TOPUP_MANUAL` | User-initiated credit (paid via Paystack) |
| `TOPUP_AUTO` | Automatic card charge |
| `TOPUP_PROMO` | Promotional/granted credit |
| `TOPUP_FAILED` | Failed auto-topup attempt (no balance change) |
| `USAGE_LLM` | AI/agent call debit |
| `USAGE_EMBED` | Embedded-session overage debit |
| `USAGE_OVERAGE` | Generic overage debit |
| `ADJUSTMENT_CREDIT` / `ADJUSTMENT_DEBIT` | Manual admin adjustments |
| `EXPIRY` | Credit expiry |

---

## Security Dial → Tier Mapping

The security dial (0–100) gates query complexity, data-volume controls, and
audit stringency.  Paid tiers unlock wider ranges:

| Dial value | Minimum tier |
|------------|-------------|
| 0–40 | Free |
| 41–60 | Starter |
| 61–70 | Team |
| 71–80 | Pro |
| 81–100 | Enterprise |

---

## Feature Flags by Tier

| Feature | Free | Starter | Team | Pro | Enterprise |
|---------|------|---------|------|-----|------------|
| White label | — | — | Badge removable | Full | Full + custom SDK |
| Row-level security | — | Basic | Full JWT | Full JWT | HIPAA-ready |
| Google SSO | — | Yes | Yes | Yes | Yes |
| SAML SSO | — | — | — | 1 IdP | Unlimited IdPs |
| SCIM provisioning | — | — | — | — | Yes |
| Multi-tenant workspaces | — | — | — | — | Yes |
| Custom domain | — | — | — | Yes | Yes |
| Audit log retention | None | 7 days | 30 days | 90 days | Unlimited |
| Priority support | — | — | — | Email + Slack | Dedicated CSM |
| SLA uptime | None | None | None | 99.5% | 99.95% |
| SLA P1 response | — | — | — | — | 30 min (24/7) |
| SLA P2 response | — | — | — | — | 2 hr |

Enterprise includes a named CSM, monthly business review calls, and a private
Slack/Teams channel for support escalation.

---

## ZAR Disclosure Copy

> Nubi's subscription prices are set in US dollars (USD) and converted to
> South African rand (ZAR) using a daily exchange rate refreshed from tier-1 FX
> providers. The ZAR amount charged each billing cycle may vary slightly as the
> exchange rate changes between cycles. Your USD price anchor remains fixed for
> the duration of your plan. The current exchange rate and when it was last
> updated is visible in your billing settings. Questions? Contact billing@nubi.io.

---

## Implementation References

- Tier catalogue and resource limits: `backend/app/ee/billing/tiers.py`
- Wallet service (credit/debit/auto-topup): `backend/app/ee/billing/wallet.py`
- Wallet storage (balance + ledger + topup config): `backend/app/ee/billing/wallet_store.py`
- FX conversion: `backend/app/ee/billing/fx.py`
- Paystack client: `backend/app/ee/billing/paystack.py`
- Billing event store: `backend/app/ee/billing/store.py`
- EE billing routes: `backend/app/ee/billing/routes.py`
- DB migration: `database/migrations/ee/0022_wallet.sql`
- Internal scenario modelling: `billing-model/generate_scenarios.py` (gitignored)
