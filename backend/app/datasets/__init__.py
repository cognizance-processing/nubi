"""Datasets catalog — lakehouse data-plane.

Public API
----------
``DatasetsCatalog``
    Abstract protocol for the datasets catalog store.

``InMemoryDatasetsCatalog``
    Dict-backed implementation for tests (no DB required).

``PgDatasetsCatalog``
    asyncpg-backed production implementation.

``get_catalog``
    Return the active catalog singleton (lazily creates PgDatasetsCatalog).

``set_catalog``
    Inject a specific catalog implementation (for tests).
"""

from __future__ import annotations

from app.datasets.catalog import (
    DatasetsCatalog,
    InMemoryDatasetsCatalog,
    PgDatasetsCatalog,
    get_catalog,
    set_catalog,
)

__all__ = [
    "DatasetsCatalog",
    "InMemoryDatasetsCatalog",
    "PgDatasetsCatalog",
    "get_catalog",
    "set_catalog",
]
