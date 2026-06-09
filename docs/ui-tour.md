# The Nubi UI — a guided tour

![The Nubi app shell — sidebar, topbar, and main canvas](illustration:HeroIllustration)

This page walks through the authenticated Nubi app so the shape of the product clicks into place before you dive into the feature docs. After reading it you'll know where everything lives, how the workspace switchers work, and how the settings area is structured.

> First time here? After signing in you land on **Home** (`/home`). If your workspace is empty, Home shows a three-step setup spine — Connect a source → Run a query → Build a dashboard. Follow it and the rest of the app falls naturally into place.

---

## The app shell

Every authenticated screen shares the same frame. Three regions are always visible; a fourth slides in on demand.

![The app shell: sidebar with org and project switchers on the left, per-page top bar, and the page content — here Home's setup spine](/docs/screenshots/home.png)

| Region | What it's for |
|--------|---------------|
| **Left sidebar** | Navigation, organisation switcher, project switcher. Full-height; collapses to icons. |
| **Top bar** | Per-page toolbar slot (centre-left) and the chat toggle + account menu (right). |
| **Page content** | The route you navigated to. |
| **AI chat panel** | Slides in from the right edge; hidden until you open it. |

On mobile the sidebar is hidden behind an off-canvas drawer (hamburger in the top bar), and the chat panel becomes a full-screen overlay.

---

## The left sidebar

### Nubi logo

The logo at the top-left links back to the public landing page. Next to it is a chevron button that collapses the sidebar to a narrow icon strip and expands it again. Your preference is remembered across sessions. In collapsed mode, hover any icon to see its label as a tooltip.

### Organisation switcher

The **org selector** (building icon) sits directly below the logo. It shows the name of your active organisation. Click it to see all organisations you belong to and switch between them — a checkmark marks the active one. Every API request is scoped to the active org.

### Project switcher

The **project selector** (folder icon) sits directly below the org selector and follows the same pattern. Projects are workspaces inside an organisation — connectors, queries, dashboards, flows, and secrets all live within the active org and project.

To create a new project: open the dropdown and click **New project**, then type a name in the prompt that appears.

The hierarchy is **org → project → resources**. Switching either selector refreshes the page content to reflect the new scope.

> Viewers (the `viewer` role) can browse everything but won't see create, edit, or run controls.

### Primary navigation

Below the selectors is the main nav. The active item shows a tinted background and a small dot on the right.

| Item | Route | What you'll do there |
|------|-------|----------------------|
| **Home** | `/home` | Setup progress, stat cards, quick-access grid, and recent dashboards and flows. |
| **Connectors** | `/connectors` | Add and manage data sources (Postgres, BigQuery, HTTP/JSON, and more). |
| **Data** | `/data` | Browse and explore tables and columns across your connectors. |
| **Queries** | `/queries` | Author SQL in a Monaco editor, run queries, and save registered queries. |
| **Dashboards** | `/dashboards` | View, search, and open live dashboards. |
| **Flows** | `/flows` | Build multi-step pipelines — cells arranged as a canvas or notebook. |
| **Automations** | `/automations` | Schedule flows and jobs to run on a cron schedule. |

### Secondary navigation

A divider separates two pinned items at the bottom of the sidebar:

- **Docs** (`/docs`) — in-app documentation viewer (the page you're reading now).
- **Settings** (`/settings`) — your profile, organisation, and project configuration.

Superadmins also see an **Admin** link that opens the internal admin console.

---

## The top bar

The top bar spans the full width of the content area and has two zones.

### Centre-left — the page toolbar slot

Each page mounts its own controls here. Simple pages leave it empty; editor-style pages fill it with context-relevant buttons. On the **Flows** page, for example, you'll see the environment selector, Save, Validate, and Run buttons here. Because the slot belongs to the current page, the controls change as you navigate.

### Right — chat toggle and account menu

Two controls sit at the far right:

1. **AI chat toggle** (message-square icon) — opens or closes the global chat panel. When a page owns its own embedded chat (e.g. the dashboard editor), this button is hidden to avoid duplication.
2. **Account menu** (your avatar or initials) — click it for:
   - Your display name and email address.
   - **Light mode / Dark mode** — toggles the theme. The label always names the mode you'll switch *to*.
   - **Settings** — jumps to `/settings/profile`.
   - **Sign out**.

---

## The AI chat panel

Click the chat toggle (or any **Ask AI** button on Home) to slide in the chat panel. Ask questions about your data in plain language — Nubi can run grounded text-to-SQL, draft dashboards, and drive an agentic tool loop on your behalf. See [AI, Chat & MCP](/docs/ai-and-mcp) for the full capability reference.

On desktop the panel shares the screen alongside your content (340 px wide). On mobile it opens full-screen. Close it with the toggle again or the panel's own close button.

---

## Light and dark theme

Nubi ships both themes.

- **Switch:** open the account menu (top-right avatar) and click **Light mode** or **Dark mode**.
- **First visit:** Nubi follows your operating-system preference until you pick one explicitly.
- **Sticky:** once you choose, it's remembered across sessions and the OS default is no longer followed.

---

## Home (`/home`)

Home has two modes, chosen automatically based on workspace state.

**Setup mode** — shown to new workspaces (or until you click **Skip setup**). Three step-cards guide you through the minimum path to a live dashboard:

1. Connect a data source → `/connectors`
2. Run your first query → `/queries`
3. Build a dashboard → `/editor`

A progress bar and a `n/3` counter track completion. A "What's next" row below the spine surfaces Flows, Automations, and Version control. Click **Skip setup** (top-right of the section) to jump directly to the general home; click **Resume setup** on the banner to return.

**General home** — shown once all three steps are done (or after skipping). It contains:

- **Stat row** — live counts for Dashboards, Queries, Connectors, and Flows. Each card links to that section.
- **Quick access grid** — one tile per feature surface, including an AI assistant tile that opens the chat panel.
- **Recent** — your most recently updated dashboards (opening `/d/<id>` full-screen) and flows (opening `/flows/<id>`).

An **Ask AI to build it for you** button in the header opens the chat panel from anywhere on Home.

---

## The environment selector

Some workspaces — most prominently **Flows** — show an **environment selector** in the top-bar toolbar slot: a small pill with the active environment name and a coloured dot.

Environments namespace materialised targets and flow run artefacts so `dev` and `prod` runs never overwrite each other.

| Environment | Dot colour | Notes |
|-------------|------------|-------|
| **prod** | green | Default. The production target. |
| **dev** | blue | For development runs. |
| *custom* | violet | Any environment you add (e.g. `staging`). |

To switch or create an environment:

1. Click the environment pill.
2. Pick an environment from the dropdown — the active one shows a checkmark.
3. To add one, click **Add environment**, type a name (lowercase, numbers, `-` and `_` only), and press **Enter** or **Add**. It's saved and selected immediately.
4. To remove a custom environment, hover it and click the **×**. Removing the active one falls back to `prod`.

---

## Settings

Navigate to **Settings** (sidebar bottom or account menu) to open the unified settings area. A grouped left sidebar — mirroring the Linear/Vercel pattern — keeps every setting in one place, organised by scope.

![The settings area: Account, Organization, and Project groups in one place](/docs/screenshots/settings-organization.png)

| Group | Section | Route | What's there |
|-------|---------|-------|--------------|
| **Account** | Profile | `/settings/profile` | Display name, avatar (URL or upload), email (read-only). |
| **Organization** *(active org name shown)* | General | `/settings/organization` | Org name and other org-level settings. |
| | Members | `/settings/members` | Invite members, view roles, remove members. |
| | Security | `/settings/security` | JWT issuers — register the public keys or JWKS endpoints your backend uses to sign embed tokens. |
| | Billing | `/billing` | Cloud/EE only; visible only when the billing feature is enabled. |
| **Project** *(active project name shown)* | General | `/settings/project` | Project name and Git sync configuration. |

`/settings` redirects to `/settings/profile`. The settings sidebar is sticky on large screens so you can scan all sections without scrolling.

---

## Secrets (`/flows/secrets`)

Secrets are org-scoped key-value pairs used inside flow tasks via `{{ secrets.NAME }}`. They are intentionally homed under Flows rather than in the top-level nav, because secrets only exist to serve flow execution.

Navigate there via **Flows → Secrets** (a link inside the Flows page) or directly at `/flows/secrets`. Values are write-only: they are never returned by the API or shown in the UI after creation. See [Secrets](/docs/secrets) for the full reference and CLI commands.

---

## Dashboard full-screen view (`/d/:id`)

![A live dashboard rendered full-width — KPIs, charts, and a data table](/docs/screenshots/dashboard-view.png)

Opening a dashboard from the Dashboards list or from a Recent card on Home navigates to `/d/<id>` — a clean full-viewport view with no app-shell chrome. This mode is ideal for presenting or embedding via the `<iframe>` embed pattern. Use your browser's back button or the close control to return to the shell.

---

## A first end-to-end pass

1. Open **Connectors** and add a data source.
2. Open **Data** to browse the tables it exposes.
3. Open **Queries**, write a SQL query, run it, and save it as a registered query.
4. Open **Dashboards** and create a new dashboard (`/editor`), pulling in your registered query.
5. Open **Flows** to chain steps into a pipeline, then **Automations** to run it on a schedule.
6. Open the **AI chat panel** whenever you'd rather describe what you want than build it by hand.

That's the whole shell. The rest of these docs are deeper dives into each area:
[Connectors](/docs/connectors) · [Queries & Parameters](/docs/queries-and-params) · [Dashboards](/docs/dashboards) · [Flows](/docs/flows) · [AI, Chat & MCP](/docs/ai-and-mcp) · [Embedding](/docs/embedding)
