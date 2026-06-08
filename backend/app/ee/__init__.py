"""Nubi EE (Enterprise Edition) package.

This package is **optional** — the open-source core works fully without it.
``main.py`` calls :func:`load_ee` once at startup; when this package is
absent, misconfigured, or its dependencies are missing, :func:`load_ee`
returns ``False`` silently and the OSS build continues unaffected.

Architecture
------------
- Core (``app/``) never imports from ``app.ee``.  That rule is enforced via
  code review and the test suite (which must pass with no EE env vars set).
- EE sub-packages register their feature checkers by calling
  ``app.features.register_feature`` at import time.
- :func:`load_ee` is the single entry point; it lazy-imports EE sub-modules
  so that missing optional dependencies (e.g. ``stripe``, ``paystack``) never
  crash the OSS server.

Adding a new EE sub-module
--------------------------
1. Create the sub-module under ``app/ee/`` (e.g. ``app/ee/billing/``).
2. In its ``__init__.py``, call ``register_feature("my_feature", checker)``
   and declare any additional commercial feature names via
   ``declare_commercial("my_feature")``.
3. Add a lazy import of the sub-module inside :func:`load_ee` below,
   wrapped in a ``try/except`` block so that a missing optional dep only
   skips that sub-module rather than taking down the whole EE loader.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def load_ee(app: Any | None = None) -> bool:  # noqa: ANN001
    """Load all EE sub-modules and register their feature checkers.

    This is a **safe no-op** when:
    - The ``app.ee`` tree is absent from the deployment (import error).
    - Required EE configuration (e.g. ``NUBI_LICENSE_KEY``) is not set.
    - Any EE sub-module fails to import (logged at WARNING, not re-raised).

    Parameters
    ----------
    app:
        The FastAPI application instance.  Passed through to EE sub-modules
        that need to mount additional routes or middleware.  May be ``None``
        when called from a worker process that does not host an HTTP server.

    Returns
    -------
    bool
        ``True`` when at least one EE sub-module loaded successfully.
        ``False`` when EE is absent or entirely misconfigured.
    """
    loaded: list[str] = []

    # ── Licensing ─────────────────────────────────────────────────────────────
    # The licensing module is always the first to load: it determines the active
    # tier so that subsequent EE sub-modules can gate themselves on the tier.
    # Feature checkers for ``billing`` / ``paid_tiers`` are registered once by
    # the billing sub-module below — NOT here — to avoid double-registration.
    try:
        from app.ee.licensing.license import get_license  # noqa: PLC0415

        license_obj = get_license()
        loaded.append("licensing")
        logger.debug("Nubi EE: licensing loaded (tier=%s)", license_obj.tier.value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nubi EE: licensing module failed to load — %s", exc)
        return False

    # ── Billing ───────────────────────────────────────────────────────────────
    try:
        from app.ee.billing import setup as _billing_setup  # noqa: PLC0415

        _billing_setup(app)
        loaded.append("billing")
        logger.debug("Nubi EE: billing loaded")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nubi EE: billing module failed to load — %s", exc)

    # ── Future EE sub-modules go here ─────────────────────────────────────────
    # Pattern:
    #   try:
    #       from app.ee.<module> import setup as _<module>_setup  # noqa: PLC0415
    #       _<module>_setup(app)
    #       loaded.append("<module>")
    #   except Exception as exc:  # noqa: BLE001
    #       logger.warning("Nubi EE: <module> module failed to load — %s", exc)

    if loaded:
        logger.info("Nubi EE loaded: %s", ", ".join(loaded))
        return True

    return False


async def ee_startup() -> None:
    """Run EE startup tasks that need a live DB pool / event loop.

    Called from the FastAPI lifespan AFTER ``init_db()`` (and after the flow
    store is ready).  Safe no-op when the EE tree is absent or any sub-module
    raises — never crashes core startup.  Distinct from :func:`load_ee`, which
    runs at app construction (before the pool exists) and only does non-DB
    registration (feature checkers, task kinds, route mounts).
    """
    try:
        from app.ee.billing import ensure_fx_refresh_flow_async  # noqa: PLC0415

        await ensure_fx_refresh_flow_async()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nubi EE: startup tasks failed (non-fatal) — %s", exc)
