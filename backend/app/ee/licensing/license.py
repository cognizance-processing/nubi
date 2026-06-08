"""EE license model for Nubi.

This module defines the :class:`License` value object and a lightweight
:func:`get_license` function that reads the ``NUBI_LICENSE_KEY`` environment
variable and returns the corresponding tier.

Design notes
------------
- The license key format is deliberately opaque at this stage.  In a future
  iteration it will be a signed JWT validated against Nubi's public key.
- Absence of ``NUBI_LICENSE_KEY`` (or an empty/invalid value) silently maps
  to :attr:`Tier.FREE` — the OSS build never breaks due to a missing key.
- This module is **only** imported inside ``app.ee``; the open-source core
  never imports it directly (enforcing the core→ee no-import rule).
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field
from functools import lru_cache


# ---------------------------------------------------------------------------
# Tier enumeration
# ---------------------------------------------------------------------------


class Tier(str, enum.Enum):
    """Subscription / deployment tier.

    FREE
        Open-source self-hosted deployment with no paid features.
    PRO
        Single-team commercial deployment; enables billing + usage caps.
    ENTERPRISE
        Multi-tenant commercial deployment; enables all EE features + SSO.
    """

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# ---------------------------------------------------------------------------
# License value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class License:
    """Immutable snapshot of the active Nubi license.

    Attributes
    ----------
    tier:
        The resolved :class:`Tier` for this deployment.
    raw_key:
        The raw value of ``NUBI_LICENSE_KEY`` (``""`` when absent).
    """

    tier: Tier
    raw_key: str = field(repr=False)

    # ------------------------------------------------------------------
    # Convenience predicates
    # ------------------------------------------------------------------

    @property
    def is_free(self) -> bool:
        """Return ``True`` for a FREE (OSS) deployment."""
        return self.tier is Tier.FREE

    @property
    def is_paid(self) -> bool:
        """Return ``True`` for any paid tier (PRO or ENTERPRISE)."""
        return self.tier in (Tier.PRO, Tier.ENTERPRISE)

    @property
    def is_enterprise(self) -> bool:
        """Return ``True`` only for the ENTERPRISE tier."""
        return self.tier is Tier.ENTERPRISE


# ---------------------------------------------------------------------------
# Key → Tier resolution
# ---------------------------------------------------------------------------

# Prefix convention (will be replaced by signed-JWT validation later):
#   nubi_pro_...        → PRO
#   nubi_enterprise_... → ENTERPRISE
#   anything else / empty → FREE
_PREFIX_MAP: dict[str, Tier] = {
    "nubi_pro_": Tier.PRO,
    "nubi_enterprise_": Tier.ENTERPRISE,
}


def _resolve_tier(key: str) -> Tier:
    """Map a raw license key string to a :class:`Tier`.

    This is intentionally simple — the real implementation will verify a
    cryptographic signature.  Absent or blank keys yield :attr:`Tier.FREE`.
    """
    key = key.strip()
    if not key:
        return Tier.FREE
    for prefix, tier in _PREFIX_MAP.items():
        if key.lower().startswith(prefix):
            return tier
    # Unrecognised key → treat as FREE (fail-open for self-hosted users who
    # supply a key from a different environment or an old format).
    return Tier.FREE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_license() -> License:
    """Return the active :class:`License` for this deployment.

    Reads ``NUBI_LICENSE_KEY`` from the process environment.  The result is
    cached after the first call (process-lifetime cache, consistent with how
    ``app.config.get_settings`` works).  Call :func:`reset_license_cache` in
    tests to clear the cache.

    Returns
    -------
    License
        Always succeeds — returns a FREE license when the key is absent or
        unrecognised.
    """
    raw_key = os.environ.get("NUBI_LICENSE_KEY", "")
    tier = _resolve_tier(raw_key)
    return License(tier=tier, raw_key=raw_key)


def reset_license_cache() -> None:
    """Clear the cached license (for use in tests)."""
    get_license.cache_clear()
