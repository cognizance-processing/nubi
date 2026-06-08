"""Nubi secrets subsystem — encrypted named secrets scoped per organisation.

Submodules
----------
crypto
    Fernet encryption/decryption helpers (``encrypt`` / ``decrypt``).
store
    Dual InMemory + Pg secret store with a module-level provider.

Public re-exports
-----------------
``get_secret_store`` and ``set_secret_store`` are re-exported here for
convenience; callers that only need the provider can import from this package
instead of the submodule.
"""

from __future__ import annotations

from app.secrets.store import (
    InMemorySecretStore,
    PgSecretStore,
    get_secret_store,
    set_secret_store,
)

__all__ = [
    "InMemorySecretStore",
    "PgSecretStore",
    "get_secret_store",
    "set_secret_store",
]
