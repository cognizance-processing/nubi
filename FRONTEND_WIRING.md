# Frontend wiring — close the backend↔UI gap

Audit finding: many backend features ship endpoints/models but have **no UI**, and
the shell surfaces are inconsistent. Frontend-file counts confirm: `/watches` 0,
`/ai/pin` 0, `/cache` 0, `/ops` 0, `/ai/context` 0, `/metrics` 1 (query-only).

User complaints (authoritative):
1. Git/versions **not consistently accessible** on the topbar across dashboards /
   queries / flows (the GitButton only renders when a page sets `topbarSlot`).
2. **Redundant environment selector** in the flows topbar (FlowsPage `EnvSelector`
   duplicates the `SidebarEnvSelector` in AppSidebar — both drive EnvContext).
3. **No persistent RHS switch for git / versions** — git is a transient slide-in.
4. Major features have **no UI**: metrics CRUD + picker, watches/alerts, ask→pin,
   chat model picker, flow variables, pre-run estimate, run/task detail.

## Process
Disjoint-file agents (non-worktree, edit-only); orchestrator owns the SHARED
central files (`src/App.jsx` routes, `src/components/app/AppSidebar.jsx` nav) and
verifies (`npm run build` + `npm run test:dash`), commits green.

## Wave G (this pass)
- **G1 — git/versions shell + env de-dup** [shared shell, ONE agent]:
  `AppShell.jsx`, `UiContext.jsx`, `GitSyncPanel.jsx`, `FlowsPage.jsx`. Make the
  git/versions control **persistent + consistent** across dashboards/queries/flows
  (a RHS rail toggle in the shell, not per-page `topbarSlot`); remove the redundant
  flows-topbar `EnvSelector` (keep the single sidebar env control / fold env+version
  into the unified surface).
- **G2 — metrics UI** [new files + DashboardEditor]: `src/lib/metrics.js` (CRUD
  client), `MetricsPage.jsx` (list/create/edit/delete + query preview), `MetricPicker.jsx`,
  wire the picker into the dashboard editor's widget inspector (`metric_id` binding).
- **G3 — watches UI** [new files]: `src/lib/watches.js`, `WatchesPage.jsx`
  (create/list/evaluate, alert state).
- **G4 — chat model picker + ask→pin** [ChatPanel]: render the existing MODELS
  picker; add a "Pin to dashboard" button on dashboard/query chat results → `POST /ai/pin`.
- **G5 — flow variables panel** [NodeInspector + client]: view/set flow variables
  (`/variables` CRUD) in the flow inspector.

Orchestrator (central, after agents): add `/metrics` + `/watches` routes (App.jsx)
and Metrics/Watches nav links (AppSidebar.jsx); verify; commit.

## Wave G — DONE ✅ `686d06c` (frontend-only, build green, dash 336/336)
- G1 git/versions persistent in the shell across all pages + flows env-selector de-dup.
- G2 metrics UI (lib + MetricsPage + MetricPicker + editor binding).
- G3 watches UI (lib + WatchesPage with evaluate + AI explanation).
- G4 chat model picker (allowlisted, persisted) + ask→pin button.
- G5 flow variables UI in NodeInspector (+ insert {{vars.NAME}}).
- Central: /metrics + /watches routes + sidebar nav.

**Open question for the user:** G1 made git a PERSISTENT top-right button opening the
RHS slide-in panel. If you want a dedicated **right-edge rail** (vertical icon strip
with git/versions toggles) rather than a single button, that's a Wave-H follow-up.

## Wave H (next pass)
Pre-run estimate chip in the query editor (`POST /query/estimate`); expandable
run/task detail + logs in `FlowRunView.jsx`; cache/ops admin surface; embed
web-component metric-binding parity; output-schema viewer.
