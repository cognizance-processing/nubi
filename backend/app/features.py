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

from typing import Callable

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

# Names that are commercial-only: denied by default unless EE registers them.
_COMMERCIAL: frozenset[str] = frozenset({"billing", "paid_tiers"})

# Feature name → callable returning bool.
# A ``None`` value means "no checker registered; fall back to the default".
_REGISTRY: dict[str, Callable[[], bool]] = {}


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


def reset_for_tests() -> None:
    """Clear all registered checkers and restore original commercial set.

    Called by the test harness (``conftest.py``) between tests to prevent
    feature-gate state from leaking across test boundaries.
    """
    _REGISTRY.clear()
    global _COMMERCIAL  # noqa: PLW0603
    _COMMERCIAL = frozenset({"billing", "paid_tiers"})
