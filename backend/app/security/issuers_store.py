"""JWT Issuers store — InMemory (tests) + Pg (production).

Provides an org-scoped store for JWT issuer / JWKS configurations used by
the embed token verification path.  Replacing the process-level
``IssuerRegistry`` (code-only) with this persistent store means that issuer
configs can be created, updated, and deleted via the management API without
restarting the backend.

Schema
------
The ``jwt_issuers`` table (migration 0024) has the columns:

    id, org_id, name, issuer, jwks_url, static_jwks_json,
    algorithms, audience, enabled, created_by, created_at, updated_at

Public API
----------
JwtIssuerRow
    TypedDict describing a single issuer row (public shape, no secrets).

InMemoryIssuersStore
    Dict-backed store for unit tests.

PgIssuersStore
    asyncpg-backed production store.

get_issuers_store() / set_issuers_store(store)
    Module-level singleton helpers (same pattern as secrets/store.py).
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Sentinel for "not provided" — distinct from None (which means "clear the field").
# ---------------------------------------------------------------------------

_UNSET: Any = object()


# ---------------------------------------------------------------------------
# Row type
# ---------------------------------------------------------------------------

JwtIssuerRow = dict[str, Any]
"""
Dict with keys: id, org_id, name, issuer, jwks_url, static_jwks_json,
algorithms, audience, enabled, created_by, created_at, updated_at
"""


# ---------------------------------------------------------------------------
# InMemoryIssuersStore
# ---------------------------------------------------------------------------


class InMemoryIssuersStore:
    """Dict-backed store for jwt_issuers — used in tests and InMemory mode.

    All mutation methods use ``uuid.uuid4()`` and ``datetime.now(timezone.utc)``
    at call time only — never at import time.
    """

    def __init__(self) -> None:
        # id -> row dict
        self._rows: dict[str, JwtIssuerRow] = {}

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(
        self,
        org_id: str,
        name: str,
        issuer: str,
        audience: str,
        created_by: str,
        *,
        jwks_url: str | None = None,
        static_jwks_json: dict[str, Any] | None = None,
        algorithms: list[str] | None = None,
        enabled: bool = True,
    ) -> JwtIssuerRow:
        """Create and return a new issuer row.

        Raises
        ------
        ValueError
            If an issuer with the same (org_id, issuer) pair already exists.
        """
        for row in self._rows.values():
            if row["org_id"] == str(org_id) and row["issuer"] == issuer:
                raise ValueError(f"Issuer {issuer!r} already configured for this org.")

        now = datetime.now(timezone.utc)
        row_id = str(uuid.uuid4())
        row: JwtIssuerRow = {
            "id": row_id,
            "org_id": str(org_id),
            "name": name,
            "issuer": issuer,
            "jwks_url": jwks_url,
            "static_jwks_json": static_jwks_json,
            "algorithms": list(algorithms) if algorithms else ["RS256"],
            "audience": audience,
            "enabled": enabled,
            "created_by": str(created_by),
            "created_at": now,
            "updated_at": now,
        }
        self._rows[row_id] = row
        return deepcopy(row)

    async def update(
        self,
        issuer_id: str,
        org_id: str,
        *,
        name: str | None = None,
        jwks_url: Any = _UNSET,
        static_jwks_json: Any = _UNSET,
        algorithms: list[str] | None = None,
        audience: str | None = None,
        enabled: bool | None = None,
    ) -> JwtIssuerRow | None:
        """Update fields on an existing issuer row.

        Returns the updated row, or ``None`` if not found / wrong org.
        Pass ``_UNSET`` (the default) for nullable fields you do not want to
        change; pass ``None`` to explicitly clear them.
        """
        row = self._rows.get(str(issuer_id))
        if row is None or row["org_id"] != str(org_id):
            return None
        row = deepcopy(row)
        if name is not None:
            row["name"] = name
        if jwks_url is not _UNSET:
            row["jwks_url"] = jwks_url
        if static_jwks_json is not _UNSET:
            row["static_jwks_json"] = static_jwks_json
        if algorithms is not None:
            row["algorithms"] = list(algorithms)
        if audience is not None:
            row["audience"] = audience
        if enabled is not None:
            row["enabled"] = enabled
        row["updated_at"] = datetime.now(timezone.utc)
        self._rows[str(issuer_id)] = row
        return deepcopy(row)

    async def delete(self, issuer_id: str, org_id: str) -> bool:
        """Delete an issuer by id; return ``True`` if it existed."""
        row = self._rows.get(str(issuer_id))
        if row is None or row["org_id"] != str(org_id):
            return False
        del self._rows[str(issuer_id)]
        return True

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_for_org(self, org_id: str) -> list[JwtIssuerRow]:
        """Return all issuer rows for *org_id*, sorted by created_at asc."""
        rows = [
            deepcopy(r) for r in self._rows.values() if r["org_id"] == str(org_id)
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def get_by_id(self, issuer_id: str, org_id: str) -> JwtIssuerRow | None:
        """Return the row for *issuer_id* within *org_id*, or ``None``."""
        row = self._rows.get(str(issuer_id))
        if row is None or row["org_id"] != str(org_id):
            return None
        return deepcopy(row)

    async def get_enabled_by_iss(
        self, org_id: str, iss: str
    ) -> JwtIssuerRow | None:
        """Return the enabled issuer row matching (org_id, iss), or ``None``."""
        for row in self._rows.values():
            if (
                row["org_id"] == str(org_id)
                and row["issuer"] == iss
                and row["enabled"]
            ):
                return deepcopy(row)
        return None


# ---------------------------------------------------------------------------
# PgIssuersStore
# ---------------------------------------------------------------------------


class PgIssuersStore:
    """asyncpg-backed store for production use (migration 0024)."""

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(
        self,
        org_id: str,
        name: str,
        issuer: str,
        audience: str,
        created_by: str,
        *,
        jwks_url: str | None = None,
        static_jwks_json: dict[str, Any] | None = None,
        algorithms: list[str] | None = None,
        enabled: bool = True,
    ) -> JwtIssuerRow:
        """Insert a new issuer row; raise ``AppError`` on duplicate."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415
        from app.errors import AppError  # noqa: PLC0415

        import json  # noqa: PLC0415

        algs = list(algorithms) if algorithms else ["RS256"]
        now = datetime.now(timezone.utc)

        static_json_str: str | None = (
            json.dumps(static_jwks_json) if static_jwks_json is not None else None
        )

        try:
            row = await db_fetchrow(
                """
                INSERT INTO jwt_issuers
                    (org_id, name, issuer, jwks_url, static_jwks_json, algorithms,
                     audience, enabled, created_by, created_at, updated_at)
                VALUES
                    ($1::uuid, $2, $3, $4, $5::jsonb, $6::text[],
                     $7, $8, $9::uuid, $10, $10)
                RETURNING
                    id, org_id, name, issuer, jwks_url, static_jwks_json,
                    algorithms, audience, enabled, created_by, created_at, updated_at
                """,
                org_id,
                name,
                issuer,
                jwks_url,
                static_json_str,
                algs,
                audience,
                enabled,
                created_by,
                now,
            )
        except Exception as exc:  # noqa: BLE001
            _msg = str(exc).lower()
            if "unique" in _msg or "duplicate" in _msg:
                raise AppError(
                    "issuer_exists",
                    f"Issuer {issuer!r} is already configured for this org.",
                    409,
                ) from exc
            raise
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO jwt_issuers returned no row.")
        return _pg_row_to_dict(row)

    async def update(
        self,
        issuer_id: str,
        org_id: str,
        *,
        name: str | None = None,
        jwks_url: Any = _UNSET,
        static_jwks_json: Any = _UNSET,
        algorithms: list[str] | None = None,
        audience: str | None = None,
        enabled: bool | None = None,
    ) -> JwtIssuerRow | None:
        """Partial-update an issuer row; return updated row or ``None``."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        import json  # noqa: PLC0415

        # Build SET clause dynamically from provided non-sentinel args.
        sets: list[str] = ["updated_at = $3"]
        params: list[Any] = [issuer_id, org_id, datetime.now(timezone.utc)]
        idx = 4

        if name is not None:
            sets.append(f"name = ${idx}")
            params.append(name)
            idx += 1
        if jwks_url is not _UNSET:
            sets.append(f"jwks_url = ${idx}")
            params.append(jwks_url)
            idx += 1
        if static_jwks_json is not _UNSET:
            val = json.dumps(static_jwks_json) if static_jwks_json is not None else None
            sets.append(f"static_jwks_json = ${idx}::jsonb")
            params.append(val)
            idx += 1
        if algorithms is not None:
            sets.append(f"algorithms = ${idx}::text[]")
            params.append(list(algorithms))
            idx += 1
        if audience is not None:
            sets.append(f"audience = ${idx}")
            params.append(audience)
            idx += 1
        if enabled is not None:
            sets.append(f"enabled = ${idx}")
            params.append(enabled)
            idx += 1

        sql = f"""
            UPDATE jwt_issuers
            SET {', '.join(sets)}
            WHERE id = $1::uuid AND org_id = $2::uuid
            RETURNING
                id, org_id, name, issuer, jwks_url, static_jwks_json,
                algorithms, audience, enabled, created_by, created_at, updated_at
        """
        row = await db_fetchrow(sql, *params)
        if row is None:
            return None
        return _pg_row_to_dict(row)

    async def delete(self, issuer_id: str, org_id: str) -> bool:
        """Delete the issuer; return ``True`` if a row was removed."""
        from app.db import execute as db_execute  # noqa: PLC0415

        status = await db_execute(
            """
            DELETE FROM jwt_issuers
            WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            issuer_id,
            org_id,
        )
        return status.endswith("1") or (not status.endswith("0"))

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_for_org(self, org_id: str) -> list[JwtIssuerRow]:
        """Return all issuer rows for *org_id*, sorted by created_at asc."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT id, org_id, name, issuer, jwks_url, static_jwks_json,
                   algorithms, audience, enabled, created_by, created_at, updated_at
            FROM jwt_issuers
            WHERE org_id = $1::uuid
            ORDER BY created_at ASC
            """,
            org_id,
        )
        return [_pg_row_to_dict(r) for r in rows]

    async def get_by_id(self, issuer_id: str, org_id: str) -> JwtIssuerRow | None:
        """Return the row for *issuer_id* within *org_id*, or ``None``."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            SELECT id, org_id, name, issuer, jwks_url, static_jwks_json,
                   algorithms, audience, enabled, created_by, created_at, updated_at
            FROM jwt_issuers
            WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            issuer_id,
            org_id,
        )
        if row is None:
            return None
        return _pg_row_to_dict(row)

    async def get_enabled_by_iss(
        self, org_id: str, iss: str
    ) -> JwtIssuerRow | None:
        """Return the enabled issuer row matching (org_id, iss), or ``None``."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            SELECT id, org_id, name, issuer, jwks_url, static_jwks_json,
                   algorithms, audience, enabled, created_by, created_at, updated_at
            FROM jwt_issuers
            WHERE org_id = $1::uuid AND issuer = $2 AND enabled = TRUE
            LIMIT 1
            """,
            org_id,
            iss,
        )
        if row is None:
            return None
        return _pg_row_to_dict(row)


# ---------------------------------------------------------------------------
# Row conversion helper
# ---------------------------------------------------------------------------


def _pg_row_to_dict(row: Any) -> JwtIssuerRow:
    """Convert an asyncpg Record to a plain dict, normalising types."""
    d = dict(row)
    for key in ("id", "org_id", "created_by"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    for key in ("created_at", "updated_at"):
        val = d.get(key)
        if isinstance(val, datetime) and val.tzinfo is None:
            d[key] = val.replace(tzinfo=timezone.utc)
    # algorithms comes back as a list from asyncpg (text[]) — ensure it is.
    if "algorithms" in d and d["algorithms"] is None:
        d["algorithms"] = ["RS256"]
    # static_jwks_json from asyncpg may be a dict already (JSONB auto-parsed).
    return d


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: InMemoryIssuersStore | PgIssuersStore | None = None


def get_issuers_store() -> InMemoryIssuersStore | PgIssuersStore:
    """Return (or lazily create) the module-level issuers store.

    In production, returns a ``PgIssuersStore``.  Tests inject an
    ``InMemoryIssuersStore`` via :func:`set_issuers_store`.
    """
    global _store
    if _store is None:
        _store = PgIssuersStore()
    return _store


def set_issuers_store(store: InMemoryIssuersStore | PgIssuersStore | None) -> None:
    """Override the module-level store singleton.

    Pass an ``InMemoryIssuersStore`` for tests; pass ``None`` to reset to the
    production default (``PgIssuersStore``).
    """
    global _store
    _store = store
