# Nubi Open-Core Architecture — Canonical Overview

This document is the canonical reference for Nubi's CE/EE split: what lives
where, the enforcement rules, how both images are built, and how the features
that shipped in the current development run (auto pre-aggregations, Docker
self-host, embed demo, billing) map onto core vs. EE.

For the original gate-API spec created by EeSeamAgent see
[`docs/open-core.md`](./open-core.md).

---

## 1. Model: GitLab CE/EE

Nubi follows the **GitLab CE/EE convention**:

- The **OSS core (CE)** ships under the MIT licence and includes everything
  needed to self-host a fully functional analytics platform.
- The **EE layer** contains commercial features that are loaded optionally at
  runtime. When the EE tree is absent the server starts cleanly and OSS users
  lose nothing except the commercial features.

The key invariant: **core code never imports from `app.ee` or `src/ee/`.**
That single rule is what makes it possible to ship the CE tree without the EE
tree.

---

## 2. Feature split — CE vs EE

### Backend

| Area | CE (OSS core) | EE (`backend/app/ee/`) |
|------|--------------|------------------------|
| Flows DAG engine | `app.flows` — work-pool executor, secrets, storage, extract/bucket_load nodes | — |
| Auto pre-aggregations | `app.preagg` — query-log mining, rollup builder, scheduler | — |
| Connectors (8 sources) | `app.connectors` — postgres, duckdb, http_json, mysql, mariadb, jdbc, snowflake, bigquery | — |
| Query / RLS / cache | `app.routes.query` + planner + content-hash cache | — |
| Dashboards / widgets | `app.routes.*` + widget CRUD | — |
| Embedding | JWT verifier, scope gate, origin pinning | — |
| Git sync | `app.git` + `routes.git` | — |
| AI / MCP | `app.ai`, `app.chat`, MCP server | — |
| Kernel (on-demand) | `app.kernel` (E2B/Modal adapters) | — |
| Feature-gate seam | `app.features` — `feature_enabled()` / `register_feature()` | — |
| Licensing resolution | — | `app.ee.licensing` — tier from `NUBI_LICENSE_KEY` |
| Billing routes + Paystack | — | `app.ee.billing` — routes, store, Paystack client, tiers |
| Paid-tier enforcement | feature-flag seam in core (`feature_enabled("paid_tiers")`) | EE registers checker |
| Advanced RBAC / SSO | _(planned)_ | _(planned EE sub-modules)_ |

### Frontend

| Area | CE (`src/`) | EE (`src/ee/`) |
|------|------------|----------------|
| Dashboard editor | `src/editor/` | — |
| Query workspace | `src/pages/app/QueryWorkspace.jsx` | — |
| Connectors page | `src/pages/app/ConnectorsPage.jsx` | — |
| Settings page | `src/pages/app/SettingsPage.jsx` | — |
| Feature-flag hook | `src/lib/features.js` — `useFeature()` / `isFeatureEnabled()` | — |
| EE slot registry | — | `src/ee/registry.js` |
| EE entry point | — | `src/ee/index.js` — `registerEe()` |
| Billing UI | — | `src/ee/billing/` — BillingPage, UpgradePrompt, BillingNavBadge |

---

## 3. Repository layout

```
nubi/
├── backend/
│   └── app/
│       ├── features.py          ← feature-gate seam (core)
│       ├── flows/               ← DAG engine (core)
│       ├── preagg/              ← auto pre-aggregations (core)
│       ├── connectors/          ← connector registry + RLS (core)
│       └── ee/                  ← EE package (commercial, optional)
│           ├── __init__.py      ← load_ee() entry point
│           ├── licensing/       ← license resolution
│           └── billing/         ← Paystack billing routes + tiers
├── src/
│   ├── lib/features.js          ← frontend feature-flag store (core)
│   └── ee/                      ← EE frontend (commercial, optional)
│       ├── index.js             ← registerEe()
│       ├── registry.js          ← slot registry
│       └── billing/             ← billing UI components
├── database/migrations/         ← zero-padded sequential SQL migrations
├── docker-compose.yml           ← CE community self-host stack
├── backend/Dockerfile           ← CE backend image (ee/ excluded)
├── frontend/Dockerfile          ← CE frontend image (src/ee/ excluded)
├── scripts/smoke.sh             ← live smoke test against a running stack
├── examples/embed-demo/         ← self-contained embed demo (index.html)
├── ee/LICENSE                   ← commercial licence placeholder
└── LICENSE                      ← MIT licence (core)
```

---

## 4. The no-import rule (enforcement)

