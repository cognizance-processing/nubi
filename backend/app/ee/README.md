# Nubi EE (Enterprise Edition) package

This directory contains commercial features that are **not** part of the
open-source core (OSS) release.

## Open-core model

Nubi follows the **GitLab CE/EE model**:

| Tier | What ships |
|------|-----------|
| OSS core | Everything under `backend/app/` *except* `backend/app/ee/` |
| EE | Additional commercial features under `backend/app/ee/` |

The OSS build **must run fully without this directory**.  If `app/ee/` is
absent (e.g. in a self-hosted OSS deployment), `main.py` calls `load_ee()`
which catches the `ImportError` and returns `False` — no crash, no degraded
behaviour for the open features.

## The no-import rule

**Core must never import from `app.ee`.**

- Core code asks "is feature X enabled?" via `app.features.feature_enabled`.
- EE registers the answer at startup via `app.features.register_feature`.
- This one-way dependency is checked during code review and enforced by tests.

Violating this rule means the OSS build would fail to start when the `ee/`
tree is absent.

## Directory layout

```
backend/app/ee/
├── __init__.py          # load_ee() entry point — safe no-op if absent
├── README.md            # this file
└── licensing/
    ├── __init__.py
    └── license.py       # Tier enum + License dataclass + get_license()
```

Future commercial sub-packages (billing, SSO, advanced RBAC, etc.) will be
added as siblings of `licensing/` and wired into `load_ee()`.

## Adding a new EE sub-module

1. Create `backend/app/ee/<module>/`.
2. Register commercial feature names:
   ```python
   from app.features import register_feature, declare_commercial
   declare_commercial("my_feature")
   register_feature("my_feature", lambda: get_license().is_paid)
   ```
3. Wire a lazy import into `load_ee()` in `backend/app/ee/__init__.py`.
4. Add tests under `backend/tests/` (not in `ee/` — tests run in the OSS
   harness and must not assume EE is present).

## License

See `ee/LICENSE` at the repository root for the commercial license that
applies to this directory.
