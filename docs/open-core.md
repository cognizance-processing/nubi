# Nubi Open-Core Architecture

![OSS core vs EE: how the two trees slot together](illustration:OpenCoreSplit)

Nubi follows the **GitLab CE/EE model**. The OSS core is fully functional under the MIT license. Commercial features live in a separate `ee/` tree that slots in at startup without the core ever importing from it.

---

## Tiers at a glance

| Tier | License | Included in OSS repo |
|------|---------|----------------------|
| OSS Core (CE) | MIT (`LICENSE`) | Yes — flows, connectors, query engine, dashboards, embedding, self-host, git-sync |
| EE | Commercial (`ee/LICENSE`) | Yes (tree ships in the repo) — billing, Paystack, wallet, FX, invoices, quota enforcement |

Without a valid `NUBI_LICENSE_KEY`, the EE tree still loads and mounts its routes, but all commercial feature checks (`feature_enabled("billing")`, `feature_enabled("paid_tiers")`) return `False`. An OSS self-hosted deployment that omits the EE tree entirely (EE files absent) never loads it; one that ships the full repo loads EE in restricted mode.

---

## Repository layout

```
nubi/
├── backend/
│   ├── main.py                    ← calls load_ee() at startup (try/except)
│   └── app/
│       ├── features.py            ← feature-gate registry (OSS, no EE import)
│       ├── routes/                ← OSS routes
│       └── ee/                    ← EE backend (commercial)
│           ├── __init__.py        ← load_ee() + ee_startup() entry points
│           ├── licensing/
│           │   └── license.py     ← Tier enum + NUBI_LICENSE_KEY resolution
│           └── billing/
│               ├── tiers.py       ← BillingTier limits, overage rates
│               ├── quota.py       ← quota enforcement
│               ├── paystack.py    ← Paystack integration
│               ├── wallet.py      ← prepaid credit wallet
│               ├── fx.py          ← USD→ZAR FX conversion
│               ├── invoice.py     ← invoice generation
│               └── routes.py      ← /ee/billing/** routes (mounted by load_ee)
├── src/
│   ├── lib/features.js            ← OSS feature-flag store
│   └── ee/                        ← EE frontend (commercial)
│       ├── index.js               ← registerEe() dynamic import entry
│       ├── registry.js            ← slot registry
│       └── billing/               ← billing UI components
│           ├── registerBilling.js
│           ├── BillingPage.jsx
│           ├── WalletPanel.jsx
│           ├── PricingCalculator.jsx
│           ├── UpgradePrompt.jsx
│           └── FxNotice.jsx
├── database/
│   └── migrations/
│       ├── *.sql                  ← OSS core schema (applied by default)
│       └── ee/
│           ├── 0017_billing.sql
│           ├── 0018_fx_rates.sql
│           ├── 0022_wallet.sql
│           └── 0027_invoices.sql  ← EE schema (--ee / NUBI_EE=1 only)
├── ee/
│   └── LICENSE                    ← Commercial license
└── LICENSE                        ← MIT license (core)
```

---

## The no-import rule

**OSS core code must never import from `app.ee` or `src/ee/`.**

This is the single most important rule. Violating it means the OSS build fails to start when the EE tree is absent. The rule is enforced by code review and the test suite (which must pass with no EE env vars set).

1. The OSS core never imports `app.ee`. It only asks the gate: `feature_enabled("billing")` → `False` (commercial: deny by default), `feature_enabled("flows")` → `True` (OSS feature: allow by default).
2. At startup, `load_ee()` lazy-imports the EE sub-modules (when present); each one calls `register_feature()` to plug in its checker, and `load_ee()` mounts the `/ee/**` routes.

EE routes (e.g. `/ee/billing`) are mounted by `load_ee()` from inside the EE tree. Core never calls `application.include_router` for any EE router.

---

## Feature gate

`backend/app/features.py` is the single source of truth for "is feature X available?". Core code calls `feature_enabled()`; EE code calls `register_feature()` at startup.

```python
# OSS core — ask whether a feature is available
from app.features import feature_enabled

if feature_enabled("billing"):
    ...  # only reached in EE/cloud deployments
```

```python
# EE code — register a checker at startup (called from load_ee())
from app.features import register_feature, declare_commercial

declare_commercial("my_feature")          # deny by default unless EE registers it
register_feature("my_feature", checker)   # checker: () -> bool
```

Key design choices verified in `backend/app/features.py`:

- **Commercial feature names** (`"billing"`, `"paid_tiers"`, and any name passed to `declare_commercial()`) **default to `False`** unless EE registers a passing checker.
- **All other feature names default to `True`** — OSS users get every non-commercial feature without configuration.
- A checker that raises is caught and treated as `False`; a broken billing module never crashes request handling.

### Quota enforcement

Core routes call `enforce_quota()` before metered operations (AI calls, embed sessions, compute). In an OSS build no quota checker is registered, so the call is a no-op (allow all). EE billing registers an async checker via `register_quota_checker()`.