```
┌─────────────────────────────────────────────────────────┐
│                    OSS Core (CE)                        │
│                                                         │
│  feature_enabled("billing")   → False  (default-deny)  │
│  feature_enabled("flows")     → True   (default-allow)  │
│                                                         │
│  NO import from app.ee.*  or  src/ee/*                  │
└────────────────────┬────────────────────────────────────┘
                     │  register_feature() at startup
                     │  (backend)  /  dynamic import (frontend)
                     ▼
┌─────────────────────────────────────────────────────────┐
│                    EE Package                           │
│                                                         │
│  load_ee(app) → lazy-imports each EE sub-module         │
│              → each sub-module calls register_feature() │
│              → returns True / False                     │
│                                                         │
│  registerEe() → setEnabledFeatures([...])               │
│              → registerBilling() → registerSlot(...)    │
└─────────────────────────────────────────────────────────┘
```

**Backend gate API** (`backend/app/features.py`):

```python
from app.features import feature_enabled, register_feature

# Core asks — never reaches into ee/:
if feature_enabled("billing"):
    ...

# EE registers at startup — never touched by core:
register_feature("billing", lambda: get_license().is_paid)
```

Default behaviour:

| Feature name | No EE present | EE present + checker truthy |
|---|---|---|
| `"billing"`, `"paid_tiers"` | `False` | `True` |
| Any OSS feature name | `True` | `True` |

**Frontend gate** (`src/lib/features.js`):

```js
import { useFeature, isFeatureEnabled } from '../lib/features.js'

// In a React component:
const hasBilling = useFeature('billing')   // false in CE build

// Outside React:
if (isFeatureEnabled('git')) { ... }       // true in CE build
```

`src/ee/index.js` is loaded via `import()` in `App.jsx` only when `src/ee/`
is present in the build. If the dynamic import fails (CE build), core
continues with all commercial flags at `false`.

---

## 5. Startup sequence

### Backend (FastAPI)

`main.py` runs at startup:

```
1. Mount all core routes (flows, query, connectors, ai, …)
2. call load_ee(app)
   ├─ try: import app.ee.licensing → register_feature("billing", checker)
   ├─ try: import app.ee.billing   → billing_setup(app) mounts EE routes
   └─ returns True (EE active) or False (OSS only)
3. Log EE status; server ready.
```

If `app/ee/` is absent: `load_ee` catches `ImportError`, returns `False`,
logs nothing alarming, server starts cleanly in OSS mode.

### Frontend (React/Vite)

`App.jsx` runs at mount:

```
1. Render all core routes (CE features).
2. dynamic import('./ee/index.js')
   ├─ success → registerEe() → setEnabledFeatures([...]) + registerSlot(...)
   └─ failure → useFeature('billing') stays false; CE runs normally.
```

---

## 6. Building and shipping

### Community Edition (CE) image

The CE image excludes all EE code at **build time** using `.dockerignore`:

- `backend/Dockerfile` builds from `backend/` — the `backend/app/ee/`
  sub-tree is excluded by `.dockerignore` so it never lands in the image.
- `frontend/Dockerfile` builds Vite with `src/ee/` excluded similarly.

```bash
# Build and start CE stack (docker-compose.yml):
make up            # docker compose up --build -d
make smoke         # scripts/smoke.sh — health + auth + query round-trip
make down          # docker compose down
```

The CE stack (`docker-compose.yml`) stands up three services:

| Service | Image | Exposed |
|---|---|---|
| `db` | postgres:16-alpine | internal only |
| `backend` | CE backend (FastAPI / uvicorn) | internal, nginx-proxied |
| `frontend` | CE frontend (nginx + Vite SPA) | `0.0.0.0:8080` |

The backend runs `docker-entrypoint.sh` which applies all pending migrations
before starting uvicorn, so `make up` is a zero-config cold-start.

### Enterprise Edition (EE) image

The EE image is built from the same `Dockerfile` **without** excluding
`app/ee/` and `src/ee/`. Concretely:

```bash
# Build EE backend (include ee/ tree):
docker build \
  --build-arg INCLUDE_EE=1 \
  -f backend/Dockerfile \
  -t nubi-backend-ee .

# Supply a license key at runtime:
docker run -e NUBI_LICENSE_KEY=nubi_pro_... nubi-backend-ee
```

`NUBI_LICENSE_KEY` is resolved by `backend/app/ee/licensing/license.py`:

| Key prefix | Tier | `is_paid` |
|---|---|---|
| absent / empty | FREE (OSS) | False |
| `nubi_pro_…` | PRO | True |
| `nubi_enterprise_…` | ENTERPRISE | True |

