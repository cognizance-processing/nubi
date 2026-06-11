# Getting started

![Nubi — browser-first BI from zero to live dashboard](illustration:HeroIllustration)

Welcome to Nubi. This guide takes you from a fresh account to a live dashboard: sign up, connect a data source, run your first query, and build your first board. It also covers the Free plan limits and where to go next.

If you want to poke around first, every new workspace includes a built-in **Demo data** connector — no configuration needed.

---

## 1. Create an account or sign in

Open Nubi and you'll land on the welcome screen.

### Sign up

![The sign-up screen — Google OAuth or email, with optional org and project names and a one-click demo project](/docs/screenshots/register.png)

1. Go to **Create account** (`/register`).
2. Choose a sign-up method:
   - **Continue with Google** — the full OAuth handshake happens server-side; Nubi never sees your Google password. You finish workspace setup on the onboarding screen (next section).
   - **Email + password** — enter your **Full name**, **Email address**, and a **Password**.
3. Email sign-up also asks for your workspace details:
   - **Organization name** — required; the placeholder suggests *"Your first name's Org"*.
   - **First project name** — required; pre-filled with *"Default"*.
   - **Add demo data** — checked by default; seeds the project with sample dashboards and data you can remove anytime.
4. Click **Create account**. You land on your Home screen.

Both names can be changed later. Don't overthink them now.

### Sign in

1. Go to **Sign in** (`/login`).
2. Use **Continue with Google**, or enter the email and password you registered with.
3. Click **Sign in**.

### Finish setup — the onboarding screen

![The onboarding screen — accept a pending invite or create your first organization](/docs/screenshots/onboarding.png)

If you're signed in but don't belong to any organization yet — typical right after **Continue with Google** — Nubi shows a full-screen onboarding step with two ways forward:

- **Join an existing organization** — pending invitations sent to your email are listed; click **Accept** to join with the role you were invited as.
- **Create a new organization** — enter an **Organization name** and a **First project name** (pre-filled *"Default"*), optionally tick **Add demo data**, and click **Create organization**.

Either path drops you on your Home screen with a ready-to-use workspace.

---

## 2. Understand the workspace structure

Nubi is organized as **Organization → Project → resources**. Connectors, queries, dashboards, and flows all live inside a project; projects live inside an organization.

Use the **left sidebar** to switch context:

- **Organization selector** (building icon, near the top) — click to switch between organizations you belong to.
- **Project selector** (folder icon, just below) — click to switch projects, or choose **New project**.

Switching the active project re-scopes everything: the connector list, query library, and dashboards all reload for that project.

> **Roles.** Owners, admins, and members can create and edit. **Viewers** are read-only — they can open dashboards and run existing queries but cannot create or edit anything. If write actions like **New query** or **Add connector** are missing, ask an admin to upgrade your role.

---

## 3. Navigate the app

The left sidebar is your primary navigation:

| Nav item | What it's for |
|---|---|
| **Home** | Onboarding checklist and quick links. Tracks your progress through the three core steps. |
| **Connectors** | Add and manage data sources. |
| **Data** | Browse tables and columns inside a connected source. |
| **Queries** | The SQL editor and query library. |
| **Dashboards** | Saved boards and the dashboard editor. |
| **Flows** | Cell-based pipelines — SQL, Python, and Note cells in notebook or canvas view. |
| **Watches** | Monitor a metric and get alerted when a threshold or change condition breaches. |
| **Automations** | Scheduled runs of queries and flows. |
| **Docs** | This documentation, rendered in-app. |
| **Settings** | Profile, organization, members, integrations, usage, and project settings (including the Git connection). |

The **Home** page tracks a three-step spine to your first live board:

![The Home screen — setup checklist and quick-access tiles](/docs/screenshots/home.png)

1. Connect a data source
2. Run your first query
3. Build a dashboard

The rest of this guide follows exactly those steps.

---

## 4. Connect your first data source

![The Connectors page — each source is a card with View data, Test, edit, and delete actions](/docs/screenshots/connectors.png)

Open **Connectors** from the sidebar, then click **Add connector** (top right). A slide-over panel opens.

