# Open-Core Architecture

![CE core and EE layer: what ships in each image](illustration:OpenCoreSplit)

Nubi follows the **GitLab CE/EE convention**: the OSS core (CE) is MIT-licensed
and ships a fully functional analytics platform. The EE layer adds commercial
features and is loaded optionally at runtime. When the EE tree is absent the
server starts cleanly and OSS users lose nothing except commercial features.

The **core invariant** is enforced by one rule: core code never imports from
`app.ee` or `src/ee/`. That single boundary is what lets the CE image be
shipped without the EE tree.

---

## CE vs EE split

### Backend

| Area | CE — `backend/app/` | EE — `backend/app/ee/` |
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
| Licensing resolution | — | `app.ee.licensing` — tier from `NUBI_LICENSE_KEY` |
| Billing + Paystack | — | `app.ee.billing` — routes, store, Paystack client, tiers, FX, wallet, quota |
| Paid-tier quota enforcement | `enforce_quota()` hook in core (no-op without EE) | EE registers quota checker |

### Frontend

| Area | CE — `src/` | EE — `src/ee/` |
|------|-------------|----------------|
| Dashboard editor | `src/editor/` | — |
| Query workspace | `src/pages/app/QueryWorkspace.jsx` | — |
| Connectors page | `src/pages/app/ConnectorsPage.jsx` | — |
| Settings page | `src/pages/app/SettingsPage.jsx` | — |
| Feature-flag hook | `src/lib/features.js` — `useFeature()` / `isFeatureEnabled()` | — |
| EE slot registry | — | `src/ee/registry.js` — `registerSlot()` / `getSlot()` |
| EE entry point | — | `src/ee/index.js` — `registerEe()` |
| Billing UI | — | `src/ee/billing/` — BillingPage, UpgradePrompt, BillingNavBadge |

---

## Repository layout

```
nubi/
├── backend/
│   └── app/
│       ├── features.py          ← feature-gate seam (CE)
│       ├── flows/               ← DAG engine (CE)
│       ├── preagg/              ← auto pre-aggregations (CE)
│       ├── connectors/          ← connector registry + encryption (CE)
│       └── ee/                  ← EE package (commercial, optional)
│           ├── __init__.py      ← load_ee() + ee_startup()
│           ├── licensing/       ← license resolution from NUBI_LICENSE_KEY
│           └── billing/         ← Paystack billing, tiers, FX, wallet, quota
├── src/
│   ├── lib/features.js          ← frontend feature-flag store (CE)
│   └── ee/                      ← EE frontend (commercial, optional)
│       ├── index.js             ← registerEe()
│       ├── registry.js          ← slot registry
│       └── billing/             ← billing UI components + registerBilling.js
├── database/migrations/         ← zero-padded core SQL migrations
├── database/migrations/ee/      ← EE-only migrations (billing, FX, wallet, invoices)
├── docker-compose.yml           ← CE community self-host stack
├── backend/Dockerfile           ← CE backend (ee/ excluded via .dockerignore)
├── frontend/Dockerfile          ← CE frontend (src/ee/ excluded via .dockerignore)
├── .dockerignore                ← strips backend/app/ee/ and src/ee/ from OSS image
├── scripts/smoke.sh             ← health + auth + query smoke test
├── examples/embed-demo/         ← self-contained embed demo
├── ee/LICENSE                   ← commercial licence placeholder
└── LICENSE                      ← MIT licence (CE)
```

---

## Feature-gate API

### Backend (`backend/app/features.py`)

Four public functions form the entire gate contract.

**`feature_enabled(name)`** — called by core at request time:

```python
from app.features import feature_enabled

if feature_enabled("billing"):
    ...  # only reached in an EE/paid deployment
```

Default behaviour when no checker is registered:

| Feature name | No EE | EE present + checker truthy |
|---|---|---|
| `"billing"`, `"paid_tiers"` | `False` | `True` |
| Any other name | `True` | `True` |

`"billing"` and `"paid_tiers"` are hard-coded in `_COMMERCIAL` at module load.
Additional names can be added by EE sub-modules via `declare_commercial()`.
A broken checker fails silently and returns `False` — a billing fault never
takes down request handling.

**`register_feature(name, checker)`** — called by EE at startup, never by core:

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
declare_commercial("sso")          # denied until EE registers a checker
register_feature("sso", checker)   # EE provides the checker separately
```

**`enforce_quota(org_id, dimension, amount)`** — async quota gate called before
metered operations (compute, AI calls, embedded sessions, flow runs). Without
EE the quota checker is `None` and the call is a no-op — OSS self-hosters are
never usage-limited. Metered dimensions: `"compute_units"`, `"ai_calls"`,
`"embedded_sessions"`, `"agent_runs"`, `"storage_gb"`. Raises
`AppError("quota_exceeded", ..., 402)` when the EE checker denies.

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
OSS defaults synchronously — commercial features `false`, everything else
`true`.

```js
import { useFeature, isFeatureEnabled } from '../lib/features.js'

// Inside a React component:
const hasBilling = useFeature('billing')   // false in CE build

