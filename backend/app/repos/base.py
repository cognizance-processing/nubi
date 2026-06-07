"""Repository Protocol for org-scoped resource CRUD.

All resource tables share the same column shape::

    id uuid, org_id uuid, created_by uuid, name text,
    config jsonb, created_at timestamptz, updated_at timestamptz

The ``resource`` parameter is validated against a fixed allowlist so that
caller-supplied strings can never be interpolated into SQL as table names.

Allowlist
---------
``RESOURCE_TABLE_MAP`` maps the URL resource name to the actual table name.
Both values are identical here (by convention) but the indirection is the
security boundary: unknown resource names are rejected before any SQL is built.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Fixed allowlist: resource name (as it appears in the URL) → table name.
# NEVER add caller-supplied strings to this dict.
RESOURCE_TABLE_MAP: dict[str, str] = {
    "datastores": "datastores",
    "boards": "boards",
    "queries": "queries",
    "widgets": "widgets",
}

# Convenience set for O(1) membership checks.
VALID_RESOURCES: frozenset[str] = frozenset(RESOURCE_TABLE_MAP)


@runtime_checkable
class Repo(Protocol):
    """Protocol for org-scoped resource repositories.

    Every method receives the *resource* name (validated against the allowlist
    by the caller or the implementation) and the *org_id* so that rows from
    other orgs can never leak through.
    """

    async def list(
        self, resource: str, org_id: str, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all rows for *resource* that belong to *org_id*.

        Parameters
        ----------
        resource:
            One of the keys in ``RESOURCE_TABLE_MAP``.
        org_id:
            UUID string of the caller's organisation.
        project_id:
            Optional project filter. When provided, only rows whose
            ``project_id`` matches are returned; when ``None`` all of the org's
            rows are returned (existing behaviour preserved).

        Returns
        -------
        list[dict]
            Possibly empty list of row dicts.
        """
        ...

    async def get(
        self, resource: str, org_id: str, id: str
    ) -> dict[str, Any] | None:
        """Return a single row by *id*, scoped to *org_id*, or ``None``.

        Parameters
        ----------
        resource:
            One of the keys in ``RESOURCE_TABLE_MAP``.
        org_id:
            UUID string of the caller's organisation.
        id:
            UUID string of the resource row.

        Returns
        -------
        dict | None
            The row dict, or ``None`` if not found / wrong org.
        """
        ...

    async def create(
        self,
        resource: str,
        org_id: str,
        created_by: str,
        name: str,
        config: dict[str, Any],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new row and return the created row dict.

        Parameters
        ----------
        resource:
            One of the keys in ``RESOURCE_TABLE_MAP``.
        org_id:
            UUID string of the caller's organisation.
        created_by:
            UUID string of the user creating the resource.
        name:
            Human-readable name for the resource.
        config:
            Arbitrary JSON-serialisable config dict (stored as ``jsonb``).
        project_id:
            Optional project the resource belongs to. When ``None`` the
            ``project_id`` column is left NULL.

        Returns
        -------
        dict
            The newly created row dict (includes ``id``, ``created_at``, etc.).
        """
        ...

    async def update(
        self,
        resource: str,
        org_id: str,
        id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update a row and return the updated dict, or ``None`` if not found.

        Only keys present in *fields* are updated.  Unknown keys are ignored.
        *org_id* is always applied as a filter so cross-org updates are
        impossible.

        Parameters
        ----------
        resource:
            One of the keys in ``RESOURCE_TABLE_MAP``.
        org_id:
            UUID string of the caller's organisation.
        id:
            UUID string of the resource row.
        fields:
            Mapping of ``{column_name: new_value}`` (subset of ``name``,
            ``config``).

        Returns
        -------
        dict | None
            The updated row dict, or ``None`` if the row was not found.
        """
        ...

    async def delete(self, resource: str, org_id: str, id: str) -> bool:
        """Delete a row; return ``True`` if deleted, ``False`` if not found.

        The delete is org-scoped: a row belonging to a different org is treated
        as not found (returns ``False``).

        Parameters
        ----------
        resource:
            One of the keys in ``RESOURCE_TABLE_MAP``.
        org_id:
            UUID string of the caller's organisation.
        id:
            UUID string of the resource row.

        Returns
        -------
        bool
            ``True`` if a row was deleted, ``False`` otherwise.
        """
        ...
