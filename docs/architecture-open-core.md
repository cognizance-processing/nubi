# Open Source + Cloud Architecture

![Core and the ee/ tree: what ships in each image](illustration:OpenCoreSplit)

Nubi is fully open source under Apache-2.0 — self-hosting is free and gets
you the complete, fully functional analytics platform, with billing off. The
code that powers **Nubi Cloud's** billing (the `ee/` tree) ships in the same
repo; it's a separate directory purely for code organization, loaded
optionally at runtime. Nothing needs to be stripped out for self-host, and
there is no separate license to buy — the `ee/` tree simply stays inactive
(billing off) unless Nubi's own Cloud deployment sets its internal
`NUBI_LICENSE_KEY` switch.

The **core invariant** is enforced by one rule: core code never imports from
`app.ee` or `src/ee/`. That single boundary is what lets a self-host build
run cleanly whether or not the `ee/` tree ends up mounted, and is what keeps
billing code from leaking into paths every user shares.

---

## Core vs `ee/` split

### Backend

| Area | Core — `backend/app/` | `ee/` — `backend/app/ee/` (Cloud billing code) |
|------|---------------------|------------------------|
| Flows DAG engine | `app.flows` — work-pool executor, secrets, storage backends, cell-based node kinds | — |
| Auto pre-aggregations | `app.preagg` — query-log miner, rollup builder, scheduler | — |
| Connectors (20+ sources) | `app.connectors` — postgres, duckdb, mysql, mariadb, snowflake, bigquery, redshift, clickhouse, databricks, athena, trino, and more | — |
| Query / RLS / cache | `app.routes.query` + planner + content-hash cache | — |
| Dashboards / widgets | `app.routes.*` + widget CRUD | — |
| Embedding | JWT verifier, scope gate, origin pinning | — |
| Git sync | `app.git` + `routes.git` | — |
| AI / MCP | `app.ai`, `app.chat`, MCP server | — |
| Server kernel | `app.kernel` (E2B/Modal adapters) | — |
| Feature-gate seam | `app.features` — `feature_enabled()` / `register_feature()` | — |
| Licensing resolution | — | `app.ee.licensing` — tier from the internal `NUBI_LICENSE_KEY` switch |
| Billing + Paystack | — | `app.ee.billing` — routes, store, Paystack client, tiers, FX, wallet, quota |
| Paid-tier quota enforcement | `enforce_quota()` hook in core (no-op when billing is off) | Registers the quota checker when billing is on |

### Frontend

| Area | Core — `src/` | `ee/` — `src/ee/` (billing UI) |
|------|-------------|----------------|
| Dashboard editor | `src/editor/` | — |
| Query workspace | `src/pages/app/QueryWorkspace.jsx` | — |
| Connectors page | `src/pages/app/ConnectorsPage.jsx` | — |
| Settings page | `src/pages/app/SettingsPage.jsx` | — |
| Feature-flag hook | `src/lib/features.js` — `useFeature()` / `isFeatureEnabled()` | — |
| Slot registry | — | `src/ee/registry.js` — `registerSlot()` / `getSlot()` |
| Entry point | — | `src/ee/index.js` — `registerEe()` |
| Billing UI | — | `src/ee/billing/` — BillingPage, UpgradePrompt, BillingNavBadge |

---

## Repository layout

```
nubi/
├── backend/
│   └── app/
│       ├── features.py          ← feature-gate seam (core)
│       ├── flows/               ← DAG engine (core)
│       ├── preagg/               ← auto pre-aggregations (core)
│       ├── connectors/          ← connector registry + encryption (core)
│       └── ee/                  ← billing code — ships in every clone
│           ├── __init__.py      ← load_ee() + ee_startup()
│           ├── licensing/       ← tier resolution from NUBI_LICENSE_KEY
│           └── billing/         ← Paystack billing, tiers, FX, wallet, quota
├── src/
│   ├── lib/features.js          ← frontend feature-flag store (core)
│   └── ee/                      ← billing UI — ships in every clone
│       ├── index.js             ← registerEe()
│       ├── registry.js          ← slot registry
│       └── billing/             ← billing UI components + registerBilling.js
├── database/migrations/         ← zero-padded core SQL migrations
├── database/migrations/ee/      ← billing-only migrations (billing, FX, wallet, invoices)
├── docker-compose.yml           ← self-host stack
├── backend/Dockerfile           ← self-host backend image
├── frontend/Dockerfile          ← self-host frontend image
├── scripts/smoke.sh             ← health + auth + query smoke test
├── examples/embed-demo/         ← self-contained embed demo
└── LICENSE                      ← Apache-2.0 (whole repo)
```

---

## Feature-gate API

### Backend (`backend/app/features.py`)

Four public functions form the entire gate contract.

**`feature_enabled(name)`** — called by core at request time:

```python
from app.features import feature_enabled

if feature_enabled("billing"):
    ...  # only reached when billing is switched on (Nubi Cloud)
```

