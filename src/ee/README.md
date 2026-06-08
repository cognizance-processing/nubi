# src/ee — EE Frontend Module

This directory contains commercial/EE-only frontend code for Nubi.

## Open-Core Boundary

Core (`src/`) MUST NOT statically import from `src/ee/` (except `src/ee/registry.js`,
which is a thin, side-effect-free seam).  The EE module is loaded dynamically at
runtime via `_tryLoadEe()` in `App.jsx`.  If the EE directory is absent or the
dynamic import fails, the OSS build continues normally with all commercial
features disabled.

## Files

| File | Purpose |
|------|---------|
| `index.js` | Entry point — exports `registerEe()`, which wires EE components into the slot registry and sets the feature-flag store. |
| `registry.js` | Extension-point slot registry. Core reads slots; EE writes them. No business logic here. |
| `README.md` | This file. |

## Adding an EE Component (BillingFrontendAgent / Phase 2)

1. Create your component file in `src/ee/`, e.g. `src/ee/BillingPage.jsx`.
2. Import it inside `src/ee/index.js` (static imports are fine — the whole file is lazy from core).
3. Inside `registerEe()`, call `registerSlot('billing-page', BillingPage)`.
4. The `/billing` route in `App.jsx` will automatically render it when the `billing` feature is enabled.

## Known Slot Names

| Slot | Description |
|------|-------------|
| `billing-page` | Full-page billing UI (routed at `/billing`) |
| `billing-nav-badge` | Small plan/badge chip for the nav sidebar |
| `upgrade-prompt` | Inline upgrade CTA block |

## Feature Flags

Commercial feature names: `billing`, `paid_tiers`

- In OSS builds (no EE module), these default to **false**.
- When the EE module loads and calls `registerEe()`, it calls `setEnabledFeatures()`
  to enable them.
- Phase 2: `registerEe()` should fetch `/api/v1/ee/features` (or similar) and
  call `setEnabledFeatures()` with the server-confirmed list instead of the
  static default.

## Backend Seam

The backend feature gate is in `backend/app/features.py` (created by EeSeamAgent).
The frontend reads `GET /api/v1/features` → `{ features: string[] }`.
On 404 or failure, `src/lib/features.js` falls back to OSS defaults.