---

## 7. How the shipped features map onto CE/EE

### Auto pre-aggregations (CE — wired)

Lives entirely in the OSS core:

- `backend/app/preagg/` — query-log miner, rollup builder, registry.
- `backend/app/flows/handlers/preagg_refresh.py` — task handler registered
  with the flows engine.
- `backend/app/preagg/scheduler.py` — `ensure_preagg_flow()` registers a
  scheduled flow (cron `0 * * * *` by default) per org at startup.
- No EE dependency. No feature flag. Available to every CE self-hoster.

The flow spec uses kind `preagg_refresh`, wired into the flows task registry
(`app.flows.registry`). The work-pool executor picks it up and runs
`run_preagg_refresh(org_id, min_hits)` on each tick.

### Docker self-host (CE — built)

The full CE self-host stack ships as:

| File | Purpose |
|---|---|
| `docker-compose.yml` | Three-service stack (db + backend + frontend) |
| `backend/Dockerfile` | CE backend image, ee/ excluded |
| `frontend/Dockerfile` | CE frontend image, src/ee/ excluded |
| `frontend/nginx.conf` | SPA fallback + `/api` proxy to backend |
| `.env.compose` | CE environment template |
| `docker-entrypoint.sh` | Migration-on-boot + uvicorn start |
| `Makefile` | `up` / `down` / `smoke` / `logs` shortcuts |
| `scripts/smoke.sh` | Live smoke test: health + auth + query round-trip |

Everything a self-hoster needs is in those eight files.

### Embed demo (CE — built)

`examples/embed-demo/` contains a self-contained HTML page that demonstrates
the full embed auth flow:

- JWT signed by `scripts/sign_embed_jwt.py` using `EMBED_SECRET`.
- `<nubi-dashboard>` web component mounted with `getToken()`.
- No server dependency: works against any running Nubi backend.

The demo is CE: it uses only the OSS embed contract (HS256 JWT + `getToken()`
+ `<nubi-dashboard>`). EE features (paid-tier gating, advanced RBAC) are not
required to run it.

### Billing (EE — scaffolded)

The billing subsystem is fully behind the EE gate:

**Backend (`backend/app/ee/billing/`):**

- `tiers.py` — tier → resource limits mapping (row limits, query quotas, etc.).
- `paystack.py` — lazy-imported Paystack client (ZAR, 2.9% + R1 local fee);
  never imported at module top-level.
- `store.py` — billing event store (InMemory + Pg dual pattern).
- `routes.py` — EE billing routes mounted by `load_ee()` via `billing_setup(app)`.

**Frontend (`src/ee/billing/`):**

- `BillingPage.jsx` — billing management UI.
- `UpgradePrompt.jsx` — inline upgrade nudge for feature-gated surfaces.
- `BillingNavBadge.jsx` — nav badge showing current tier.
- `registerBilling.js` — calls `registerSlot()` for each billing component.

**DB migration:** `database/migrations/0017_billing.sql`.

Core behaviour without EE: `feature_enabled("billing")` returns `False`;
billing routes are never mounted; no Paystack SDK is ever imported.

---

## 8. Adding a new EE feature

```
Backend
-------
1. Pick a feature name, e.g. "sso".
2. Create backend/app/ee/sso/__init__.py.
3. Call declare_commercial("sso") + register_feature("sso", checker).
4. Wire into load_ee() with a try/except lazy import.
5. Gate core behaviour: if feature_enabled("sso"): ...
6. Write tests using register_feature("sso", lambda: True/False).

Frontend
--------
1. Create src/ee/sso/SsoSettings.jsx.
2. In src/ee/sso/registerSso.js call registerSlot("sso-settings", SsoSettings).
3. Import + call registerSso() from src/ee/index.js inside registerEe().
4. In core: const SsoPanel = useSlot("sso-settings") ?? null.
```

---

## 9. Related documents

| Document | Content |
|---|---|
| [`docs/open-core.md`](./open-core.md) | EeSeamAgent's original gate API spec (authoritative on the feature-gate contract) |
| [`docs/self-host.md`](./self-host.md) | Self-hosting guide for operators |
| [`docs/embedding.md`](./embedding.md) | Embedding guide for host-app developers |
| [`docs/flows.md`](./flows.md) | Flows DAG engine reference |
| [`ROADMAP.md`](../ROADMAP.md) | Product roadmap with milestone status |
| [`TASKS.md`](../TASKS.md) | Per-wave task breakdown |
