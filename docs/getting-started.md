# Getting started

Welcome to Nubi. This guide walks you through everything you need to go from zero to a live dashboard: creating an account, setting up your workspace, connecting a data source, running your first query, and building your first dashboard. It also covers what's included on the free plan.

If you'd rather just look around first, every new account ships with a built-in **Demo data** source, so you can run queries and build a board before you connect anything of your own.

---

## 1. Create an account or sign in

Open Nubi and you'll land on the welcome screen.

### Sign up

1. Go to **Create account** (`/register`).
2. You have two options:
   - **Continue with Google** — click the button and complete the standard Google sign-in. Nubi never sees your Google password.
   - **Email + password** — fill in your **Full name**, **Email address**, and a **Password**.
3. If you sign up with email, you'll also see two optional fields:
   - **Organization name** — your workspace name (defaults to *"<Your first name>'s Org"* if left blank).
   - **First project name** — defaults to *"Default"*.
4. Click **Create account**. You'll be taken straight to your home screen.

> Both fields can be renamed later, and you can add more organizations and projects at any time — so don't overthink them now.

### Sign in

1. Go to **Sign in** (`/login`).
2. Use **Continue with Google**, or enter the email and password you registered with.
3. Click **Sign in**.

When you sign in with Google, the entire OAuth handshake happens server-side — you're redirected to Google, approve access, and returned to Nubi already authenticated.

---

## 2. Choose your organization and project

Nubi is organized as **Organization → Project → resources**. Connectors, queries, dashboards, and flows all live inside a project, and projects live inside an organization.

You manage both from the **left sidebar**:

- **Organization selector** (building icon, near the top) — click it to switch between organizations you belong to. Your selection is remembered between sessions.
- **Project selector** (folder icon, just below) — click it to switch projects, or choose **New project** to create one. You'll be prompted for a name.

Switching the active project re-scopes everything you see — the connector list, query library, and dashboards all reload for that project.

> **Roles.** Your role in an organization controls what you can do. **Viewers** are read-only — they can open dashboards and run existing queries but cannot create or edit. Every other role (owner, admin, member) can write. If buttons like **New query** or **Add connector** are missing, you likely have viewer access; ask an admin to upgrade your role.

---

## 3. Find your way around

The left sidebar is your primary navigation:

| Nav item | What it's for |
|---|---|
| **Home** | Onboarding checklist and quick links. Shows your progress through the three core steps. |
| **Connectors** | Add and manage data sources. |
| **Data** | Browse the tables and columns inside a connected source. |
| **Queries** | The SQL editor and query library. |
| **Dashboards** | Your saved boards, plus the dashboard editor. |
| **Flows** | Cell-based pipelines — SQL, Python, and Note cells in a notebook or canvas view. |
| **Automations** | Scheduled runs of queries and flows. |
| **Settings** | Profile, organization, project settings, and Git sync. |

The **Home** page tracks a simple three-step spine to your first live board:

1. **Connect a data source**
2. **Run your first query**
3. **Build a dashboard**

The rest of this guide follows exactly those steps.

---

## 4. Connect your first data source

Open **Connectors** from the sidebar, then click **Add connector** (top right).

1. **Pick a type.** A slide-over panel opens with a searchable catalog grouped by category — Postgres, MySQL/MariaDB, SQL Server, BigQuery, Snowflake, Redshift, Databricks, and more. Type in the search box to filter, then click the source you want.
2. **Fill in the details.** Give the connector a **name** and complete the type-specific fields (host, port, database, credentials, SSL mode, etc.). Sensible defaults (like the standard port) are pre-filled.
3. **Save.** Click **Add connector**.

Your credentials are **encrypted at rest with AES-256-GCM** and are never returned by the API once saved — a security note in the form reinforces this.

### Test and explore

Each connector card has actions:

- **Test** — verifies Nubi can reach the source and authenticate. A green pill confirms success; a red one shows what failed.
- **View data** — opens the **Data Browser** for that connector, where you can inspect tables and columns.
- **Edit** / **Delete** — update or remove the connector (delete also destroys its encrypted credentials).

> **No data source yet?** That's fine. Every workspace includes a built-in **Demo data** connector. It needs no configuration — adding it is a single click from the type picker — and it powers all the demo queries (`demo_all`, `demo_active`, and others) used throughout the product.

---

## 5. Run your first query

Open **Queries** from the sidebar. The page is a full SQL workspace:

- A **left rail** lists your queries, split into **Drafts** (unsaved, in-memory) and **Registry** (saved, reusable queries).
- The main area is the **query workspace** — a notebook-style editor with a toolbar, a SQL editor, and a results grid.

### Write and run SQL