Default behaviour when no checker is registered:

| Feature name | Billing off (self-host) | Billing code present + checker truthy (Nubi Cloud) |
|---|---|---|
| `"billing"`, `"paid_tiers"` | `False` | `True` |
| Any other name | `True` | `True` |

`"billing"` and `"paid_tiers"` are hard-coded in `_COMMERCIAL` at module load.
Additional names can be added via `declare_commercial()`.
A broken checker fails silently and returns `False` — a billing fault never
takes down request handling.

**`register_feature(name, checker)`** — called by the billing code at startup, never by core:

```python
# Inside app/ee/billing/__init__.py — never in core:
from app.features import register_feature

register_feature("billing", lambda: get_license().is_paid)
register_feature("paid_tiers", lambda: get_license().is_paid)
```

The checker is any zero-argument callable returning `bool`. It is called on
every `feature_enabled()` invocation, so keep it fast (no I/O).

**`declare_commercial(*names)`** — marks additional names as denied-by-default:

```python
declare_commercial("sso")          # denied until the billing code registers a checker
register_feature("sso", checker)   # billing code provides the checker separately
```

**`enforce_quota(org_id, dimension, amount)`** — async quota gate called before
metered operations (compute, AI calls, embedded sessions, flow runs). Without
billing switched on the quota checker is `None` and the call is a no-op — self-hosters
are never usage-limited. Metered dimensions: `"compute_units"`, `"ai_calls"`,
`"embedded_sessions"`, `"agent_runs"`, `"storage_gb"`. Raises
`AppError("quota_exceeded", ..., 402)` when the Nubi Cloud checker denies.

**`reset_for_tests()`** — clears the registry and restores the original
`_COMMERCIAL` set. Called by `conftest.py` between tests:

```python
from app.features import reset_for_tests
reset_for_tests()
```

### Frontend (`src/lib/features.js`)

The frontend gate mirrors the backend pattern. On first use it fetches
`GET /api/v1/features` (once, deduplicated across concurrent callers) and
populates a module-level `Set`. Until the fetch resolves it falls back to
self-host defaults synchronously — billing features `false`, everything else
`true`.

```js
import { useFeature, isFeatureEnabled } from '../lib/features.js'

// Inside a React component:
const hasBilling = useFeature('billing')   // false unless billing is switched on

// Outside React:
if (isFeatureEnabled('billing')) { ... }
```

`COMMERCIAL_FEATURES = new Set(['billing', 'paid_tiers'])` mirrors the backend
default-deny list.

`setEnabledFeatures(names)` is called by the `ee/` loader after it receives the
live feature set from the backend. All active `useFeature()` hooks re-render
automatically because `features.js` notifies registered listeners.

`useFeatureSet()` returns the full enabled `Set` — useful for debugging or
building a feature-flag inspector.

---

## Startup sequence

### Backend

`main.py` at app construction:

1. Mount all core routes (flows, query, connectors, ai, …).
2. Call `load_ee(app)`:
   - try `import app.ee.licensing` → resolve tier from the internal `NUBI_LICENSE_KEY` switch;
   - try `import app.ee.billing` → `setup(app)`: registers the `"billing"` / `"paid_tiers"` feature checkers, the quota checker (`enforce_quota` hook), and the `"fx_refresh"` task kind in the core flows registry, then mounts the billing routes onto the app;
   - return `True` (billing active) or `False` (billing off — every self-host deployment).
3. Log billing status; server ready.

FastAPI lifespan, after `init_db()` opens the asyncpg pool:

4. Call `ee_startup()` → `ensure_fx_refresh_flow_async()` — creates the daily FX-refresh scheduled flow (cron `0 5 * * *` UTC = 07:00 SAST) if absent. Idempotent: no-ops when `__nubi_fx_refresh__` already exists.

`setup()` runs at app construction before the DB pool exists; DB-backed work
happens in `ee_startup()` during the lifespan. `load_ee` is wrapped in
`try/except`, so even if `app/ee/` were ever absent from a build it fails
closed to billing-off rather than crashing the server.

### Frontend

`App.jsx` at mount:

1. Render all core routes.
2. Dynamic `import('./ee/index.js')`:
   - success → `registerEe()`: `_fetchAndApplyFeatures()` (background, async), then `registerBilling()` → `registerSlot('billing-page', …)`, `registerSlot('billing-nav-badge', …)`, `registerSlot('upgrade-prompt', …)`;
   - failure → `useFeature('billing')` stays `false`; core runs normally.

`src/ee/registry.js` is the one file inside `src/ee/` that core is permitted
to import — it is a thin, side-effect-free `Map` with no business logic. Core
reads slots via `getSlot(name)` and renders `null` when the billing UI hasn't registered one.

---

## Database migrations

`database/migrate.py` is the forward-only migration runner (asyncpg).

```bash
# core — apply core schema only (self-host default):
python database/migrate.py

# Nubi Cloud — apply core + billing/FX/wallet/invoices schema:
python database/migrate.py --ee
# or: NUBI_CLOUD=1 python database/migrate.py
# or: NUBI_EE=1   python database/migrate.py
```

