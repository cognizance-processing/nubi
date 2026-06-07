"""In-memory repository — test implementation.

Stores rows in nested dicts: ``_store[table][id] = row_dict``.
Mirrors PgRepo semantics:

- org scoping on every operation (rows from other orgs are invisible).
- Returns plain ``dict`` values (no asyncpg types).
- ``config`` is stored and returned as a Python dict (not a JSON string).
- ``id``, ``created_at``, ``updated_at`` are generated at call time using
  ``uuid.uuid4()`` and ``datetime.now(timezone.utc)`` — never at import time.

Thread-safety: single-async-thread assumption (same as asyncpg helpers);
no locking needed for standard test usage.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.repos.base import RESOURCE_TABLE_MAP, VALID_RESOURCES
from app.errors import AppError


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _validate_resource(resource: str) -> str:
    """Return the table name for *resource*, or raise AppError 404."""
    if resource not in VALID_RESOURCES:
        raise AppError("not_found", f"Unknown resource: {resource!r}.", 404)
    return RESOURCE_TABLE_MAP[resource]


class InMemoryRepo:
    """Dict-backed Repo implementation for tests.

    Usage in tests::

        repo = InMemoryRepo()
        # Optionally seed org membership:
        repo.seed_org_member(org_id="...", user_id="...")
        set_repo(repo)
    """

    def __init__(self) -> None:
        # Per-table storage: table_name -> {id: row_dict}
        self._store: dict[str, dict[str, dict[str, Any]]] = {
            table: {} for table in RESOURCE_TABLE_MAP.values()
        }
        # org_members: "{org_id}:{user_id}" -> {org_id, user_id, role}
        self._org_members: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def seed_org_member(
        self, org_id: str, user_id: str, role: str = "owner"
    ) -> None:
        """Pre-populate an org membership for tests that need org resolution."""
        key = f"{org_id}:{user_id}"
        self._org_members[key] = {
            "org_id": org_id,
            "user_id": user_id,
            "role": role,
        }

    def get_org_for_user(self, user_id: str) -> str | None:
        """Return the org_id of the first membership for *user_id*, or None."""
        for member in self._org_members.values():
            if str(member["user_id"]) == str(user_id):
                return str(member["org_id"])
        return None

    def reset(self) -> None:
        """Clear all stored data (called between tests)."""
        for table in self._store:
            self._store[table].clear()
        self._org_members.clear()

    # ------------------------------------------------------------------
    # Repo protocol implementation
    # ------------------------------------------------------------------

    async def list(
        self, resource: str, org_id: str, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all rows in *resource* that belong to *org_id*, sorted by created_at.

        When *project_id* is provided the result is additionally scoped to that
        project; when ``None`` all of the org's rows are returned.
        """
        table = _validate_resource(resource)
        rows = [
            deepcopy(row)
            for row in self._store[table].values()
            if str(row["org_id"]) == str(org_id)
            and (project_id is None or str(row.get("project_id")) == str(project_id))
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def get(
        self, resource: str, org_id: str, id: str
    ) -> dict[str, Any] | None:
        """Return a single row scoped to *org_id*, or ``None``."""
        table = _validate_resource(resource)
        row = self._store[table].get(str(id))
        if row is None or str(row["org_id"]) != str(org_id):
            return None
        return deepcopy(row)

    async def create(
        self,
        resource: str,
        org_id: str,
        created_by: str,
        name: str,
        config: dict[str, Any],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new row and return the created dict."""
        table = _validate_resource(resource)
        row_id = str(uuid.uuid4())
        now = _now_iso()
        row: dict[str, Any] = {
            "id": row_id,
            "org_id": str(org_id),
            "project_id": str(project_id) if project_id is not None else None,
            "created_by": str(created_by),
            "name": name,
            "config": deepcopy(config),
            "created_at": now,
            "updated_at": now,
        }
        self._store[table][row_id] = row
        return deepcopy(row)

    async def update(
        self,
        resource: str,
        org_id: str,
        id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update allowed fields and return the updated dict, or ``None``."""
        table = _validate_resource(resource)
        row = self._store[table].get(str(id))
        if row is None or str(row["org_id"]) != str(org_id):
            return None

        if "name" in fields:
            row["name"] = fields["name"]
        if "config" in fields:
            row["config"] = deepcopy(fields["config"])
        row["updated_at"] = _now_iso()
        return deepcopy(row)

    async def delete(self, resource: str, org_id: str, id: str) -> bool:
        """Delete a row; return ``True`` if deleted, ``False`` if not found."""
        table = _validate_resource(resource)
        row = self._store[table].get(str(id))
        if row is None or str(row["org_id"]) != str(org_id):
            return False
        del self._store[table][str(id)]
        return True
