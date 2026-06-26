"""CRUD store for the ``access_grants`` table (migration 0022).

An access grant is an org-scoped record that grants a subject (``user`` /
``role`` / ``embed_sub``) access to a ``(dimension, value)`` pair, optionally
with an expiry.  Grants are read by ``GET /auth/scope`` and merged into the
caller's effective RLS policies (token policies ∪ stored grants, per dimension).

SECURITY
--------
* Every query filters on ``org_id`` (parameterised — never interpolated).  The
  org_id MUST be supplied by the caller from the VERIFIED token / membership,
  NEVER from the request body.  Cross-org rows are unreachable by construction.
* ``delete`` is org-scoped: deleting a grant that belongs to another org (or
  does not exist) returns ``False`` so the route can answer 404 (never 403),
  preventing cross-tenant existence enumeration.
* Subject values are stored verbatim; the planner NEVER reads this table — only
  the scope-resolution endpoint does, and only for the caller's own subject.

The store is a thin, dependency-injectable layer over ``app.db`` so tests can
swap an in-memory double via ``set_grants_store`` / ``get_grants_store``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app import db

# Subject types accepted in the ``subject_type`` column.  Mirrors the CHECK-free
# convention documented in migration 0022.
VALID_SUBJECT_TYPES: frozenset[str] = frozenset({"user", "role", "embed_sub"})


def _iso(value: Any) -> Any:
    """ISO-8601-serialize datetimes; pass everything else through."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _serialize(row: Any) -> dict[str, Any]:
    """Convert a DB row (or dict) into the API grant shape."""
    d = dict(row)
    return {
        "id": str(d["id"]),
        "org_id": str(d["org_id"]),
        "subject_type": d["subject_type"],
        "subject_id": d["subject_id"],
        "dimension": d["dimension"],
        "value": d["value"],
        "expires_at": _iso(d.get("expires_at")),
        "created_at": _iso(d.get("created_at")),
    }


def _is_expired(expires_at: Any, *, now: datetime | None = None) -> bool:
    """Return True if *expires_at* is non-null and in the past."""
    if expires_at is None:
        return False
    now = now or datetime.now(tz=timezone.utc)
    if not isinstance(expires_at, datetime):
        try:
            expires_at = datetime.fromisoformat(str(expires_at))
        except ValueError:
            return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now


class GrantsStore:
    """Asyncpg-backed CRUD for ``access_grants`` (org-scoped, fail-closed)."""

    async def list_for_subject(
        self,
        org_id: str,
        subject_type: str,
        subject_id: str,
    ) -> list[dict[str, Any]]:
        """List all grants for ``(org_id, subject_type, subject_id)``.

        Org-scoped: only rows belonging to *org_id* are ever returned.
        """
        rows = await db.fetch(
            """
            SELECT id, org_id, subject_type, subject_id, dimension, value,
                   expires_at, created_at
            FROM access_grants
            WHERE org_id = $1::uuid
              AND subject_type = $2
              AND subject_id = $3
            ORDER BY dimension, value
            """,
            org_id,
            subject_type,
            subject_id,
        )
        return [_serialize(r) for r in rows]

    async def effective_for_subject(
        self,
        org_id: str,
        subject_type: str,
        subject_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, list[str]]:
        """Return non-expired grants for the subject as ``{dimension: [values]}``.

        Expired grants are filtered out in Python as a defensive layer (DB test
        doubles may not evaluate the expiry predicate).  This is the shape
        merged into ``GET /auth/scope``'s ``effective_policies``.
        """
        grants = await self.list_for_subject(org_id, subject_type, subject_id)
        out: dict[str, list[str]] = {}
        for g in grants:
            if _is_expired(g.get("expires_at"), now=now):
                continue
            out.setdefault(g["dimension"], []).append(g["value"])
        return out

    async def create(
        self,
        org_id: str,
        subject_type: str,
        subject_id: str,
        dimension: str,
        value: str,
        expires_at: datetime | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """Insert a grant (idempotent on the unique tuple) and return it.

        ON CONFLICT updates ``expires_at`` so re-granting refreshes the expiry.
        """
        grant_id = str(uuid.uuid4())
        row = await db.fetchrow(
            """
            INSERT INTO access_grants
                (id, org_id, subject_type, subject_id, dimension, value,
                 expires_at, created_by)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (org_id, subject_type, subject_id, dimension, value)
            DO UPDATE SET expires_at = EXCLUDED.expires_at
            RETURNING id, org_id, subject_type, subject_id, dimension, value,
                      expires_at, created_at
            """,
            grant_id,
            org_id,
            subject_type,
            subject_id,
            dimension,
            value,
            expires_at,
            created_by,
        )
        if row is None:
            # Some drivers/doubles do not return on conflict; re-read.
            rows = await self.list_for_subject(org_id, subject_type, subject_id)
            for r in rows:
                if r["dimension"] == dimension and r["value"] == value:
                    return r
            raise RuntimeError("access_grants insert returned no row")
        return _serialize(row)

    async def delete(self, grant_id: str, org_id: str) -> bool:
        """Delete a grant by id WITHIN *org_id*.

        Returns True if a row was deleted, False otherwise (not found OR belongs
        to another org).  The caller answers 404 on False so existence never
        leaks across tenants.
        """
        status = await db.execute(
            "DELETE FROM access_grants WHERE id = $1::uuid AND org_id = $2::uuid",
            grant_id,
            org_id,
        )
        # asyncpg returns e.g. "DELETE 1" / "DELETE 0".
        try:
            return int(str(status).rsplit(" ", 1)[-1]) > 0
        except (ValueError, IndexError):
            return bool(status)


# ---------------------------------------------------------------------------
# Module-level singleton (swappable in tests)
# ---------------------------------------------------------------------------

_store: GrantsStore = GrantsStore()


def get_grants_store() -> GrantsStore:
    """Return the active GrantsStore (default: DB-backed)."""
    return _store


def set_grants_store(store: GrantsStore) -> None:
    """Replace the active store (for tests)."""
    global _store
    _store = store


def reset_for_tests() -> None:
    """Reset to the default DB-backed store."""
    global _store
    _store = GrantsStore()
