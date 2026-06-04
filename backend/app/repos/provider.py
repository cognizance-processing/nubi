"""Repository provider — FastAPI dependency and test injection point.

The active repository is stored in a module-level singleton.  By default it
is an instance of ``PgRepo`` (the asyncpg-backed production implementation).
Tests can replace it with ``set_repo(InMemoryRepo())`` before running
requests.

Usage
-----
In route handlers::

    from app.repos.provider import get_repo

    @router.get("/{resource}")
    async def list_resource(resource: str, repo: Repo = Depends(get_repo)):
        ...

In tests::

    from app.repos import InMemoryRepo, set_repo

    repo = InMemoryRepo()
    set_repo(repo)
    # ... run tests ...
    set_repo(None)   # optional: reset to PgRepo default
"""

from __future__ import annotations


from app.repos.base import Repo

# Module-level singleton.  Set to None initially; get_repo() initialises it
# lazily on first call so that PgRepo (which uses the DB pool) is not
# instantiated at import time (the pool isn't open yet at import time).
_repo: Repo | None = None


def set_repo(repo: Repo | None) -> None:
    """Override the active repo singleton.

    Pass an ``InMemoryRepo`` instance to swap in the test double.
    Pass ``None`` to reset to the default ``PgRepo``.

    Parameters
    ----------
    repo:
        The new repo instance to use, or ``None`` to restore the default.
    """
    global _repo
    _repo = repo


def get_repo() -> Repo:
    """FastAPI dependency: return the active repo singleton.

    If no override has been injected via ``set_repo()``, a ``PgRepo``
    instance is created lazily (and cached) on first call.

    Returns
    -------
    Repo
        The active Repo implementation.
    """
    global _repo
    if _repo is None:
        from app.repos.pg import PgRepo
        _repo = PgRepo()
    return _repo
