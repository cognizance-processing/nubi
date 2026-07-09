# Quickstart — clone, seed, run, first dashboard

This guide takes you from a fresh checkout to a running Nubi instance with demo
data and your first dashboard in under five minutes. All steps run locally; no
cloud account is required.

![Nubi query editor — SQL workspace with live results](illustration:HeroIllustration)

---

## Prerequisites

| Tool | Minimum version |
|---|---|
| Git | any |
| Docker + Docker Compose | Docker 24 / Compose v2 |
| **— or —** Python | 3.11+ |
| **— or —** Node | 20+ (frontend only) |

For the fastest path use Docker. The dev-path (Python + Node) is better for
active development.

---

## Path A — Docker Compose (one command)

```bash
# 1. Clone
git clone https://github.com/nu-bi/nubi.git
cd nubi

# 2. Start the full stack (Postgres + MinIO + backend + frontend)
make up
# docker compose up -d --build
```

The compose stack exposes the app on **http://localhost:8080** once all
containers are healthy (about 30 seconds on first build).

```bash
# 3. (Optional but recommended) Seed the demo workspace
#    — creates admin@nubi.dev / nubi-admin-2026 with a full demo project
docker compose exec backend python seed.py --demo
```

The `--demo` flag does three things in one command:

1. Creates the superuser `admin@nubi.dev` / `nubi-admin-2026`.
2. Creates a personal organisation and a "Default" project.
3. Seeds the **demo bundle** — a DuckDB connector backed by ~17 Parquet files
   (orders, sales, products, regions, events, …) plus sample queries and
   dashboards — into that project. The bundle is removable and identical to what
   ticking **Add demo data** at the register screen produces.

Open **http://localhost:8080**, sign in as `admin@nubi.dev` / `nubi-admin-2026`,
and you land on the Home screen with the demo workspace ready.

> Without `--demo` the seed creates only the bare superuser. You are directed to
> the /onboarding wizard on first login to create your org and optionally add
> demo data there.

### Run a smoke test

```bash
make smoke      # scripts/smoke.sh — health, auth, query assertions
```

---

## Path B — Dev path (backend + frontend separately)

Use this when you want fast reload cycles during development.

**Prerequisites:** Python 3.11+, Node 20+, a Postgres 16 instance (local or
Neon).

```bash
# ── 1. Clone ──────────────────────────────────────────────────────────
git clone https://github.com/nu-bi/nubi.git
cd nubi

# ── 2. Backend ────────────────────────────────────────────────────────
python3.11 -m venv .venv-backend
source .venv-backend/bin/activate
pip install -r backend/requirements.txt

# Copy env template and set the two required vars
cp .env.example .env
# Edit .env: set DATABASE_URL and JWT_SECRET (see below)

# Run migrations
python database/migrate.py

# Seed the demo workspace (with venv active and DATABASE_URL set)
cd backend && python seed.py --demo
# → admin@nubi.dev / nubi-admin-2026

# Start the API server
uvicorn main:app --reload
# API:    http://localhost:8000
# Swagger: http://localhost:8000/docs  (disabled in production)

# ── 3. Frontend (new terminal, repo root) ─────────────────────────────
npm install
# In .env set: VITE_BACKEND_URL=http://localhost:8000
npm run dev
# Frontend: http://localhost:5173
```

Open **http://localhost:5173** and sign in as `admin@nubi.dev` / `nubi-admin-2026`.

### Minimum required environment variables

| Variable | Notes |
|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host/db?sslmode=require` — or `postgresql://user:pass@localhost:5432/nubi` for a local DB |
| `JWT_SECRET` | At least 32 random bytes — `openssl rand -hex 32` |
| `VITE_BACKEND_URL` | Frontend only: `http://localhost:8000` |

The `.env.example` in the repo root documents every optional variable.

---

## First dashboard in 3 steps

After sign-in with the seeded demo workspace you have a running connector and
sample data. Here is the fastest path to a live dashboard.

### Step 1 — Run a query

1. Click **Queries** in the sidebar.
2. Click **New query** in the queries panel on the right.
3. Select the **Demo data** connector in the toolbar.
4. Paste the SQL below and press **Cmd/Ctrl + Enter**:

```sql
SELECT
    region,
    DATE_TRUNC('month', order_date) AS month,
    SUM(amount)                     AS revenue,
    COUNT(*)                        AS orders
FROM sales
GROUP BY region, month
ORDER BY month DESC, revenue DESC
```

You should see rows stream in. The result is registered in the browser's
DuckDB-WASM engine as `cell_1`.

5. Click **Save**, name it `revenue_by_region_month`, and confirm. It moves
   from Drafts into the Registry.

### Step 2 — Build a dashboard

1. Click **Dashboards** → **New dashboard**.
2. Open the **Add** panel (+ in toolbar) and add a **KPI** widget and a
   **Chart** widget.
3. On the KPI, open **Configure** → set Query to `revenue_by_region_month`, Value column to `revenue`, label `Total Revenue`, format `currency`.
4. On the Chart, open **Configure** → set Query to `revenue_by_region_month`, chart type `line`, X column `month`, Y series `revenue`, color `region`.
5. Click **Save**.

### Step 3 — View and share

Click **Open** on the dashboard card (or visit `/d/<id>`) to see the live
board. Share the URL; anyone with access opens it in their browser — no server
compute per view (DuckDB-WASM runs locally).

---

## What the demo data contains

The **Demo data** connector is a DuckDB instance backed by ~17 Parquet files.
Key tables:

| Table | Rows (approx.) | Content |
|---|---|---|
| `sales` | 50 000 | Order lines — `order_id`, `region`, `product_id`, `amount`, `order_date` |
| `products` | 200 | Catalogue — `product_id`, `name`, `category`, `price` |
| `customers` | 10 000 | `customer_id`, `country`, `segment` |
| `events` | 100 000 | Clickstream — `event_type`, `session_id`, `ts` |
| `order_lines` | 50 000 | `ordered_qty`, `delivered_qty`, `shipped_at` — used in PvD metric |

Browse all tables from **Connectors → View data** (the Data Browser).

---

## Where to go next

| Goal | Doc |
|---|---|
| Understand the full UI | [UI tour](/docs/ui-tour) |
| Connect your own data source | [Connectors](/docs/connectors) |
| Write parameterised queries | [Queries & Parameters](/docs/queries-and-params) |
| Accelerate repeated queries | [Pre-aggregations](/docs/pre-aggregations) |
| Build a full dashboard | [Dashboards](/docs/dashboards) |
| Define a governed metric | [Semantic layer & data apps](/docs/semantic-and-data-apps) |
| Automate a pipeline | [Flows](/docs/flows) |
| Embed a dashboard in your app | [Embedding](/docs/embedding) |
| Deploy to production | [Self-host](/docs/self-host) |
| Full API reference | [API reference](/docs/api-reference) |
