"""Project-scoped environments + resource versioning.

``app.environments.store`` holds the dual-store (InMemoryEnvStore for tests,
PgEnvStore for production) behind the ``get_env_store()`` / ``set_env_store()``
provider — mirroring ``app.flows.store``.
"""

from app.environments.store import (
    InMemoryEnvStore,
    PgEnvStore,
    get_env_store,
    set_env_store,
)

__all__ = [
    "InMemoryEnvStore",
    "PgEnvStore",
    "get_env_store",
    "set_env_store",
]
