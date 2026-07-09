# Nubi `ee/` package (billing / paid-tier code)

This directory holds Nubi's **billing and paid-tier code** (licensing, billing,
wallet, FX, invoicing, quota enforcement). It is **open** — it ships in this
repo under the same **Apache-2.0** license as the rest of Nubi. It is *not* a
separately-licensed "commercial edition": Nubi does not sell EE or self-hosting.

The `ee/` split is purely code organization + a runtime switch: this code stays
**inert** unless activated, which only happens in **Nubi Cloud** (our hosted
SaaS) via the `NUBI_LICENSE_KEY` operations secret. Self-hosters get the full
OSS product — free, billing off.

## OSS + Cloud

| | What it is |
|---|---|
| **Self-host (OSS)** | Everything, free, billing off. `app/ee/` can even be absent. |
| **Nubi Cloud** | The same code, deployed by us with `NUBI_LICENSE_KEY` set → billing/paid tiers activate. |

The OSS build **must run fully without this directory**. If `app/ee/` is absent
(or present but no license key), `main.py`'s `load_ee()` degrades gracefully —
no crash, no loss of the open features; the paid-tier gates just report off.

## The no-import rule

**Core must never import from `app.ee`.**

- Core code asks "is feature X enabled?" via `app.features.feature_enabled`.
- `ee/` registers the answer at startup via `app.features.register_feature`.
- This one-way dependency is checked in review and enforced by tests.

Violating it means the OSS build would fail to start when the `ee/` tree is absent.

## Directory layout

```
backend/app/ee/
├── __init__.py          # load_ee() entry point — safe no-op if absent
├── README.md            # this file
├── billing/             # wallet, FX, invoicing, tiers, quota, token-billing
└── licensing/
    ├── __init__.py
    └── license.py       # Tier enum + License dataclass + get_license()
```

## Adding a new `ee/` sub-module

1. Create `backend/app/ee/<module>/`.
2. Register the feature names:
   ```python
   from app.features import register_feature, declare_commercial
   declare_commercial("my_feature")   # marks it a paid-tier gate
   register_feature("my_feature", lambda: get_license().is_paid)
   ```
3. Wire a lazy import into `load_ee()` in `backend/app/ee/__init__.py`.
4. Add tests under `backend/tests/` (not in `ee/` — tests run in the OSS
   harness and must not assume the paid tier is active).

## License

This directory is covered by the repository's root **Apache-2.0 `LICENSE`**,
same as all Nubi code. There is no separate commercial license.
