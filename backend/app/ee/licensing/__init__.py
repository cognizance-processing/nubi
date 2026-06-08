"""Nubi EE licensing sub-package.

Public surface
--------------
:class:`~app.ee.licensing.license.Tier`
    Enumeration of deployment tiers (FREE / PRO / ENTERPRISE).
:class:`~app.ee.licensing.license.License`
    Immutable license value object.
:func:`~app.ee.licensing.license.get_license`
    Return the active license (cached; reads ``NUBI_LICENSE_KEY``).
:func:`~app.ee.licensing.license.reset_license_cache`
    Clear the lru_cache (for tests).
"""

from __future__ import annotations

from app.ee.licensing.license import (  # noqa: F401
    License,
    Tier,
    get_license,
    reset_license_cache,
)

__all__ = ["License", "Tier", "get_license", "reset_license_cache"]
