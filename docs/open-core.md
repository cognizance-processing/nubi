# Nubi Open-Core Architecture

Nubi follows the **GitLab CE/EE model**: a fully functional open-source core
(CE) complemented by optional commercial features (EE) that slot in without
modifying the core.

## Tiers at a glance

| Tier | License | What you get |
|------|---------|-------------|
| **OSS Core** | MIT (root `LICENSE`) | Flows, connectors, query engine, dashboards, embedding, self-host, git-sync |
| **EE** | Commercial (see `ee/LICENSE`) | Billing, paid-tier enforcement, advanced RBAC, SSO, and future commercial features |

## Repository layout

```
nubi/
├── backend/
│   └── app/
│       ├── ...              ← OSS core (MIT)
│       └── ee/              ← EE backend modules (commercial)
│           ├── __init__.py  ← load_ee() entry point
│           └── licensing/   ← license model + tier resolution
├── src/
│   ├── ...                  ← OSS frontend (MIT)
│   └── ee/                  ← EE frontend modules (commercial)
│       ├── index.js         ← registerEe() + useFeature()
│       └── registry.js      ← EE component slot registry
├── ee/
│   └── LICENSE              ← Commercial license placeholder
└── LICENSE                  ← MIT license (core)
```

## The no-import rule

**Core code must never import from `app.ee` or `src/ee/`.**

This is the single most important rule.  Violating it means the OSS build
fails to start when the EE tree is absent.

### How it works

```
┌─────────────────────────────────────────┐
│              OSS Core                   │
│                                         │
│  feature_enabled("billing") → False     │  ← default deny for commercial
│  feature_enabled("flows")   → True      │  ← default allow for OSS features
│                                         │
│  No import from app.ee                  │
└────────────────┬────────────────────────┘
                 │ register_feature() at startup
                 ▼
┌─────────────────────────────────────────┐
│              EE Package                 │
│                                         │
│  load_ee() → imports ee sub-modules     │
│           → registers checkers          │
│           → returns True/False          │
└─────────────────────────────────────────┘
```

### Feature gate API

```python
# Core code — ask whether a feature is available:
from app.features import feature_enabled

if feature_enabled("billing"):
    # run billing-specific logic
    ...

# EE code — register a checker at startup:
from app.features import register_feature

register_feature("billing", lambda: get_license().is_paid)
```

The feature gate lives in `backend/app/features.py`.  The key design choices:

- **Non-commercial features default to `True`** so OSS users get everything
  without configuration.
- **Commercial feature names** (`"billing"`, `"paid_tiers"`) **default to
  `False`** unless EE registers a passing checker.
- A broken checker (raises an exception) is caught and treated as `False`
  rather than crashing request handling.

## OSS build without the EE tree

When the `backend/app/ee/` directory is absent (e.g. a `pip install nubi`
from the OSS repo without the EE extras):

1. `main.py` calls `load_ee(app)` at startup.
2. `load_ee` catches the `ImportError` (the `app.ee` package does not exist)
   and returns `False`.
3. All commercial features remain disabled (`feature_enabled("billing")` →
   `False`).
4. All OSS features work as normal.
5. No error is logged; the server starts cleanly.

## EE startup sequence

When the EE tree *is* present:

1. `main.py` calls `load_ee(app)`.
2. `load_ee` lazy-imports each EE sub-module inside `try/except`.
3. Each sub-module calls `register_feature` to plug in its checker.
4. `load_ee` returns `True` and logs which sub-modules loaded.
5. Core code can now call `feature_enabled("billing")` and get `True` (if
   the license key supports it).

## Adding a commercial feature

1. Decide on a feature name (e.g. `"sso"`).
2. In your EE sub-module, call `declare_commercial("sso")` and
   `register_feature("sso", checker)`.
3. Wire the import into `load_ee()` in `backend/app/ee/__init__.py`.
4. In the OSS core, gate the behaviour with `feature_enabled("sso")`.
5. Write tests using `register_feature("sso", lambda: True/False)` — tests
   run in the OSS harness with no EE env vars.

## License key resolution

EE tiers are resolved from the `NUBI_LICENSE_KEY` environment variable:

| Key prefix | Tier |
|------------|------|
| *(absent / empty)* | FREE (OSS) |
| `nubi_pro_...` | PRO |
| `nubi_enterprise_...` | ENTERPRISE |

The resolution logic lives in `backend/app/ee/licensing/license.py`.  It is
intentionally simple and will be replaced by cryptographic signature
verification in a future release.

---

## See also

For the full CE/EE architecture overview — including the CE-vs-EE feature
table, Docker image build instructions, and how the auto pre-aggregations,
Docker self-host, embed demo, and billing features map onto each tier — see
[`docs/architecture-open-core.md`](./architecture-open-core.md).
