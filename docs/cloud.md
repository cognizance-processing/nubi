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

## How Nubi Cloud runs (architecture)

Nubi Cloud runs on **Fly.io** as a single app (`nubi`) in the **`jnb`
(Johannesburg)** region. One combined Docker image — the FastAPI backend with
the built SPA embedded — runs as **two processes**:

```
                        ┌──────────────────────────────────────────────┐
                        │  Fly app "nubi" (region: jnb)                │
                        │  one combined image, two processes           │
   https (force_https)  │                                              │
  Browser / embeds ────▶│  ┌─────────────────────────────────────┐     │
                        │  │ app — uvicorn (FastAPI)             │     │
                        │  │  • /api/v1 + SSE                    │     │
                        │  │  • serves the SPA (STATIC_DIR,      │     │
                        │  │    same origin — no CORS hops)      │     │
                        │  └──────────────┬──────────────────────┘     │
                        │                 │                            │
                        │  ┌──────────────┴──────────────────────┐     │
                        │  │ worker — python worker.py           │     │
                        │  │  • flows scheduler loop             │     │
                        │  │  • worker pool draining task_runs   │     │
                        │  └──────┬───────────────────┬──────────┘     │
                        └─────────┼───────────────────┼────────────────┘
                                  │                   │
                          ┌───────┴────────┐  ┌───────┴────────────────┐
                          │ Neon Postgres  │  │ Cloudflare R2 (S3 API) │
                          │ (DATABASE_URL) │  │ materialized /         │
                          │                │  │ incremental flow       │
                          │                │  │ targets (parquet)      │
                          └────────────────┘  └────────────────────────┘
```

- **`app` process** — uvicorn serving the API *and* the built frontend from
  the same origin (the backend's static-SPA mode). All browser calls are
  same-origin, so cookies and embed sessions need no cross-origin setup.
- **`worker` process** — the standalone flows worker
  (scheduler tick + concurrent worker pool). Scheduled flows and queued
  `task_runs` are executed here, never in the request path.
- **Postgres on Neon** — the only system of record. Machines hold no state.
- **Object storage on Cloudflare R2** (S3-compatible) — materialized and
  incremental flow targets are written as parquet under
  `FLOWS_MATERIALIZE_BASE_URI`, so they survive machine replacement.
- **Migrations** — the forward-only runner (`database/migrate.py`) executes
  as a Fly `release_command` before each rollout: a throwaway machine applies
  pending migrations, then the new image replaces the old one.
- **Git layer (env-as-branch)** — pushes to GitHub/GitLab go through the
  **provider APIs** (GitHub App installation token or GitLab access token).
  There is no server-side git working tree or daemon, so this too keeps the
  machines stateless and disposable.

### Scaling

| Process | Strategy |
|---|---|
| `app` | Fly's proxy auto-stops idle machines and auto-starts them on demand, with **at least one machine always warm** (`min_machines_running = 1`) so embeds and SSE streams get fast first responses. Concurrency limits: 200 soft / 250 hard requests per machine; add machines as traffic grows. |
| `worker` | Always-on, count 1 (no HTTP service, so the proxy never stops it). Scale horizontally with `fly scale count worker=N` — workers lease `task_runs` so replicas don't collide — or automatically with **fly-autoscaler** keyed on pending `task_runs` queue depth. |

### Deploy runbook

- **First deploy / manual deploys** — run `scripts/deploy-fly.sh` from the
  repo root. It verifies `flyctl` is installed and authenticated, creates the
  app on first run, checks the required secrets are set (printing the full
  checklist with `fly secrets set` examples if not), and then runs
  `fly deploy --remote-only`. `scripts/deploy-fly.sh --secrets-only` prints
  the secrets checklist without deploying.
- **Continuous deploys** — every push to `main` triggers
  `.github/workflows/deploy-main.yml`, which runs `flyctl deploy
  --remote-only` using the `FLY_API_TOKEN` repository secret.

Both paths build the combined image (root `Dockerfile`: Vite SPA build →
Python deps → runtime) on Fly's remote builders and roll out the `app` and
`worker` processes together; migrations run automatically via the release
command.

## Pricing

Plans are anchored in **US dollars** and billed in **South African Rand** at a
daily-refreshed exchange rate (with a small buffer); your USD price anchor stays
fixed for the duration of your plan. The full breakdown — what's metered, the
usage wallet, overage rates, and invoices — is in
**[Billing, plans & usage wallet](billing-and-usage)**.

> Want to run everything yourself instead? See the **Open-source project**
> section, starting with **[Self-hosting](self-host)**.
