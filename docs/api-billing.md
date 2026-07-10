# Usage & billing

> Part of the [API reference](/docs/api-reference) — see it for conventions, error codes, and the audit log.

## Usage & billing

Two layers. **Usage metering is open core** — the `usage_events` table and its
read views ship in every self-host build (billing off). **Billing routes are
Nubi Cloud only** — they live in the `ee/` tree ([open core](/docs/open-core))
and are mounted only when Nubi's Cloud deployment sets `NUBI_LICENSE_KEY`. A
self-host build never usage-limits and never exposes the `/ee/billing/*` routes.

### Usage (open core)

| Method | Path | Description |
|---|---|---|
| `GET` | `/usage` | Current usage totals for the caller's org, read from `usage_events`. |
| `GET` | `/usage/series` | Time-series of metered usage. |

### Billing (Nubi Cloud only)

Prices are in ZAR; payments via Paystack. See [Billing model](/docs/billing-model).

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/pricing` | Public | Tier + price catalogue (no auth). |
| `GET` | `/ee/billing/tier` | Member | The org's current tier and quotas. |
| `POST` | `/ee/billing/checkout` | Member | Start a Paystack checkout for a tier/top-up. |
| `POST` | `/ee/billing/webhook` | Paystack HMAC | Paystack event sink (payment confirmations). |
| `GET` | `/ee/billing/events` | Member | Billing event history. |
| `GET` | `/ee/billing/invoices` | Member | List invoices. |
| `GET` | `/ee/billing/invoices/current-cycle` | Member | The in-progress cycle's running charges. |
| `GET` | `/ee/billing/invoices/{invoice_id}/pdf` | Member | Download an invoice PDF. |
| `GET` | `/ee/billing/wallet` | Member | Prepaid ZAR wallet balance. |
| `POST` | `/ee/billing/wallet/topup` | Member | Top up the wallet. |
| `PUT` | `/ee/billing/wallet/autotopup` | Member | Configure auto-top-up thresholds. |
| `GET` | `/ee/billing/admin/orgs/{org_id}` | Superadmin | Per-org billing state (support). |
| `PUT` | `/ee/billing/admin/orgs/{org_id}` | Superadmin | Override a org's tier/quota (disable billing, set overrides). |

### AI provider keys (Nubi Cloud)

| Method | Path | Description |
|---|---|---|
| `POST` | `/ai/keys` | Store a bring-your-own-model provider key (encrypted at rest). |
| `DELETE` | `/ai/keys` | Remove the stored provider key. |

Metered operations raise `402 quota_exceeded` when a Cloud quota is exhausted.
Metered dimensions: `compute_units`, `ai_calls`, `embedded_sessions`,
`agent_runs`, `storage_gb`.

---