1. Click **New query** in the left rail (or start from the draft that's already open).
2. In the workspace toolbar, pick a **Connector**. Leave it on **Demo data (built-in)** to try things out, or select one of your own connectors. The SQL dialect is auto-detected from the connector you choose.
3. Type your SQL in the editor. For example, against the demo data:

   ```sql
   SELECT * FROM demo LIMIT 100
   ```

4. Click **Run** (or press **⌘/Ctrl + Enter**). Results appear in the grid below, along with the row count and how long the query took.

### Use parameters

Add named parameters with double-brace placeholders. Nubi detects them automatically and shows an input for each one above the editor:

```sql
SELECT *
FROM orders
WHERE region = {{ region }}
  AND created_at >= {{ start_date }}
```

Fill in values in the parameter inputs, then **Run**. Each parameter has a type (text, number, date, and so on) so the value is coerced correctly before the query runs.

### Save a query to the registry

Saving turns an ad-hoc draft into a **registered query** you can reuse — in dashboards, flows, and schedules.

1. Click **Save** in the toolbar.
2. Give the query a **name** and confirm.

The query moves from **Drafts** into the **Registry** in the left rail. Registered queries are what dashboard widgets and flows refer to by ID.

### Generate SQL with AI

Click the **AI assist** (sparkle) button in the toolbar to describe what you want in plain language — for example, *"Show total sales by region for the last 30 days"* — and Nubi drafts the SQL for you. You can edit it before running. (AI features depend on your plan; see [Free tier limits](#7-free-tier-limits-and-when-to-upgrade) below.)

> **Notebook cells & exploration.** Below the primary query you can add extra **SQL** or **Python** cells for scratch exploration. Each cell's results are addressable from later cells by name (`cell_1`, `cell_2`, …), so you can chain steps — e.g. `SELECT count(*) FROM cell_1`. These scratch cells are session-only and aren't saved with the query.

---

## 6. Build your first dashboard

Open **Dashboards** from the sidebar, then click **New dashboard**. This opens the **dashboard editor** on a fresh, empty board.

### Add widgets

The editor has a top toolbar with panel toggles. Open the **Add widget** panel (the **+** button) and choose a widget type:

| Widget | What it shows |
|---|---|
| **KPI** | A single headline number. |
| **Metric** | A number with a comparison delta and optional sparkline. |
| **Chart** | Line, bar, horizontal bar, area, scatter, pie, donut, heatmap, or gauge. |
| **Table** | Rows and columns, with column formatting and conditional formatting. |
| **Pivot** | A cross-tab with row/column dimensions and an aggregation. |
| **Filter** | A control (select, multiselect, date range, text) that drives a dashboard variable. |
| **Text** | Markdown content for headings and notes. |
| **Section** | A titled divider to group widgets. |

New widgets default to a demo query so you see something immediately.

### Configure a widget

Click a widget to select it, then open the **Configure** panel (gear icon). Here you:

1. **Choose the query** — pick one of the demo queries or type the ID of a query you registered in step 5.
2. **Map columns to the widget** — Nubi introspects the query's columns and offers dropdowns. For a chart, that's the **X** axis and one or more **Y** series (each can be a different chart type and left/right axis); for a KPI, it's the **value** column; for a table, the **visible columns**; and so on.
3. **Tune the display** — labels, number/currency/percent/date formats, stacking, height, conditional formatting rules, and more.

### Arrange the layout

- **Drag** widgets to move them and **drag the handles** to resize them on the grid.
- Switch the device preview between **Desktop**, **Tablet**, and **Mobile** in the toolbar to set responsive layouts per breakpoint.
- Use **zoom** controls to fit the whole board on screen.

### Make widgets interactive (optional)

- Add a **Filter** widget and point it at a **target variable** (e.g. `region`). Other widgets whose queries use `{{ region }}` re-query when the filter changes.
- Enable **Drilldown / cross-filter** on a chart so clicking a data point sets a dashboard variable — turning one chart into a filter for the rest of the board.

### Save and open

1. Click **Save** (the button reads **Create** the first time, then **Save**).
2. The board is now listed under **Dashboards**. Click **Open** on its card to view it live, or **Edit** to come back to the editor.

> **Let AI build it.** From an empty **Dashboards** page you can also click **Ask AI to build one** and describe the board you want — Nubi assembles a starting dashboard you can refine in the editor.

---

## 7. Free tier limits (and when to upgrade)

The **Free** plan is genuinely usable for real projects and never expires. It includes:

| Resource | Free plan |
|---|---|
| Seats and viewers | **Unlimited** |
| Connectors (data sources) | Up to **3** |
| Saved dashboards | Up to **5** |
| Scheduled flows | Up to **2** |
| Rows returned per query | Up to **10,000** |
| Storage | **1 GB** |
| Monthly compute | **500 compute units** |
| AI / agent calls | Not included on Free |
| Embedded sessions | Not included on Free |

Seats are always unlimited — Nubi does not charge per user on any plan. When you outgrow the Free limits (more connectors, larger result sets, scheduled AI, or embedding your dashboards in another app), you can move to a paid plan. See the [pricing page](/pricing) for the full tier comparison, or [Billing & usage](/docs/billing-and-usage) for Nubi Cloud billing details.

> Free workspaces are abuse-capped (request rate and a hard compute ceiling), and storage for an inactive workspace may be reclaimed after a period of inactivity. Active workspaces are unaffected.

---

## Where to go next

You've now done the three core things: connected a source, run a query, and built a board. From here:

- **[Connectors](/docs/connectors)** — the full list of supported sources and their settings.
- **[Queries & Parameters](/docs/queries-and-params)** — registered queries and typed `{{named}}` parameters in depth.
- **[Dashboards](/docs/dashboards)** — every widget type, chart options, and dashboard variables.
- **[Flows](/docs/flows)** — cell-based pipelines with SQL, Python, and Note cells; notebook and canvas views.
- **[Exports & Scheduled Reports](/docs/exports-and-jobs)** — email a query or board on a schedule.
- **[Embedding](/docs/embedding)** — put a live, row-level-secured dashboard inside your own app.
