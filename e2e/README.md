# Nubi End-to-End Tests (Playwright)

This directory contains Playwright end-to-end specs for the Nubi frontend.

## Quick start (manual stack)

If your backend and frontend are already running:

```bash
# Install Playwright browsers (first time only)
npx playwright install chromium

# Run all specs
npm run test:e2e

# Run a specific spec file
npx playwright test e2e/auth.spec.js

# Run headed (visible browser)
npx playwright test --headed

# Show HTML report
npx playwright show-report
```

## Automated full run (`scripts/e2e.sh`)

The orchestration script starts a throwaway Postgres container, migrates the
schema, seeds demo data, starts the backend and frontend, runs the full suite,
then tears everything down.

```bash
bash scripts/e2e.sh
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `E2E_BASE_URL` | `http://localhost:5173` | Frontend base URL passed to Playwright |
| `BACKEND_URL` | `http://localhost:8000` | Backend URL; used for health-check |
| `DATABASE_URL` | auto-generated | Postgres connection string. Set this + `SKIP_DOCKER_PG=1` to use an existing DB |
| `SKIP_DOCKER_PG` | `0` | Set to `1` to skip starting the Docker Postgres |
| `PG_PORT` | auto-free port | Port mapped to the throwaway Postgres container |
| `PG_CONTAINER` | `nubi_e2e_pg` | Docker container name |
| `JWT_SECRET` | dev default (32+ bytes) | JWT signing secret for the backend |
| `KERNEL_LOCAL_ENABLED` | `true` | Enable local Python kernel |
| `PLAYWRIGHT_ARGS` | _(empty)_ | Extra args forwarded to `playwright test`. E.g. `--headed`, `--debug`, `--grep auth` |

### Requirements

- **Docker** (for the ephemeral Postgres)
- **Python 3.10+** with the backend's `requirements.txt` installed
- **Node.js 18+** + `npm install` already run
- Playwright browsers: `npx playwright install chromium`

### CI example

```yaml
- name: Install deps
  run: |
    npm ci
    npx playwright install chromium
    pip install -r backend/requirements.txt

- name: Run e2e
  env:
    SKIP_DOCKER_PG: 0   # let the script spin up Docker Postgres
  run: bash scripts/e2e.sh
```

## Spec inventory

| File | What it covers |
|---|---|
| `auth.spec.js` | Login (seeded user), register (fresh user), login failure |
| `query-library.spec.js` | `/queries` page: list queries, run one, see results + cache badge |
| `editor.spec.js` | `/editor`: add KPI + chart + filter, edit title, save → `/editor/:id`, reload persists |
| `dashboard-view.spec.js` | `/d/sample`, `/d/:id` spec boards, URL-bound variables, edit link |
| `ai-chat.spec.js` | Ask AI panel: open, type prompt, Generate, NullProvider spec returned, Replace canvas |

## Seeded demo credentials

The backend `seed.py` (or `seed_demo.py`) creates:

- **Email**: `test@nubi.dev`
- **Password**: `nubitest123`

These are the credentials used by all specs via `e2e/helpers/auth.js`.

## `data-testid` attributes added to source files

The following `data-testid` attributes were added to the minimum set of source
files permitted by the task spec:

### `src/editor/QueryLibrary.jsx`
| testid | Element |
|---|---|
| `query-library-page` | Page root `<div>` |
| `query-list` | Wrapper `<div>` around all query cards |
| `query-card` | Individual query card |
| `query-card-name` | Card heading `<h2>` |
| `query-run-btn` | "Run" button inside each card |
| `query-result` | Result section `<div>` (shown after a run) |

### `src/editor/DashboardEditor.jsx`
| testid | Element |
|---|---|
| `editor-title` | Dashboard title `<input>` in the top bar |
| `editor-save-btn` | Save / Create `<button>` in the top bar |
| `palette-add-kpi` | Palette "KPI" button |
| `palette-add-table` | Palette "Table" button |
| `palette-add-chart` | Palette "Chart" button |
| `palette-add-filter` | Palette "Filter" button |
| `palette-add-text` | Palette "Text" button |
| `editor-canvas` | RGL canvas `<main>` |
| `widget-<id>` | Each widget wrapper `<div>` (dynamic; e.g. `widget-kpi_1`) |

### `src/pages/DashboardViewPage.jsx`
| testid | Element |
|---|---|
| `dashboard-view-page` | Page root `<div>` |
| `dashboard-view-error` | Fallback / error notice banner |
| `dashboard-spec-renderer` | Wrapper `<div>` around `<SpecRenderer>` |
| `dashboard-html-renderer` | Wrapper `<div>` around `<DashboardView>` |

## Artefacts

Playwright writes:
- `playwright-report/` — HTML report (open with `npx playwright show-report`)
- `test-results/` — screenshots, traces, videos for failed tests
