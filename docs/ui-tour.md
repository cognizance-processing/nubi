# The Nubi UI — a tour

This is a guided walk through the Nubi app for someone who has just signed in for the first time. It explains the parts of the screen, what each navigation item does, and how to move around. Once the shape of the app clicks into place, the feature-specific guides ([Connectors](/docs/connectors), [Queries & Parameters](/docs/queries-and-params), [Dashboards](/docs/dashboards), [Flows](/docs/flows)) will feel familiar.

> New here? After signing in you land on **Home** (`/home`). If your workspace is empty, Home greets you with a three-step setup spine — connect a source, run a query, build a dashboard. Follow it and the rest of the app falls into place.

---

## The app shell at a glance

After you sign in, every authenticated screen shares the same frame — the **app shell**. It has three persistent regions plus an on-demand fourth:

```
┌───────────┬──────────────────────────────────────────────┐
│           │  Top bar:  [page toolbar]      [chat][avatar] │
│  Sidebar  ├──────────────────────────────────────────────┤
│           │                                   │           │
│  (nav +   │     Page content (the route)      │  AI chat  │
│   org +   │                                   │  (slides  │
│   project)│                                   │   in)     │
└───────────┴──────────────────────────────────────────────┘
```

| Region | Where | What it's for |
|--------|-------|---------------|
| **Left sidebar** | Full height, left edge | Primary navigation, plus the organisation and project switchers. |
| **Top bar** | Across the top of the content area | A per-page toolbar slot (left/centre), and the AI chat toggle + your account menu (right). |
| **Page content** | The large central area | The page you navigated to (Home, Queries, Dashboards, …). |
| **AI chat panel** | Right edge, slides in | Ask Nubi about your data in natural language. Hidden until you open it. |

On mobile the layout adapts: the sidebar is hidden behind an off-canvas drawer triggered by a hamburger button in the top bar, and the chat panel becomes a full-screen overlay.

---

## The left sidebar

The sidebar is your map of the product. From top to bottom it holds the workspace selectors, the primary navigation, and a pinned Settings link.

### Workspace selectors — organisation and project

Two dropdowns sit at the top of the sidebar, just under the Nubi logo. They set the *scope* everything else operates in.

1. **Organisation switcher** (building icon). Click it to see all organisations you belong to and switch between them. A checkmark marks the active one. Your choice is remembered across sessions, and every subsequent request is scoped to that org. If you only belong to one org, you'll simply see it named here.
2. **Project switcher** (folder icon), directly beneath. Projects are workspaces *inside* an org. Switch projects the same way. To create a new one, open the dropdown and click **New project**, then type a name in the browser prompt that appears.

The hierarchy is **org → project → resources**: your connectors, queries, dashboards and flows all live within the active org and project. Switch either selector and the page content refreshes to reflect the new scope.

> Read-only members (the `viewer` role) can browse everything but won't see create/edit/run buttons — those are hidden when you don't have write access.

### Primary navigation

Below the selectors is the main nav. Each item is a destination; the active one is highlighted with a tinted background and a small dot.

| Item | Icon | Goes to | What you'll do there |
|------|------|---------|----------------------|
| **Home** | house | `/home` | Your hub — setup progress, usage stats, quick links, and recent dashboards/flows. |
| **Connectors** | plug | `/connectors` | Add and manage data sources (Postgres, DuckDB, MySQL, HTTP/JSON, BYO warehouse). |
| **Data** | table | `/data` | Browse and explore the tables and columns your connectors expose. |
| **Queries** | code | `/queries` | Author and run SQL, save reusable registered queries, and ask AI for SQL. |
| **Dashboards** | dashboard | `/dashboards` | View and open live dashboards. |
| **Flows** | workflow | `/flows` | Build multi-step pipelines on a canvas or as a notebook. |
| **Automations** | calendar | `/automations` | Schedule flows to run on a cron schedule, hands-free. |

A divider separates a pinned **Settings** (gear) link at the bottom, which opens your profile, organisation, project, and security settings.

> **Where are Secrets?** Secrets are scoped to flows (referenced in flow tasks as `{{ secrets.NAME }}`), so they live inside the Flows workspace at `/flows/secrets` rather than in the top-level nav. See [Secrets](/docs/secrets).

### Collapsing the sidebar