```python
from app.features import enforce_quota

await enforce_quota(org_id, "ai_calls", amount=1.0)
# → no-op in OSS; raises AppError("quota_exceeded", ..., 402) when EE denies
```

---

## Startup sequence

### OSS build (EE tree absent or `NUBI_LICENSE_KEY` unset)

`backend/main.py` wraps the EE load in `try/except`:

```python
try:
    from app.ee import load_ee        # noqa: PLC0415
    _ee_loaded = load_ee(application)
    if not _ee_loaded:
        logger.debug("Running in OSS mode (commercial features disabled)")
except Exception as _ee_exc:
    logger.warning("Nubi EE loader raised an unexpected error (non-fatal, OSS mode): %s", _ee_exc)
```

When the EE tree is absent the `ImportError` is caught; the server logs at DEBUG and continues. All commercial features remain `False`; all OSS features work normally.

### EE build (tree present, valid `NUBI_LICENSE_KEY`)

1. `main.py` calls `load_ee(app)`.
2. `load_ee` lazy-imports **licensing** first (determines active tier), then **billing** (registers checkers, mounts `/ee/billing` routes). Each sub-module is wrapped in its own `try/except` so one broken sub-module does not abort the rest.
3. `load_ee` returns `True` and logs which sub-modules loaded.
4. After the DB pool is ready, the FastAPI lifespan calls `await ee_startup()`, which schedules the FX-refresh flow.

---

## Database migrations

```bash
# OSS core schema only (default — no billing tables)
python database/migrate.py

# EE schema included (billing, FX, wallet, invoices)
python database/migrate.py --ee
# or: NUBI_EE=1 python database/migrate.py
# or: NUBI_CLOUD=1 python database/migrate.py
```

`database/migrate.py` applies core migrations (`migrations/*.sql`) by default. EE migrations (`migrations/ee/*.sql`) are applied only when `--ee` is passed or `NUBI_CLOUD=1` / `NUBI_EE=1` is set. EE versions are keyed as `ee/<file>` in the `schema_migrations` ledger so they never collide with core versions and always run after core (FKs to `orgs` and other core tables resolve).

| File | Schema contents |
|------|-----------------|
| `ee/0017_billing.sql` | Subscriptions, billing events |
| `ee/0018_fx_rates.sql` | USD→ZAR FX rate cache |
| `ee/0022_wallet.sql` | Prepaid credit wallet + ledger |
| `ee/0027_invoices.sql` | Invoice records |

---

## License key resolution

The EE tier is resolved from `NUBI_LICENSE_KEY` (`backend/app/ee/licensing/license.py`):

| Key prefix | Tier |
|------------|------|
| *(absent / empty / unrecognised)* | FREE (OSS) |
| `nubi_pro_...` | PRO |
| `nubi_enterprise_...` | ENTERPRISE |

The `Tier` enum (`FREE / PRO / ENTERPRISE`) is the license-level concept. The billing sub-module defines a separate `BillingTier` enum (`FREE / STARTER / TEAM / PRO / ENTERPRISE`) for quota limits and overage rates. STARTER and TEAM tiers are activated through the billing flow rather than a license-key prefix. The two enums are bridged by `billing_tier_from_license_tier()` in `backend/app/ee/billing/tiers.py`.

The current key-prefix scheme is intentionally simple and will be replaced by signed-JWT validation in a future release.

---

## Adding a commercial feature

1. Pick a feature name, e.g. `"sso"`.
2. In your EE sub-module `__init__.py`, call `declare_commercial("sso")` and `register_feature("sso", checker)`.
3. Wire a lazy import of the sub-module into `load_ee()` in `backend/app/ee/__init__.py`.
4. In OSS core, gate the behaviour with `feature_enabled("sso")`.
5. Write tests using `register_feature("sso", lambda: True/False)` — the test suite runs with no EE env vars.

---

## Frontend EE slot system

Core (`App.jsx`) never statically imports anything from `src/ee/`. It attempts a **dynamic import** of `src/ee/index.js`:

```js
// App.jsx (core) — never a static import
const { registerEe } = await import('./ee/index.js')
registerEe()
```

`registerEe()` (`src/ee/index.js`) does two things:

1. Fetches `GET /api/v1/features` and calls `setEnabledFeatures()` so the React feature-flag store reflects the backend's live state.
2. Calls `registerBilling()`, which calls `registerSlot()` for billing UI slots (`billing-page`, `billing-nav-badge`, `upgrade-prompt`).

In an OSS build where `src/ee/` is absent the dynamic import rejects silently; OSS components that call `useFeature("billing")` get `false` and render nothing.

---

## See also

- [Architecture: Open-Core](/docs/architecture-open-core) — CE vs EE feature table, Docker build, tier mapping
- [Billing & Usage](/docs/billing-and-usage) — tiers, pricing, wallet (Nubi Cloud / EE only)
- [Self-Host](/docs/self-host) — Docker Compose deployment guide
