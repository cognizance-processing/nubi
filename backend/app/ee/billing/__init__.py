"""EE billing sub-package for Nubi.

This package is loaded lazily by :func:`app.ee.load_ee` — never imported
directly by open-source core code.

Responsibilities
----------------
1. Declares ``billing`` and ``paid_tiers`` as commercial feature names.
2. Registers feature checkers via ``app.features.register_feature`` so that
   :func:`app.features.feature_enabled` returns the correct value for a
   given deployment's tier.
3. Mounts billing API routes onto the FastAPI app when :func:`setup` is
   called from ``load_ee``.
4. Registers the ``'fx_refresh'`` task kind in the flows registry so that
   the daily FX rate refresh can be triggered as a scheduled flow.
5. Creates (or no-ops if already exists) a daily FX refresh scheduled flow
   via the flow store — cron ``0 5 * * *`` UTC (07:00 SAST).

The feature checkers read the active subscription from the billing store so
that tier upgrades take effect without a server restart.

Usage (inside load_ee)
----------------------
::

    try:
        from app.ee.billing import setup as billing_setup  # noqa: PLC0415
        billing_setup(app)
        loaded.append("billing")
    except Exception as exc:
        logger.warning("Nubi EE: billing module failed to load — %s", exc)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Cron expression for daily FX rate refresh: 05:00 UTC = 07:00 SAST.
_FX_REFRESH_CRON = "0 5 * * *"

# Sentinel flow name — used to avoid creating duplicate flows across restarts.
_FX_REFRESH_FLOW_NAME = "__nubi_fx_refresh__"


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------


def _register_billing_features() -> None:
    """Register billing and paid_tier feature checkers in core feature registry."""
    from app.ee.billing.store import get_billing_store  # noqa: PLC0415,F401
    from app.ee.billing.tiers import BillingTier  # noqa: PLC0415,F401
    from app.features import register_feature  # noqa: PLC0415

    # We use the license module as a fast path for the feature check so we
    # don't need an async DB call inside a sync feature checker.  The
    # license module already resolves the tier from NUBI_LICENSE_KEY.
    try:
        from app.ee.licensing.license import get_license  # noqa: PLC0415

        def _billing_enabled() -> bool:
            return get_license().is_paid

        def _paid_tiers_enabled() -> bool:
            return get_license().is_paid

        register_feature("billing", _billing_enabled)
        register_feature("paid_tiers", _paid_tiers_enabled)
        logger.debug("EE billing: feature checkers registered via license")
    except Exception as exc:  # noqa: BLE001
        # Fallback: register a permissive checker so that billing UI is
        # accessible even without a valid license key (manual / trial mode).
        logger.warning(
            "EE billing: could not read license, billing features disabled — %s", exc
        )

        def _denied() -> bool:
            return False

        register_feature("billing", _denied)
        register_feature("paid_tiers", _denied)


# ---------------------------------------------------------------------------
# Usage-quota checker registration
# ---------------------------------------------------------------------------


def _register_quota_checker() -> None:
    """Register the EE usage-quota checker into the core enforcement hook.

    Core call sites (compute, flows, AI, embed) call
    ``app.features.enforce_quota`` before metered operations; without this
    registration the hook is a no-op (OSS build: no quotas).  The checker
    resolves the org's subscription tier and applies the canonical billing
    model: dimensions with an overage rate are allow-and-meter; dimensions
    without one (e.g. everything on FREE) hard-stop at the tier quota.
    """
    try:
        from app.ee.billing.quota import register_quota_checker  # noqa: PLC0415

        register_quota_checker()
        logger.debug("EE billing: quota checker registered")
    except Exception as exc:  # noqa: BLE001
        logger.warning("EE billing: could not register quota checker — %s", exc)


# ---------------------------------------------------------------------------
# FX refresh task registration
# ---------------------------------------------------------------------------


def _register_fx_refresh_task() -> None:
    """Register the ``'fx_refresh'`` task kind in the core flows registry.

    This is a runtime registration — EE registers into the core registry
    without modifying any core source files.  The core registry is already
    instantiated; we simply call ``.register()`` on it.

    Safe to call multiple times (second call overwrites with the same handler,
    which is harmless).
    """
    try:
        from app.ee.billing.fx import _fx_refresh_handler  # noqa: PLC0415
        from app.flows.registry import get_task_kind_registry  # noqa: PLC0415

        registry = get_task_kind_registry()
        registry.register("fx_refresh", _fx_refresh_handler)
        logger.debug("EE billing: 'fx_refresh' task kind registered in flows registry")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "EE billing: could not register fx_refresh task kind — %s", exc
        )


async def ensure_fx_refresh_flow_async() -> None:
    """Create the daily FX-refresh scheduled flow if it does not already exist.

    DB-backed: must be awaited at application STARTUP, after ``init_db()`` has
    opened the asyncpg pool.  Invoked by :func:`app.ee.ee_startup` from the
    FastAPI lifespan — NOT from :func:`setup` (which runs at app construction,
    before the pool exists).  Idempotent: a flow named ``__nubi_fx_refresh__``
    is created at most once.  Cron ``0 5 * * *`` UTC = 07:00 SAST.
    """
    try:
        from app.flows.store import get_flow_store  # noqa: PLC0415

        store = get_flow_store()

        try:  # avoid duplicates across restarts
            for flow in await store.list_flows(org_id="__system__"):
                if flow.get("name") == _FX_REFRESH_FLOW_NAME:
                    logger.debug(
                        "EE billing: fx_refresh flow already exists (id=%s)",
                        flow.get("id"),
                    )
                    return
        except Exception:  # noqa: BLE001
            pass  # list_flows may be unavailable on some store impls

        spec = {
            "version": 1,
            "name": _FX_REFRESH_FLOW_NAME,
            "params": [],
            "tasks": [
                {"key": "refresh", "kind": "fx_refresh", "needs": [], "config": {}}
            ],
        }
        flow = await store.create_flow(
            org_id="__system__",
            created_by="__system__",
            name=_FX_REFRESH_FLOW_NAME,
            spec=spec,
            schedule=_FX_REFRESH_CRON,
            enabled=True,
        )
        logger.info(
            "EE billing: daily fx_refresh flow created (id=%s, cron=%s)",
            flow.get("id"),
            _FX_REFRESH_CRON,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("EE billing: could not create fx_refresh flow — %s", exc)


# ---------------------------------------------------------------------------
# Public setup entry point
# ---------------------------------------------------------------------------


def setup(app: Any | None = None) -> None:
    """Initialise the billing sub-module.

    Called from :func:`app.ee.load_ee` with the FastAPI application instance.
    Safe to call with ``app=None`` in worker processes that do not host HTTP.

    Steps
    -----
    1. Register billing feature checkers so ``feature_enabled("billing")``
       works correctly for this deployment tier.
    2. Register the ``'fx_refresh'`` task kind in the core flows registry so
       that the daily FX refresh flow can execute.
    3. Ensure the daily FX refresh scheduled flow exists in the flow store.
    4. Mount billing API routes onto *app* (if provided).

    Parameters
    ----------
    app:
        FastAPI application instance, or ``None`` for non-HTTP worker use.
    """
    _register_billing_features()
    _register_quota_checker()
    _register_fx_refresh_task()
    # The daily FX-refresh flow is DB-backed, so it is created at app STARTUP
    # (after init_db()) via ensure_fx_refresh_flow_async() — awaited by
    # app.ee.ee_startup() from the FastAPI lifespan — NOT here.  setup() runs at
    # app construction, before the asyncpg pool exists.

    if app is not None:
        try:
            from app.ee.billing.routes import setup as mount_routes  # noqa: PLC0415

            mount_routes(app)
        except Exception as exc:  # noqa: BLE001
            logger.warning("EE billing: failed to mount routes — %s", exc)
