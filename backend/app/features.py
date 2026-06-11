"""Feature-gate registry for Nubi's open-core architecture.

This module is the **only** source-of-truth for "is feature X available in
this deployment?"  Core code MUST call :func:`feature_enabled` instead of
performing ad-hoc env/config checks.  EE code MUST call
:func:`register_feature` to plug in its own checker; the core module never
imports from ``app.ee``.

Design
------
- Non-commercial (open-core) features: ``feature_enabled`` returns ``True``
  by default so the OSS build works fully without any EE code present.
- Commercial features (``billing``, ``paid_tiers``, and anything explicitly
  registered via :func:`declare_commercial`): ``feature_enabled`` returns
  ``False`` unless EE code has registered a truthy checker.

EE integration
--------------
EE packages call ``register_feature(name, checker)`` at import / startup time
(typically from ``app.ee.__init__.load_ee``).  The checker is any callable
returning ``bool``; it may inspect the active license, org tier, etc.

Thread safety
-------------
:data:`_REGISTRY` is populated at startup (single-threaded import phase) and
read at request time.  No locking is needed for the current single-process
FastAPI model.

Usage
-----
>>> from app.features import feature_enabled, register_feature
>>> feature_enabled("billing")         # False in OSS build
False
>>> register_feature("billing", lambda: True)
>>> feature_enabled("billing")         # True after EE registers
True
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

# Names that are commercial-only: denied by default unless EE registers them.
_COMMERCIAL: frozenset[str] = frozenset({"billing", "paid_tiers"})

# Feature name → callable returning bool.
# A ``None`` value means "no checker registered; fall back to the default".
_REGISTRY: dict[str, Callable[[], bool]] = {}

# Usage-quota checker — mirrors the feature-checker pattern above.  Core code
# calls :func:`enforce_quota` before metered operations (compute, AI, embed,
# flows); EE billing registers an implementation that resolves the org's
# subscription tier and compares current-period usage against the tier quota.
# ``None`` (OSS build, or EE absent) means "no quota enforcement: allow all".
#
# Signature: checker(org_id: str, dimension: str, amount: float)
#   → (allowed: bool, reason: str)   (may be sync or async)
QuotaChecker = Callable[[str, str, float], "tuple[bool, str] | Awaitable[tuple[bool, str]]"]

_QUOTA_CHECKER: QuotaChecker | None = None

# Usage-limits provider — the read-only sibling of the quota checker, consumed
# by the OSS-core *usage* surface (``app.usage``) to show "used / limit / %" per
# metric.  EE billing registers a provider that maps the org's subscription tier
# to its per-metric limits; when no provider is registered (OSS build, or EE
# absent) every limit is ``None`` (unlimited) — core surfaces usage without ever
# implying a hard billing cap.
#
# Signature: provider(org_id: str) -> dict[str, float | None]
#   keys are usage-metric ids (see app.usage.aggregate.METRICS), values are the
#   numeric limit for the org's tier or ``None`` for unlimited.  May be
#   sync or async.
UsageLimitsProvider = Callable[[str], "dict[str, float | None] | Awaitable[dict[str, float | None]]"]

_USAGE_LIMITS_PROVIDER: UsageLimitsProvider | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def feature_enabled(name: str) -> bool:
    """Return ``True`` when feature *name* is available in this deployment.

    - If EE has registered a checker for *name*, that checker is called.
    - Otherwise: commercial features default to ``False``; all others to
      ``True`` (open-core: ship everything non-commercial in OSS).

    Parameters
    ----------
    name:
        The feature identifier (e.g. ``"billing"``, ``"paid_tiers"``).

    Returns
    -------
    bool
        ``True`` when the feature is available.
    """
    checker = _REGISTRY.get(name)
    if checker is not None:
        try:
            return bool(checker())
        except Exception:
            # Defensive: a broken checker must never take down request handling.
            return False
    # Default: deny commercial, allow everything else.
    return name not in _COMMERCIAL


def register_feature(name: str, checker: Callable[[], bool]) -> None:
    """Register *checker* as the availability test for feature *name*.

    EE packages call this at import/startup time to provide tier- or
    license-based availability logic for commercial features.  Core code
    must never call this — only ``app.ee.*`` modules should.

    Parameters
    ----------
    name:
        Feature identifier (e.g. ``"billing"``).
    checker:
        Zero-argument callable returning ``bool``.  Will be called on every
        :func:`feature_enabled` call so keep it fast (no I/O).

    Raises
    ------
    TypeError
        When *checker* is not callable.
    """
    if not callable(checker):
        raise TypeError(f"register_feature: checker for '{name}' must be callable, got {type(checker)!r}")
    _REGISTRY[name] = checker


def declare_commercial(*names: str) -> None:
    """Mark additional feature names as commercial (denied by default).

    Use this when an EE module wants to declare a new gated feature without
    immediately registering a checker (the checker is registered separately).
    Calling this is idempotent.

    Note: ``"billing"`` and ``"paid_tiers"`` are already declared commercial
    at module load time; you only need this for additional EE-defined features.
    """
    global _COMMERCIAL  # noqa: PLW0603
    _COMMERCIAL = _COMMERCIAL | frozenset(names)


# ---------------------------------------------------------------------------
# Usage-quota enforcement hook
# ---------------------------------------------------------------------------


def register_quota_checker(checker: QuotaChecker | None) -> None:
    """Register *checker* as the runtime usage-quota gate (EE billing only).

    The checker is invoked by :func:`check_quota` / :func:`enforce_quota`
    before metered operations execute.  It receives keyword arguments
    ``org_id`` (str), ``dimension`` (a :class:`UsageSnapshot` field name such
    as ``"compute_units"``, ``"ai_calls"``, ``"embedded_sessions"``,
    ``"agent_runs"``, or ``"storage_gb"``) and ``amount`` (the requested usage)
    and returns ``(allowed, reason)``.  It may be sync or async.

    Pass ``None`` to remove the registered checker (OSS default: allow all).

    Raises
    ------
    TypeError
        When *checker* is neither callable nor ``None``.
    """
    if checker is not None and not callable(checker):
        raise TypeError(f"register_quota_checker: checker must be callable or None, got {type(checker)!r}")
    global _QUOTA_CHECKER  # noqa: PLW0603
    _QUOTA_CHECKER = checker


async def check_quota(org_id: str | None, dimension: str, amount: float = 1.0) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for spending *amount* of *dimension*.

    Allows when:
    - No quota checker is registered (OSS build — quotas are an EE concern).
    - ``org_id`` is ``None`` (unattributable usage cannot be quota-checked;
      the metering layer logs a warning for that case).
    - The registered checker allows, or raises (fail-open: a broken billing
      checker must never take down request handling).
    """
    if _QUOTA_CHECKER is None or org_id is None:
        return True, ""
    try:
        result: Any = _QUOTA_CHECKER(org_id=org_id, dimension=dimension, amount=amount)
        if inspect.isawaitable(result):
            result = await result
        allowed, reason = result
        return bool(allowed), str(reason or "")
    except Exception:
        # Defensive: a broken checker must never take down request handling.
        return True, ""