There's a chevron toggle next to the logo. Click it to collapse the sidebar to an **icon-only** strip (handy on smaller screens or when you want more room for content), and click again to expand it back to full width with labels. In collapsed mode, hover a nav icon to see its label as a tooltip. Your preference is remembered.

---

## The top bar

The top bar spans the content area and has two functional zones.

### Left/centre — the page toolbar slot

The wide centre region is a **toolbar slot** that each page fills with its own controls. It's empty on simple pages and rich on editor-style pages. For example, on **Flows** the toolbar carries the view toggle, Validate / Save / Run buttons, and the **environment selector** (see below). Because the slot belongs to the current page, the controls you see there change as you navigate.

### Right — chat and account

Two controls sit at the far right:

1. **AI chat toggle** (message-square icon). Opens or closes the AI chat panel on the right. When chat is open the button is highlighted. Some editor-style pages own their own chat experience — on those, this global button is hidden because chat is built into the page.
2. **Account menu** (your avatar or initials). Click it for a small menu with:
   - **Light mode / Dark mode** — toggles the theme (also see [Light and dark theme](#light-and-dark-theme)).
   - **Settings** — jumps to your settings.
   - **Sign out** — logs you out.

---

## The AI chat panel

Click the chat toggle in the top bar (or any **Ask AI** button on Home) to slide in the chat panel from the right. Ask questions about your data in plain language — Nubi can run grounded text-to-SQL, draft dashboards, and drive an agentic loop with a set of tools on your behalf (see [AI, Chat & MCP](/docs/ai-and-mcp)). On desktop the panel shares the screen alongside your content; on mobile it opens full-screen. Close it with the toggle again or the panel's close button.

---

## Light and dark theme

Nubi ships light and dark themes.

- **To switch:** open the **account menu** (top-right avatar) and click **Light mode** or **Dark mode**. The label always names the theme you'll switch *to*.
- **First visit:** Nubi follows your operating system's preference (light or dark) until you pick one explicitly.
- **Sticky:** once you choose a theme it's remembered across sessions, and Nubi stops following the OS setting.

---

## The environment selector

Some workspaces — most prominently **Flows** — let you choose the **run environment** for an execution. It appears as a small pill in the page toolbar, showing the active environment with a coloured dot.

Why it matters: when a flow materializes or writes incremental targets, Nubi namespaces those targets under the environment name, so `dev` and `prod` runs never clobber each other.

| Environment | Dot colour | Notes |
|-------------|------------|-------|
| **prod** | green | The default and the production target. |
| **dev** | blue | For development runs. |
| *custom* | violet | Any environment you add yourself (e.g. `staging`). |

**prod is selected by default.** To work in a different environment:

1. Click the environment pill in the toolbar.
2. Pick an environment from the list — the active one shows a checkmark.
3. To create one, click **Add environment**, type a name (e.g. `staging`; lowercase letters, numbers, `-` and `_` only), and press **Enter** or click **Add**. It's saved for next time and selected immediately.
4. To remove a custom environment, hover it in the list and click the small **×**. Removing the active one falls back to `prod`.

---

## Moving between the main areas

The fastest way to move around is the left sidebar — click **Queries**, **Dashboards**, **Flows**, **Data**, or **Settings** and the central content swaps to that page. A few extra paths are worth knowing:

- **From Home.** The Home hub doubles as a launchpad: a **Quick access** grid links to every area, **stat cards** (Dashboards / Queries / Connectors / Flows) jump straight to those sections, and **Recent** lists open your latest dashboards and flows in one click.
- **Opening a dashboard full-screen.** Opening a dashboard navigates to `/d/<id>`, a clean full-viewport view with no shell chrome — ideal for presenting. Use your browser's back button to return.
- **Deep links.** Most pages have stable URLs — for example `/queries/<id>` for a saved query or `/flows/<id>` for a specific flow — so you can bookmark or share them. Switching org/project keeps you on the same page but re-scopes the content.

### Try it: a first end-to-end pass

1. Open **Connectors** and add a data source.
2. Open **Queries**, write a `SELECT`, run it, and save it as a registered query.
3. Open **Dashboards** (or the editor) and build a board from your query.
4. Open **Flows** to chain steps into a pipeline, then **Automations** to schedule it.
5. Pop open **AI chat** any time you'd rather describe what you want than build it by hand.

That's the whole shell. Everything else in these docs is a deeper dive into one of the areas you just toured.