// Outside React:
if (isFeatureEnabled('billing')) { ... }
```

`COMMERCIAL_FEATURES = new Set(['billing', 'paid_tiers'])` mirrors the backend
default-deny list.

`setEnabledFeatures(names)` is called by the EE loader after it receives the
live feature set from the backend. All active `useFeature()` hooks re-render
automatically because `features.js` notifies registered listeners.

`useFeatureSet()` returns the full enabled `Set` — useful for debugging or
building a feature-flag inspector.

---

## Startup sequence

### Backend

`main.py` at app construction:

1. Mount all CE routes (flows, query, connectors, ai, …).
2. Call `load_ee(app)`:
   - try `import app.ee.licensing` → resolve tier from `NUBI_LICENSE_KEY`;
   - try `import app.ee.billing` → `setup(app)`: registers the `"billing"` / `"paid_tiers"` feature checkers, the quota checker (`enforce_quota` hook), and the `"fx_refresh"` task kind in the core flows registry, then mounts the EE billing routes onto the app;
   - return `True` (EE active) or `False` (OSS only).
3. Log EE status; server ready.

FastAPI lifespan, after `init_db()` opens the asyncpg pool:

4. Call `ee_startup()` → `ensure_fx_refresh_flow_async()` — creates the daily FX-refresh scheduled flow (cron `0 5 * * *` UTC = 07:00 SAST) if absent. Idempotent: no-ops when `__nubi_fx_refresh__` already exists.

`setup()` runs at app construction before the DB pool exists; DB-backed work
happens in `ee_startup()` during the lifespan. If `app/ee/` is absent,
`load_ee` catches `ImportError` and returns `False` silently.

### Frontend

`App.jsx` at mount:

1. Render all CE routes.
2. Dynamic `import('./ee/index.js')`:
   - success → `registerEe()`: `_fetchAndApplyFeatures()` (background, async), then `registerBilling()` → `registerSlot('billing-page', …)`, `registerSlot('billing-nav-badge', …)`, `registerSlot('upgrade-prompt', …)`;
   - failure → `useFeature('billing')` stays `false`; CE runs normally.

`src/ee/registry.js` is the one file inside `src/ee/` that core is permitted
to import — it is a thin, side-effect-free `Map` with no business logic. Core
reads slots via `getSlot(name)` and renders `null` when EE is absent.

---

## Database migrations

`database/migrate.py` is the forward-only migration runner (asyncpg).

```bash
# CE — apply core schema only:
python database/migrate.py

# EE — apply core + EE billing/FX/wallet/invoices schema:
python database/migrate.py --ee
# or: NUBI_CLOUD=1 python database/migrate.py
# or: NUBI_EE=1   python database/migrate.py
```

EE migrations live in `database/migrations/ee/` and are keyed in the
`schema_migrations` ledger as `ee/<file>` so they never collide with core
versions and always apply after core (so foreign keys to `orgs` etc. resolve).

Current EE migrations:

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

`backend/app/ee/licensing/license.py` resolves `NUBI_LICENSE_KEY` to a tier:

| Key prefix | Tier | `is_paid` |
|---|---|---|
| absent or empty | `FREE` | `False` |
| `nubi_pro_…` | `PRO` | `True` |
| `nubi_enterprise_…` | `ENTERPRISE` | `True` |

The result is cached for process lifetime (`@lru_cache(maxsize=1)`). Call
`reset_license_cache()` in tests to clear it. Unrecognised keys map to `FREE`
(fail-open — a self-hoster with a stale or wrong-environment key is not locked
out of their own server).

Feature checkers for `billing` and `paid_tiers` are registered by
`app.ee.billing.setup()` using `get_license().is_paid` as the predicate.

---

## Building images

### CE image

`.dockerignore` excludes `backend/app/ee/` and `src/ee/` from the Docker build
context before any `COPY` instruction runs. EE code never lands in the CE image.

```bash
make up      # docker compose up --build -d  (three services: db, backend, frontend)
make smoke   # scripts/smoke.sh — health + auth + query round-trip
make down    # docker compose down -v
```

`docker-entrypoint.sh` applies pending **core** migrations (no `--ee`) then
starts uvicorn, so `make up` is a zero-config cold-start.

### EE image

The EE image uses the same Dockerfiles with the EE trees present in the build
context (`.dockerignore` exclusions removed or overridden):

```bash
DOCKER_BUILDKIT=1 docker build \
  --secret id=ee_src,src=./backend/app/ee \
  -f backend/Dockerfile \
  -t nubi-backend-ee .

docker run -e NUBI_LICENSE_KEY=nubi_pro_... nubi-backend-ee
```

---

## Adding a new EE feature

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
| [Self-hosting guide](/docs/self-host) | Operators deploying CE |
| [Secrets](/docs/secrets) | `{{ secrets.NAME }}` in flows; `nubi secrets set/list` |
| [Flows](/docs/flows) | DAG engine reference (CE) |
| [Embedding](/docs/embedding) | JWT trust boundary, origin pinning, RLS policies |
| [SDK and CLI](/docs/sdk-and-cli) | `nubi login / deploy / run / diff / pull` |
| [Connectors](/docs/connectors) | AES-256-GCM secret encryption, network modes |
| [Billing and usage](/docs/billing-and-usage) | EE/Cloud billing (ZAR, Paystack, tiers) |
