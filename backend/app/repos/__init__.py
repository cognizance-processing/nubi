"""Repository layer for Nubi resource CRUD.

The repository layer decouples route handlers from the database so that
in-memory fakes can be injected during tests without a live Neon connection.

Public exports
--------------
Repo          — the typing.Protocol that all repo implementations satisfy.
PgRepo        — asyncpg-backed production implementation.
InMemoryRepo  — dict-backed in-memory implementation for tests.
get_repo      — FastAPI dependency returning the active repo.
set_repo      — test helper: inject a specific repo implementation.
"""

from app.repos.base import Repo
from app.repos.memory import InMemoryRepo
from app.repos.pg import PgRepo
from app.repos.provider import get_repo, set_repo

__all__ = ["Repo", "PgRepo", "InMemoryRepo", "get_repo", "set_repo"]