1. **Pick a type.** The picker groups 20+ types by category — relational databases (Postgres, MySQL, MariaDB, SQL Server, Oracle, CockroachDB), cloud-managed SQL (Cloud SQL, Azure SQL), cloud warehouses (BigQuery, Snowflake, Redshift, Databricks, ClickHouse, Azure Synapse), query engines (Athena, Trino, Presto), lakehouse and files (Parquet/DuckDB via S3, GCS, or HTTPS), and APIs (HTTP/JSON, JDBC). Type in the search box to filter.
2. **Fill in the details.** Give the connector a **name** and complete the type-specific fields (host, port, database, credentials, SSL mode, and so on). Standard ports are pre-filled.
3. **Click Add connector.**

Your credentials are **encrypted at rest with AES-256-GCM** and are never returned by the API after saving.

### Test and explore

Each connector card has four actions:

| Action | What it does |
|---|---|
| **View data** | Opens the Data Browser for that connector. Browse tables and columns. |
| **Test** | Verifies Nubi can reach the source and authenticate. A green pill confirms success; red shows the error. |
| **Edit** | Update the connector's config or credentials. |
| **Delete** | Remove the connector and destroy its encrypted credentials. |

> **No data source yet?** Every workspace includes a built-in **Demo data** connector. It needs no configuration — adding it is a single click from the type picker. It powers all the demo queries used throughout the product. If you ticked **Add demo data** at sign-up, it's already there along with sample queries and dashboards.

---

## 5. Run your first query

![The query workspace — SQL editor on top, results grid below, saved queries on the right](/docs/screenshots/queries-editor.png)

Open **Queries** from the sidebar. The workspace has two parts:

- **Queries panel** (right sidebar, toggled from the toolbar) — a **New query** button, search, and the list of queries split into **Drafts** (unsaved, in-memory) and the saved **Registry**.
- **Query workspace** (main area) — a notebook-style editor with a toolbar, a SQL editor, and a results grid.

### Write and run SQL

1. Click **New query** in the Queries panel (or start from the draft already open).
2. In the toolbar, pick a **Connector**. Leave it on **Demo data** to try things out, or select one of your own connectors. The SQL dialect is auto-detected from the connector you choose.
3. Type your SQL in the Monaco editor:

   ```sql
   SELECT * FROM demo LIMIT 100
   ```

4. Click **Run** (or press **Cmd/Ctrl + Enter**). Results appear in the grid below with the row count and execution time.

Results stream back as **Apache Arrow IPC** — large result sets arrive and render fast. The result is also registered in the browser's **DuckDB-WASM** engine as `cell_1`, so you can query it locally in scratch cells with no server round-trips.

### Use parameters

Add named parameters with double-brace placeholders. Nubi detects them automatically and renders an input for each one above the editor:

```sql
SELECT *
FROM orders
WHERE region = {{ region }}
  AND created_at >= {{ start_date }}
```

Fill in values in the parameter inputs, then **Run**. Each parameter has a type (text, number, date, and so on); the value is coerced correctly before the query executes.

### Save a query to the registry

Saving turns an ad-hoc draft into a **registered query** you can reuse in dashboards, flows, and schedules.

1. Click **Save** in the toolbar.
2. Give it a **name** and confirm.

The query moves from Drafts into the **Registry** in the panel. Registered queries are what dashboard widgets and flows reference by ID.

### Generate SQL with AI