async def enforce_quota(org_id: str | None, dimension: str, amount: float = 1.0) -> None:
    """Raise ``AppError("quota_exceeded", …, 402)`` when the quota denies.

    The raising counterpart of :func:`check_quota` for route/executor call
    sites.  A no-op in OSS builds (no checker registered) and for usage that
    cannot be attributed to an org.
    """
    allowed, reason = await check_quota(org_id, dimension, amount)
    if not allowed:
        from app.errors import AppError  # noqa: PLC0415 — avoid import cycle at module load

        raise AppError(
            "quota_exceeded",
            reason or f"Usage quota exceeded for {dimension!r}. Upgrade your plan to continue.",
            402,
        )


# ---------------------------------------------------------------------------
# Usage-limits provider hook (read-only; consumed by app.usage)
# ---------------------------------------------------------------------------


def register_usage_limits_provider(provider: UsageLimitsProvider | None) -> None:
    """Register *provider* as the per-org usage-limits source (EE billing only).

    The provider is invoked by :func:`get_usage_limits` to surface each usage
    metric's configured limit ("used / limit / %") in the core usage view.  It
    receives the ``org_id`` and returns a ``{metric_id: limit_or_None}`` mapping;
    it may be sync or async.

    Pass ``None`` to remove the registered provider (OSS default: all unlimited).

    Raises
    ------
    TypeError
        When *provider* is neither callable nor ``None``.
    """
    if provider is not None and not callable(provider):
        raise TypeError(
            f"register_usage_limits_provider: provider must be callable or None, "
            f"got {type(provider)!r}"
        )
    global _USAGE_LIMITS_PROVIDER  # noqa: PLW0603
    _USAGE_LIMITS_PROVIDER = provider


async def get_usage_limits(org_id: str | None) -> dict[str, float | None]:
    """Return the per-metric usage limits for *org_id* (``{}`` when unlimited).

    Returns an empty mapping when:
    - No provider is registered (OSS build — limits are an EE concern).
    - ``org_id`` is ``None``.
    - The registered provider raises (fail-open: a broken provider must never
      take down the usage view; the caller treats missing keys as unlimited).
    """
    if _USAGE_LIMITS_PROVIDER is None or org_id is None:
        return {}
    try:
        result: Any = _USAGE_LIMITS_PROVIDER(org_id)
        if inspect.isawaitable(result):
            result = await result
        return dict(result or {})
    except Exception:
        return {}


def reset_for_tests() -> None:
    """Clear all registered checkers and restore original commercial set.

    Called by the test harness (``conftest.py``) between tests to prevent
    feature-gate state from leaking across test boundaries.
    """
    _REGISTRY.clear()
    global _COMMERCIAL  # noqa: PLW0603
    _COMMERCIAL = frozenset({"billing", "paid_tiers"})
    global _QUOTA_CHECKER  # noqa: PLW0603
    _QUOTA_CHECKER = None
    global _USAGE_LIMITS_PROVIDER  # noqa: PLW0603
    _USAGE_LIMITS_PROVIDER = None
