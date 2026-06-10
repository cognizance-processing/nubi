"""Datasets catalog store — dual InMemory + Pg implementations.

Schema of a dataset row
-----------------------
{
    "id":           str (UUID),
    "org_id":       str (UUID),
    "name":         str,
    "storage_uri":  str,           # full URI e.g. "file:///..." or "s3://..."
    "format":       str,           # "parquet" (default)
    "schema_json":  list[dict] | None,  # [{name, type}] inferred from data
    "created_by":   str (UUID),
    "source":       "upload" | "materialized",
    "datastore_id": str | None,    # linked datastores row (queryable via normal path)
    "created_at":   str (ISO-8601),
    "updated_at":   str (ISO-8601),
}
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_row(row: Any) -> dict[str, Any]:
    """Convert asyncpg Record (or dict) to a plain dict with string IDs."""
    if row is None:
        return {}
    result: dict[str, Any] = dict(row)
    for key in ("id", "org_id", "created_by", "datastore_id"):
        if key in result and result[key] is not None:
            result[key] = str(result[key])
    if "created_at" in result and isinstance(result["created_at"], datetime):
        result["created_at"] = result["created_at"].isoformat()
    if "updated_at" in result and isinstance(result["updated_at"], datetime):
        result["updated_at"] = result["updated_at"].isoformat()
    if isinstance(result.get("source"), object) and not isinstance(result.get("source"), str):
        # asyncpg may return an enum value object
        result["source"] = str(result["source"])
    if "schema_json" in result and isinstance(result["schema_json"], str):
        try:
            result["schema_json"] = json.loads(result["schema_json"])
        except (ValueError, TypeError):
            result["schema_json"] = None
    return result


# ---------------------------------------------------------------------------
# Protocol (structural subtyping — no ABC needed for Protocol usage)
# ---------------------------------------------------------------------------


class DatasetsCatalog:
    """Abstract interface for the datasets catalog store.

    Concrete implementations: InMemoryDatasetsCatalog (tests), PgDatasetsCatalog (prod).
    """

    async def create(
        self,
        org_id: str,
        name: str,
        storage_uri: str,
        format: str,
        schema_json: list[dict[str, Any]] | None,
        created_by: str,
        source: str,
        datastore_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def get(self, org_id: str, dataset_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def list(self, org_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def update_datastore_id(
        self, org_id: str, dataset_id: str, datastore_id: str
    ) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryDatasetsCatalog(DatasetsCatalog):
    """Dict-backed catalog for tests.  No DB required."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self._store.clear()

    async def create(
        self,
        org_id: str,
        name: str,
        storage_uri: str,
        format: str,
        schema_json: list[dict[str, Any]] | None,
        created_by: str,
        source: str,
        datastore_id: str | None = None,
    ) -> dict[str, Any]:
        row_id = str(uuid.uuid4())
        now = _now_iso()
        row: dict[str, Any] = {
            "id": row_id,
            "org_id": str(org_id),
            "name": name,
            "storage_uri": storage_uri,
            "format": format,
            "schema_json": deepcopy(schema_json),
            "created_by": str(created_by),
            "source": source,
            "datastore_id": str(datastore_id) if datastore_id else None,
            "created_at": now,
            "updated_at": now,
        }
        self._store[row_id] = row
        return deepcopy(row)

    async def get(self, org_id: str, dataset_id: str) -> dict[str, Any] | None:
        row = self._store.get(str(dataset_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return None
        return deepcopy(row)

    async def list(self, org_id: str) -> list[dict[str, Any]]:
        rows = [
            deepcopy(row)
            for row in self._store.values()
            if str(row["org_id"]) == str(org_id)
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def update_datastore_id(
        self, org_id: str, dataset_id: str, datastore_id: str
    ) -> None:
        row = self._store.get(str(dataset_id))
        if row and str(row["org_id"]) == str(org_id):
            row["datastore_id"] = str(datastore_id)
            row["updated_at"] = _now_iso()


# ---------------------------------------------------------------------------
# Postgres implementation (production)
# ---------------------------------------------------------------------------


class PgDatasetsCatalog(DatasetsCatalog):
    """asyncpg-backed catalog for production."""

    async def create(
        self,
        org_id: str,
        name: str,
        storage_uri: str,
        format: str,
        schema_json: list[dict[str, Any]] | None,
        created_by: str,
        source: str,
        datastore_id: str | None = None,
    ) -> dict[str, Any]:
        from app.db import fetchrow  # noqa: PLC0415

        schema_str = json.dumps(schema_json) if schema_json is not None else None
        row = await fetchrow(
            """
            INSERT INTO datasets
                (org_id, name, storage_uri, format, schema_json,
                 created_by, source, datastore_id)
            VALUES
                ($1::uuid, $2, $3, $4, $5::jsonb,
                 $6::uuid, $7::dataset_source, $8::uuid)
            RETURNING *
            """,
            org_id,
            name,
            storage_uri,
            format,
            schema_str,
            created_by,
            source,
            datastore_id,
        )
        return _coerce_row(row)

    async def get(self, org_id: str, dataset_id: str) -> dict[str, Any] | None:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            "SELECT * FROM datasets WHERE id = $1::uuid AND org_id = $2::uuid",
            dataset_id,
            org_id,
        )
        if row is None:
            return None
        return _coerce_row(row)

    async def list(self, org_id: str) -> list[dict[str, Any]]:
        from app.db import fetch  # noqa: PLC0415

        rows = await fetch(
            "SELECT * FROM datasets WHERE org_id = $1::uuid ORDER BY created_at ASC",
            org_id,
        )
        return [_coerce_row(r) for r in rows]

    async def update_datastore_id(
        self, org_id: str, dataset_id: str, datastore_id: str
    ) -> None:
        from app.db import execute  # noqa: PLC0415

        await execute(
            """
            UPDATE datasets
            SET datastore_id = $1::uuid, updated_at = NOW()
            WHERE id = $2::uuid AND org_id = $3::uuid
            """,
            datastore_id,
            dataset_id,
            org_id,
        )


# ---------------------------------------------------------------------------
# Singleton provider (mirrors repos/provider.py pattern)
# ---------------------------------------------------------------------------

_catalog: DatasetsCatalog | None = None


def set_catalog(catalog: DatasetsCatalog | None) -> None:
    """Override the active catalog singleton (for tests)."""
    global _catalog
    _catalog = catalog


def get_catalog() -> DatasetsCatalog:
    """Return the active catalog singleton; lazily creates PgDatasetsCatalog."""
    global _catalog
    if _catalog is None:
        _catalog = PgDatasetsCatalog()
    return _catalog
