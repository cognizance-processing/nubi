"""``app.config`` package — backward-compatible shim + project (nubi.toml) config.

Historically ``app.config`` was a single module (``backend/app/config.py``) that
exposed :class:`Settings` / :func:`get_settings` (env-derived application
settings).  Wave 4 adds a *second*, orthogonal kind of configuration — the
per-project ``nubi.toml`` (managed-lakehouse optimizer overrides) — which lives
in :mod:`app.config.nubi_toml`.

Turning ``config`` into a package would normally **shadow** the original
``config.py`` module (a package wins over a same-named module on ``sys.path``),
which would break every existing ``from app.config import get_settings`` import.
To stay strictly backward-compatible WITHOUT moving or editing ``config.py``
(owned elsewhere), this package loads the original module *by file path* and
re-exports its entire public surface.  The result: ``app.config`` keeps every
name it had before, and additionally exposes the ``nubi_toml`` submodule.

This shim is intentionally defensive — if the sibling ``config.py`` ever becomes
a true package member we degrade gracefully rather than crash on import.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import os as _os
import sys as _sys

# ---------------------------------------------------------------------------
# 1. Re-export the original env-settings module (backward compatibility).
#
# The legacy implementation lives at ``backend/app/config.py`` — a SIBLING of
# this package directory (``backend/app/config/``).  We load it under a private
# module name and copy its public names into this package's namespace so that
# ``from app.config import Settings, get_settings`` keeps working verbatim.
# ---------------------------------------------------------------------------

_PKG_DIR = _os.path.dirname(_os.path.abspath(__file__))
_LEGACY_PATH = _os.path.join(_os.path.dirname(_PKG_DIR), "config.py")


def _load_legacy_settings_module():
    """Load the legacy ``config.py`` by path and return the module (or None)."""
    if not _os.path.isfile(_LEGACY_PATH):
        return None
    mod_name = "app._config_legacy"
    if mod_name in _sys.modules:
        return _sys.modules[mod_name]
    spec = _importlib_util.spec_from_file_location(mod_name, _LEGACY_PATH)
    if spec is None or spec.loader is None:
        return None
    module = _importlib_util.module_from_spec(spec)
    # Register before exec so any self-referential import resolves to one object
    # (preserves the ``@lru_cache`` singleton identity of ``get_settings``).
    _sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


_legacy = _load_legacy_settings_module()

if _legacy is not None:
    # Copy public names (and the well-known settings helpers) so existing
    # imports keep resolving against the *same* objects.
    for _name in dir(_legacy):
        if _name.startswith("__"):
            continue
        globals().setdefault(_name, getattr(_legacy, _name))

# ---------------------------------------------------------------------------
# 2. New surface: per-project nubi.toml config.
# ---------------------------------------------------------------------------

from app.config.nubi_toml import (  # noqa: E402
    OptimizeTableConfig,
    ProjectConfig,
    load_project_config,
)

__all__ = [
    # Project (nubi.toml) config — new in Wave 4.
    "ProjectConfig",
    "OptimizeTableConfig",
    "load_project_config",
]

# Best-effort: surface the legacy public names in ``__all__`` too, so
# ``from app.config import *`` (rare, but possible) stays equivalent.
if _legacy is not None:
    for _name in ("Settings", "get_settings"):
        if _name in globals() and _name not in __all__:
            __all__.append(_name)