Click the **AI assist** (sparkle) button in the toolbar to describe what you want in plain language — for example, *"Show total sales by region for the last 30 days"* — and Nubi drafts the SQL for you. Edit it before running. AI features require a paid plan; see [Free tier limits](#7-free-tier-limits) below.

### Notebook scratch cells

Below the primary query you can add extra **SQL** or **Python** cells for exploration. SQL scratch cells run entirely in the browser's DuckDB-WASM engine and can reference earlier results by name (`cell_1`, `cell_2`, …). Python cells run on a metered, scale-to-zero server kernel — use them for steps SQL cannot express. Scratch cells are session-only and are not saved with the query.

---

## 6. Build your first dashboard

![A finished board in preview — KPI row, trend and breakdown charts, and a detail table](/docs/screenshots/dashboard-view.png)

Open **Dashboards** from the sidebar, then click **New dashboard**. The **dashboard editor** opens on an empty board.

### Add widgets

Open the **Add widget** panel (the **+** button in the top toolbar) and choose a widget type:

| Widget | What it shows |
|---|---|
| **KPI** | A single headline number. |
| **Metric** | A number with a comparison delta and optional sparkline. |
| **Chart** | Line, bar, horizontal bar, area, scatter, pie, donut, heatmap, or gauge. |
| **Table** | Rows and columns with column formatting and conditional formatting. |
| **Pivot** | A cross-tab with row/column dimensions and an aggregation. |
| **Filter** | A control (select, multiselect, date range, text) that drives a dashboard variable. |
| **Text** | Markdown content for headings and notes. |
| **Section** | A titled divider to group widgets. |

New widgets default to a demo query so you see something immediately.

### Configure a widget

Click a widget to select it, then open the **Configure** panel (gear icon):

1. **Choose the query** — pick a demo query or type the ID of a query you registered in step 5.
2. **Map columns** — Nubi introspects the query's columns and offers dropdowns. For a chart that's the **X** axis and one or more **Y** series (each can be a different chart type and left/right axis); for a KPI it's the **value** column; for a table the **visible columns**.
3. **Tune the display** — labels, number/currency/percent/date formats, stacking, height, conditional formatting, and more.

### Arrange the layout

- **Drag** widgets to move them; **drag the handles** to resize on the grid.
- Switch the device preview between **Desktop**, **Tablet**, and **Mobile** in the toolbar to set responsive layouts per breakpoint.
- Use the **zoom** controls to fit the whole board on screen.

### Make widgets interactive (optional)

- Add a **Filter** widget and point it at a **target variable** (for example `region`). Other widgets whose queries use `{{ region }}` re-query when the filter changes.
- Enable **drilldown / cross-filter** on a chart so clicking a data point sets a dashboard variable, turning one chart into a filter for the rest of the board.

### Save and view

1. Click **Save** (reads **Create** the first time, then **Save**).
2. The board is now listed under **Dashboards**. Click **Open** on its card to view it live, or **Edit** to return to the editor.

> From an empty Dashboards page you can also click **Ask AI to build one** and describe the board you want. Nubi assembles a starting dashboard you can refine in the editor.

---

## 7. Free tier limits

The **Free** plan never expires and is genuinely usable for real projects. All plans — including Free — have unlimited seats and viewers; Nubi does not charge per user.

| Resource | Free plan |
|---|---|
| Seats and viewers | Unlimited |
| Connectors | Up to 3 |
| Saved dashboards | Up to 5 |
| Scheduled flows | Up to 2 |
| Rows returned per query | Up to 10,000 |
| Storage | 1 GB |
| Monthly compute units | 500 |
| AI / agent calls | Not included |
| Embedded sessions | Not included (Nubi branding on embeds) |

When you outgrow these limits — more connectors, larger result sets, scheduled AI, or embedding dashboards in another app — move to a paid plan. See the [pricing page](/pricing) for the full tier comparison, or [Billing & usage](/docs/billing-and-usage) for Nubi Cloud billing details.

> Free workspaces are abuse-capped (request rate and a hard compute ceiling). Storage for an inactive workspace may be reclaimed after a long period of inactivity; active workspaces are unaffected.

---

## Self-hosting Nubi

The repo ships a `docker-compose.yml` that brings up the full stack in one command. Prerequisites: Docker with Compose.

```bash
git clone https://github.com/imranparuk/nubi.git
cd nubi
make up          # docker compose up -d --build
```

The compose stack starts four long-running services (plus a one-shot `minio-init` job that creates the storage bucket):

| Service | Exposed port | Role |
|---|---|---|
| `db` (Postgres 16) | internal | Application database |
| `minio` | 9000 (S3 API), 9001 (console) | S3-compatible object storage |
| `backend` (FastAPI) | internal (nginx proxies `/api`) | API + migrations |
| `frontend` (nginx + SPA) | **8080** | Serves the Vite SPA; proxies `/api` to backend |

Open `http://localhost:8080` once `make up` finishes and create your first account at `/register`. (The OSS image does not ship the seed script — on the dev path you can seed a superuser instead; see below.)

Migrations run automatically on startup via `docker-entrypoint.sh`. To apply them without restarting:

```bash
make migrate
```

### Required environment variables

Copy `.env.example` to `.env` and set at minimum:

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | `postgresql://user:pass@host/db?sslmode=require` |
| `JWT_SECRET` | Yes | Min 32 bytes — `openssl rand -hex 32` |
| `GOOGLE_CLIENT_ID` | OAuth | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | OAuth | Callback URI registered in Google Console |
| `FRONTEND_URL` | Backend | Where the backend redirects after Google OAuth |
| `CORS_ORIGINS` | Backend | Comma-separated allowed origins |
| `ENV` | Backend | `development` or `production` (disables `/docs` in prod) |
| `VITE_BACKEND_URL` | Frontend | Base URL of the FastAPI backend |

The compose file injects `DATABASE_URL` pointing at the bundled Postgres container and MinIO credentials automatically. Override any value in `.env` before running `make up`.

### Dev path — backend and frontend separately

**Prerequisites:** Python 3.11+, Node 20+

```bash
# Backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # Python deps live at the repo root
cp .env.example .env          # edit DATABASE_URL, JWT_SECRET, etc.
python database/migrate.py
cd backend && uvicorn main:app --reload
# API: http://localhost:8000   Swagger: http://localhost:8000/docs (dev only)
```

To seed a superuser (optional, venv active, `DATABASE_URL` set):

```bash
cd backend && python seed.py
# → admin@nubi.dev / nubi-admin-2026 (override with SUPERUSER_EMAIL / SUPERUSER_PASSWORD)
```

```bash
# Frontend (new terminal, repo root)
npm install
cp .env.example .env          # set VITE_BACKEND_URL=http://localhost:8000
npm run dev
# Frontend: http://localhost:5173
```

### EE and cloud migrations

`python database/migrate.py` applies only the OSS core migrations by default. EE migrations (billing, wallet, FX, invoices) live under `database/migrations/ee/` and are applied only with `--ee` or when `NUBI_CLOUD=1` / `NUBI_EE=1` is set in the environment. The OSS self-host schema stays thin — no billing tables it will never use.

### Monorepo layout

```
nubi/
├── backend/          FastAPI application (Python 3.11)
│   ├── app/          Core modules: auth, connectors, queries, dashboards, flows, …
│   │   └── ee/       Enterprise Edition (billing, Paystack, licensing) — never imported by core
│   └── main.py       Uvicorn entry point
├── src/              React 19 + Vite SPA
│   └── ee/           EE-only frontend modules (billing UI, etc.)
├── database/
│   ├── migrate.py    Forward-only SQL migration runner (asyncpg)
│   └── migrations/
│       ├── *.sql     OSS core migrations
│       └── ee/       EE-only migrations (billing, wallet, FX, invoices)
├── sdk/              @nubi/sdk — embedding and CLI client
├── docs/             Markdown docs rendered by the in-app /docs viewer
├── docker-compose.yml  Full stack: Postgres 16, MinIO, backend, frontend (nginx)
├── Makefile          up / down / logs / migrate / smoke
└── .env.example      Environment variable reference
```

---

## Where to go next

You've done the three core things: connected a source, run a query, and built a board. From here:

- **[UI tour](/docs/ui-tour)** — a guided walk through every part of the app shell.
- **[Connectors](/docs/connectors)** — the full list of supported sources and their settings.
- **[Queries & Parameters](/docs/queries-and-params)** — registered queries and typed `{{named}}` parameters in depth.
- **[Dashboards](/docs/dashboards)** — every widget type, chart options, and dashboard variables.
- **[Flows](/docs/flows)** — cell-based pipelines with SQL, Python, and Note cells; notebook and canvas views.
- **[Exports & Scheduled Reports](/docs/exports-and-jobs)** — email a query or board on a schedule.
- **[Embedding](/docs/embedding)** — put a live, row-level-secured dashboard inside your own app.
- **[Git sync](/docs/git-sync)** — version-control your queries, dashboards, and flows in GitHub or GitLab.
- **[Lakehouse](/docs/lakehouse)** — query files on object storage through DuckDB, or provision the one-click managed lakehouse.
- **[Self-host](/docs/self-host)** — detailed deployment guide, SSL, managed Postgres, and production hardening.