Billing migrations live in `database/migrations/ee/` and are keyed in the
`schema_migrations` ledger as `ee/<file>` so they never collide with core
versions and always apply after core (so foreign keys to `orgs` etc. resolve).

Current billing migrations:

| File | Content |
|------|---------|
| `ee/0017_billing.sql` | Billing subscriptions schema |
| `ee/0018_fx_rates.sql` | FX rate cache table |
| `ee/0022_wallet.sql` | Prepaid credit wallet |
| `ee/0027_invoices.sql` | Invoice records |

Migrations that previously lived in `database/migrations/` and moved into
`database/migrations/ee/` (0017, 0018, 0022, 0027) are handled by a legacy
re-key pass: the runner updates the ledger row from the bare file name to
`ee/<file>` instead of re-applying, so already-deployed databases converge
without re-running DDL.

The runner holds `pg_advisory_lock(727274)` for the duration of each run,
serializing concurrent runners across replicas.

---

## Licensing

`backend/app/ee/licensing/license.py` resolves the internal `NUBI_LICENSE_KEY`
switch to a tier — this exists solely for Nubi's own Cloud deployment to tell
a running process which billing tier to enforce. There is no purchase flow or
storefront: self-hosters simply never set this variable.

| Key prefix | Tier | `is_paid` |
|---|---|---|
| absent or empty | `FREE` | `False` |
| `nubi_pro_…` | `PRO` | `True` |
| `nubi_enterprise_…` | `ENTERPRISE` | `True` |

The result is cached for process lifetime (`@lru_cache(maxsize=1)`). Call
`reset_license_cache()` in tests to clear it. Unrecognised keys map to `FREE`
(fail-open — a stale or wrong-environment key never locks anyone out of their
own server).

Feature checkers for `billing` and `paid_tiers` are registered by
`app.ee.billing.setup()` using `get_license().is_paid` as the predicate.

---

## Building images

### Self-host image

The standard `Dockerfile`s build the whole repo, `ee/` included — there's
nothing to strip out. With `NUBI_LICENSE_KEY` unset (the default), billing
stays off and the app runs as the full, free, unmetered product.

```bash
make up      # docker compose up --build -d  (three services: db, backend, frontend)
make smoke   # scripts/smoke.sh — health + auth + query round-trip
make down    # docker compose down -v
```

`docker-entrypoint.sh` applies pending **core** migrations (no `--ee`) then
starts uvicorn, so `make up` is a zero-config cold-start.

### Nubi Cloud image

Nubi's own Cloud deployment builds the same tree with `Dockerfile.ee`, applies
the `--ee` billing migrations, and sets `NUBI_LICENSE_KEY` as an internal
operations variable in its own infrastructure — this is not a step a
self-hoster performs. See [Nubi Cloud](/docs/cloud) for how Nubi runs it.

---

## Adding a new Cloud-only feature

### Backend

1. Pick a feature name, e.g. `"sso"`.
2. Create `backend/app/ee/sso/__init__.py`.
3. Call `declare_commercial("sso")` + `register_feature("sso", checker)`.
4. Wire into `load_ee()` with a `try/except` lazy import.
5. Gate core behaviour: `if feature_enabled("sso"): ...`
6. Write tests with `register_feature("sso", lambda: True/False)` and
   `reset_for_tests()` in teardown.

```python
# backend/app/ee/sso/__init__.py
from app.features import declare_commercial, register_feature
from app.ee.licensing.license import get_license

declare_commercial("sso")
register_feature("sso", lambda: get_license().is_enterprise)

def setup(app):
    from app.ee.sso import routes as sso_routes  # noqa: PLC0415
    app.include_router(sso_routes.router, prefix="/api/v1/sso")
```

### Frontend

1. Create `src/ee/sso/SsoSettings.jsx`.
2. In `src/ee/sso/registerSso.js` call `registerSlot("sso-settings", SsoSettings)`.
3. Import and call `registerSso()` from `src/ee/index.js` inside `registerEe()`.
4. In core: `const SsoPanel = getSlot("sso-settings") ?? null`.

---

## Related docs

| Doc | Audience |
|-----|---------|
| [Self-hosting guide](/docs/self-host) | Operators deploying the free, full OSS product |
| [Secrets](/docs/secrets) | `{{ secrets.NAME }}` in flows; `nubi secrets set/list` |
| [Flows](/docs/flows) | DAG engine reference (core) |
| [Embedding](/docs/embedding) | JWT trust boundary, origin pinning, RLS policies |
| [SDK and CLI](/docs/sdk-and-cli) | `nubi login / deploy / run / diff / pull` |
| [Connectors](/docs/connectors) | AES-256-GCM secret encryption, network modes |
| [Billing and usage](/docs/billing-and-usage) | Nubi Cloud billing (ZAR, Paystack, tiers) |
