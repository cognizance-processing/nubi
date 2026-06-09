# Nubi Cloud

Nubi Cloud is the **managed, hosted** way to run Nubi. It is a deliberately
**thin layer** on top of the open-source project: the entire product — the
query workspace, dashboards, embedding, Flows orchestration, connectors,
pre-aggregations, AI, and MCP — is the same open-source code you can self-host.
Cloud only adds the things that genuinely require a managed operator.

## What Cloud adds (and self-host doesn't have)

Everything in this section is part of the Enterprise Edition (EE) tree and is
**not** present in a pure open-source self-host. The OSS database schema never
even creates these tables (the billing migrations live under
`database/migrations/ee/` and are applied only when the cloud layer is active).

- **Billing & subscriptions** — the five plans (Free, Starter, Team, Pro,
  Enterprise), collected via Paystack. See **[Billing, plans & usage wallet](billing-and-usage)**.
- **Usage wallet** — prepaid credits with manual and automatic top-up and spend
  caps, used to cover metered overages.
- **Overages & metering** — usage beyond your plan's quota (storage, compute,
  AI calls, embedded sessions, agent runs). Prices are **anchored in USD** and
  **billed in ZAR** at a daily-refreshed exchange rate.
- **Invoices** — monthly invoice PDFs (base subscription + overages + VAT where
  applicable), emailed and downloadable from your billing settings.
- **Managed infrastructure & SLA** — hosting, backups, scaling, and (on
  Enterprise) a contractual uptime SLA and dedicated support.

## What's identical to self-host

The product itself. Connectors, queries, parameters, dashboards, the Flows
builder, pre-aggregations, embedding, AI/chat, MCP, organizations, projects,
roles, secrets, and the security/embed-JWT model are the **same open-source
code** whether you run Nubi Cloud or host it yourself. Anything you learn in the
**Using Nubi** section applies to both.

## Cloud vs self-host at a glance

| Capability | Open-source self-host | Nubi Cloud |
|---|---|---|
| Full product (queries, dashboards, flows, embed, AI, MCP) | ✅ | ✅ |
| You operate infra, upgrades, backups | ✅ (your responsibility) | Managed |
| Subscriptions / plans / Paystack billing | — | ✅ |
| Usage wallet, overages, invoices, VAT | — | ✅ |
| USD-anchored pricing billed in ZAR (daily FX) | — | ✅ |
| Uptime SLA + dedicated support | — | ✅ (Enterprise) |

## Pricing

Plans are anchored in **US dollars** and billed in **South African Rand** at a
daily-refreshed exchange rate (with a small buffer); your USD price anchor stays
fixed for the duration of your plan. The full breakdown — what's metered, the
usage wallet, overage rates, and invoices — is in
**[Billing, plans & usage wallet](billing-and-usage)**.

> Want to run everything yourself instead? See the **Open-source project**
> section, starting with **[Self-hosting](self-host)**.
