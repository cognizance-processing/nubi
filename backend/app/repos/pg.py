"""asyncpg-backed repository — production implementation.

Every query is parameterised (``$N`` placeholders); table names are never
interpolated from caller input — they are resolved from the fixed
``RESOURCE_TABLE_MAP`` allowlist.

asyncpg returns ``Record`` objects; all public methods convert them to plain
``dict`` values so callers are not tied to asyncpg types.  The ``config``
column is stored as ``jsonb``; asyncpg automatically deserialises it to a
Python ``dict``, so no extra parsing is required.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from app.db import execute, fetch, fetchrow
from app.errors import AppError
from app.repos.base import RESOURCE_TABLE_MAP, VALID_RESOURCES


def _record_to_dict(record: Any) -> dict[str, Any]:
    """Convert an asyncpg Record (or dict) to a plain dict.

    Also coerces non-serialisable types (``datetime``, ``uuid.UUID``) to
    strings so the result can be passed directly to ``JSONResponse``.
    """
    if record is None:
        return {}
    row: dict[str, Any] = dict(record)
    for key, value in row.items():
        if isinstance(value, datetime):
            row[key] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            row[key] = str(value)
    # config should already be a dict from asyncpg jsonb decoding, but guard.
    if "config" in row and isinstance(row["config"], str):
        try:
            row["config"] = json.loads(row["config"])
        except (ValueError, TypeError):
            row["config"] = {}
    return row


def _validate_resource(resource: str) -> str:
    """Return the table name for *resource*, or raise AppError 404."""
    if resource not in VALID_RESOURCES:
        raise AppError("not_found", f"Unknown resource: {resource!r}.", 404)
    return RESOURCE_TABLE_MAP[resource]


async def resolve_required_project_id(org_id: str, project_id: str | None) -> str:
    """Return a non-NULL project id for a resource INSERT.

    Every resource row carries ``project_id NOT NULL``.  When the caller did
    not resolve a project (``None``), fall back to the org's default project,
    creating the "Default" project if the org somehow has none.  Raises
    ``AppError`` 400 only when no project can be resolved at all.
    """
    if project_id is not None:
        return str(project_id)
    from app.repos import projects as projects_repo  # noqa: PLC0415

    pid = await projects_repo.get_default_project_id(org_id)
    if pid is None:
        try:
            pid = await projects_repo.ensure_default_project(org_id, None)
        except Exception:  # noqa: BLE001 — surfaced as a clean 400 below
            pid = None
    if pid is None:
        raise AppError(
            "project_required",
            "Resource creation requires a project and the org has none.",
            400,
        )
    return str(pid)


class PgRepo:
    """asyncpg-backed production Repo implementation.

    Uses the ``fetch`` / ``fetchrow`` / ``execute`` helpers from ``app.db``
    (which acquire a connection from the pool automatically).  All queries are
    org-scoped via ``WHERE org_id = $N``.
    """

    async def list(
        self, resource: str, org_id: str, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all rows for *resource* in *org_id*, ordered by ``created_at``.

        When *project_id* is provided the result is additionally scoped to that
        project; when ``None`` all of the org's rows are returned (preserving
        existing behaviour).
        """
        table = _validate_resource(resource)
        # Table name comes from the allowlist — safe to format into the query.
        if project_id is not None:
            rows = await fetch(
                f"SELECT * FROM {table} WHERE org_id = $1::uuid "
                f"AND project_id = $2::uuid ORDER BY created_at ASC",
                org_id,
                project_id,
            )
        else:
            rows = await fetch(
                f"SELECT * FROM {table} WHERE org_id = $1::uuid ORDER BY created_at ASC",
                org_id,
            )
        return [_record_to_dict(r) for r in rows]

    async def get(
        self, resource: str, org_id: str, id: str
    ) -> dict[str, Any] | None:
        """Return a single row scoped to *org_id*, or ``None``."""
        table = _validate_resource(resource)
        row = await fetchrow(
            f"SELECT * FROM {table} WHERE id = $1::uuid AND org_id = $2::uuid",
            id,
            org_id,
        )
        if row is None:
            return None
        return _record_to_dict(row)

    async def create(
        self,
        resource: str,
        org_id: str,
        created_by: str,
        name: str,
        config: dict[str, Any],
        project_id: str | None = None,
        id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new row and return the created dict.

        When *id* is provided the row is inserted with that exact uuid (the
        caller already minted a stable identifier — e.g. the query registry
        keeps registry ids and row ids identical); otherwise the DB default
        generates one.

        ``project_id`` is required by the schema (NOT NULL): when the caller
        passes ``None`` the org's default project is resolved as a fallback,
        so a create can never write a NULL project_id.
        """
        table = _validate_resource(resource)
        project_id = await resolve_required_project_id(org_id, project_id)
        config_json = json.dumps(config)
        if id is not None:
            row = await fetchrow(
                f"""
                INSERT INTO {table} (id, org_id, created_by, name, config, project_id)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::jsonb, $6::uuid)
                RETURNING *
                """,
                id,
                org_id,
                created_by,
                name,
                config_json,
                project_id,
            )
        else:
            row = await fetchrow(
                f"""
                INSERT INTO {table} (org_id, created_by, name, config, project_id)
                VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::uuid)
                RETURNING *
                """,
                org_id,
                created_by,
                name,
                config_json,
                project_id,
            )
        if row is None:  # pragma: no cover — INSERT RETURNING always returns a row
            raise AppError("internal_error", "Failed to create resource.", 500)
        return _record_to_dict(row)

    async def update(
        self,
        resource: str,
        org_id: str,
        id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update allowed fields and return the updated dict, or ``None``."""
        table = _validate_resource(resource)

        # Build SET clause from allowed fields only.
        allowed = {"name", "config"}
        updates: list[str] = []
        values: list[Any] = []
        param_idx = 1

        for field in ("name", "config"):
            if field not in fields:
                continue
            if field not in allowed:
                continue
            value = fields[field]
            if field == "config":
                value = json.dumps(value)
                updates.append(f"{field} = ${param_idx}::jsonb")
            else:
                updates.append(f"{field} = ${param_idx}")
            values.append(value)
            param_idx += 1

        if not updates:
            # Nothing to update — fetch and return current state.
            return await self.get(resource, org_id, id)

        updates.append("updated_at = now()")
        set_clause = ", ".join(updates)

        # id and org_id come after the SET values.
        values.extend([id, org_id])
        id_param = param_idx
        org_param = param_idx + 1

        row = await fetchrow(
            f"""
            UPDATE {table}
            SET {set_clause}
            WHERE id = ${id_param}::uuid AND org_id = ${org_param}::uuid
            RETURNING *
            """,
            *values,
        )
        if row is None:
            return None
        return _record_to_dict(row)

    async def delete(self, resource: str, org_id: str, id: str) -> bool:
        """Delete a row; return ``True`` if a row was deleted."""
        table = _validate_resource(resource)
        status = await execute(
            f"DELETE FROM {table} WHERE id = $1::uuid AND org_id = $2::uuid",
            id,
            org_id,
        )
        # asyncpg returns "DELETE N"; parse the count.
        try:
            count = int(status.split()[-1])
        except (ValueError, IndexError):
            count = 0
        return count > 0

    def get_sync(self, resource: str, org_id: str, id: str) -> dict | None:
        """Synchronous wrapper around ``get`` for use in threadpool handlers.

        Runs ``self.get(resource, org_id, id)`` in a new event loop created
        for this call.  Use this method only from non-async contexts (e.g.
        flow task handlers that run in a ``ThreadPoolExecutor`` thread).

        Returns ``None`` when the row is not found or belongs to a different org.
        """
        import asyncio  # noqa: PLC0415
        return asyncio.run(self.get(resource, org_id, id))
