"""Feature-flag REST endpoint.

Endpoint
--------
GET /features
    Return the list of enabled commercial/registered feature names.

    The frontend reads this endpoint to decide which paid-tier UI elements to
    activate.  OSS features are default-on client-side and are NOT listed here;
    only features that are enabled on the backend (feature_enabled() == True)
    are included so the response is always a subset of the commercial names.

Security
--------
Requires a valid first-party Bearer token (``current_user``).  The feature
list is org-invariant at the moment (no per-org licensing); if that changes the
endpoint can be extended to accept an org context.

Mirrors routes/secrets.py for router/prefix/auth style.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import current_user
from app.features import _COMMERCIAL, _REGISTRY, feature_enabled

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/features", tags=["features"])


# ---------------------------------------------------------------------------
# GET /features
# ---------------------------------------------------------------------------


@router.get("", status_code=200)
async def list_features(
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, list[str]]:
    """Return enabled commercial/registered feature names.

    Evaluates :func:`~app.features.feature_enabled` over:

    * All built-in commercial names (``billing``, ``paid_tiers``).
    * Any additional names registered via :func:`~app.features.register_feature`
      or :func:`~app.features.declare_commercial`.

    Only features for which :func:`~app.features.feature_enabled` returns
    ``True`` are included in the response.  An empty list means the deployment
    is running in OSS mode with no commercial features active.

    Returns
    -------
    dict
        ``{"features": ["billing", ...]}`` — list of enabled feature names.
    """
    # Union of built-in commercial names and any additionally registered names.
    all_names: set[str] = set(_COMMERCIAL) | set(_REGISTRY.keys())
    enabled = [name for name in sorted(all_names) if feature_enabled(name)]
    return {"features": enabled}
