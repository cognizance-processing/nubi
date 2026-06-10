"""Variable store implementations — InMemoryVarStore (tests) + PgVarStore (prod).

The variables store is the PERSISTENT key/value store backing workstream A5.
Each variable is org-scoped and optionally project-scoped (``project_id`` NULL
means an org-global variable).  ``value`` is arbitrary JSON.

This is the persistent store ONLY — the run-scoped overlay and the Python
``set_var`` SDK are a LATER slice and are deliberately NOT implemented here.

``InMemoryVarStore`` is a dict-backed store used in tests.  ``PgVarStore`` is
the asyncpg-backed production store mapping each method to a parameterised SQL
query against the ``variables`` table (from 0007_variables.sql).  Rows are
converted to plain dicts; jsonb and datetime values match the shape produced by
``InMemoryVarStore``.

Provider
--------
``get_var_store()`` returns the configured singleton store.  By default it
returns a ``PgVarStore`` (suitable for production); tests inject an
``InMemoryVarStore`` via ``set_var_store(store)``.  This mirrors the pattern
used in ``app/flows/store.py``.

Design
------
- All mutation methods use ``uuid.uuid4()`` and ``datetime.now(timezone.utc)``
  **at call time only** — never at module/class import time.
- ``set_var_store()`` lets tests swap the singleton for an injected store
  without touching route signatures.
- ``InMemoryVarStore`` uses ``deepcopy`` for all returned objects so that
  callers cannot mutate internal state.
- Datetimes are always tz-aware UTC; uuids are strings.
- ``value`` is arbitrary JSON (dict, list, scalar, or ``None``).
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Variable = dict[str, Any]

# Sentinel uuid used to fold the org-global scope (project_id NULL) onto a
# single key — mirrors the COALESCE(project_id, <zero-uuid>) unique index in
# 0007_variables.sql so the InMemory and Pg stores agree on uniqueness.
_GLOBAL_SCOPE = "00000000-0000-0000-0000-000000000000"


def _scope_key(project_id: str | None) -> str:
    """Return the project scope token (sentinel for org-global)."""
    return str(project_id) if project_id is not None else _GLOBAL_SCOPE


# ---------------------------------------------------------------------------
# InMemoryVarStore
# ---------------------------------------------------------------------------


class InMemoryVarStore:
    """Dict-backed store for variables.

    Variable shape
    --------------
    ``{id, org_id, project_id, key, value(JSON), updated_by, created_at,
    updated_at}``

    Uniqueness is per ``(org_id, project-or-global, key)`` — a project-scoped
    variable and an org-global variable with the same key are distinct rows.
    """

    def __init__(self) -> None:
        # (org_id, scope_token, key) → Variable
        self._vars: dict[tuple[str, str, str], Variable] = {}

    async def list_vars(
        self, org_id: str, project_id: str | None = None
    ) -> list[Variable]:
        """Return variables for *org_id*, sorted by key.

        When *project_id* is provided the result is scoped to that project; when
        ``None`` only the org-global variables are returned.
        """
        scope = _scope_key(project_id)
        rows = [
            deepcopy(v)
            for (o, s, _k), v in self._vars.items()
            if o == str(org_id) and s == scope
        ]
        rows.sort(key=lambda r: r["key"])
        return rows

    async def get_var(
        self, org_id: str, key: str, project_id: str | None = None
    ) -> Variable | None:
        """Return the variable, or ``None`` if not found in this scope."""
        v = self._vars.get((str(org_id), _scope_key(project_id), str(key)))
        return deepcopy(v) if v is not None else None

    async def set_var(
        self,
        org_id: str,
        key: str,
        value: Any,
        project_id: str | None = None,
        updated_by: str | None = None,
    ) -> Variable:
        """Upsert a variable; return the stored dict.

        Insert on first write, update ``value`` / ``updated_by`` /
        ``updated_at`` on subsequent writes for the same scope+key.
        """
        idx = (str(org_id), _scope_key(project_id), str(key))
        now = datetime.now(timezone.utc)
        existing = self._vars.get(idx)
        if existing is None:
            record: Variable = {
                "id": str(uuid.uuid4()),
                "org_id": str(org_id),
                "project_id": str(project_id) if project_id is not None else None,
                "key": str(key),
                "value": deepcopy(value),
                "updated_by": str(updated_by) if updated_by is not None else None,
                "created_at": now,
                "updated_at": now,
            }
            self._vars[idx] = record
            return deepcopy(record)
        existing["value"] = deepcopy(value)
        existing["updated_by"] = str(updated_by) if updated_by is not None else None
        existing["updated_at"] = now
        return deepcopy(existing)

    async def delete_var(
        self, org_id: str, key: str, project_id: str | None = None
    ) -> bool:
        """Delete a variable; return ``True`` if a row was removed."""
        idx = (str(org_id), _scope_key(project_id), str(key))
        if idx not in self._vars:
            return False
        del self._vars[idx]
        return True


# ---------------------------------------------------------------------------
# PgVarStore — asyncpg-backed production implementation
# ---------------------------------------------------------------------------


def _row_to_var(row: Any) -> Variable:
    """Convert an asyncpg Record (or dict) to a Variable dict.

    Ensures uuids are strings, datetimes are tz-aware UTC, and ``value`` jsonb
    is returned as a parsed Python object.
    """
    d = dict(row)
    for key in ("id", "org_id", "project_id", "updated_by"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    for key in ("created_at", "updated_at"):
        val = d.get(key)
        if isinstance(val, datetime) and val.tzinfo is None:
            d[key] = val.replace(tzinfo=timezone.utc)
    # asyncpg returns jsonb already parsed; normalise stringified jsonb.
    if "value" in d and isinstance(d["value"], (str, bytes, bytearray)):
        import json  # noqa: PLC0415

        try:
            d["value"] = json.loads(d["value"])
        except Exception:  # noqa: BLE001
            pass
    return d


class PgVarStore:
    """asyncpg-backed variable store for production use.

    Uses the ``fetch`` / ``fetchrow`` / ``execute`` helpers from ``app.db``.
    All SQL is parameterised with ``$N`` placeholders.  Column names match the
    ``variables`` table from 0007_variables.sql.  Rows are converted to plain
    dicts matching the shape produced by ``InMemoryVarStore``.
    """

    async def list_vars(
        self, org_id: str, project_id: str | None = None
    ) -> list[Variable]:
        """Return variables for *org_id* in the given scope, sorted by key."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        if project_id is not None:
            rows = await db_fetch(
                "SELECT * FROM variables WHERE org_id = $1::uuid "
                "AND project_id = $2::uuid ORDER BY key ASC",
                org_id,
                project_id,
            )
        else:
            rows = await db_fetch(
                "SELECT * FROM variables WHERE org_id = $1::uuid "
                "AND project_id IS NULL ORDER BY key ASC",
                org_id,
            )
        return [_row_to_var(r) for r in rows]

    async def get_var(
        self, org_id: str, key: str, project_id: str | None = None
    ) -> Variable | None:
        """Return the variable, or ``None`` if not found in this scope."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        if project_id is not None:
            row = await db_fetchrow(
                "SELECT * FROM variables WHERE org_id = $1::uuid "
                "AND project_id = $2::uuid AND key = $3",
                org_id,
                project_id,
                key,
            )
        else:
            row = await db_fetchrow(
                "SELECT * FROM variables WHERE org_id = $1::uuid "
                "AND project_id IS NULL AND key = $2",
                org_id,
                key,
            )
        return _row_to_var(row) if row is not None else None

    async def set_var(
        self,
        org_id: str,
        key: str,
        value: Any,
        project_id: str | None = None,
        updated_by: str | None = None,
    ) -> Variable:
        """Upsert a variable and return the stored dict.

        Conflict target is the COALESCE(project_id, <zero-uuid>) unique index
        from 0007_variables.sql, expressed here as the matching index predicate
        so ON CONFLICT resolves the same scope (project-or-global, key).
        """
        import json  # noqa: PLC0415
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            INSERT INTO variables (org_id, project_id, key, value, updated_by)
            VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::uuid)
            ON CONFLICT (
                org_id,
                COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid),
                key
            )
            DO UPDATE SET value = EXCLUDED.value,
                          updated_by = EXCLUDED.updated_by,
                          updated_at = now()
            RETURNING *
            """,
            org_id,
            project_id,
            key,
            json.dumps(value),
            updated_by,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("UPSERT INTO variables returned no row.")
        return _row_to_var(row)

    async def delete_var(
        self, org_id: str, key: str, project_id: str | None = None
    ) -> bool:
        """Delete a variable; return ``True`` if a row was removed."""
        from app.db import execute as db_execute  # noqa: PLC0415

        if project_id is not None:
            status = await db_execute(
                "DELETE FROM variables WHERE org_id = $1::uuid "
                "AND project_id = $2::uuid AND key = $3",
                org_id,
                project_id,
                key,
            )
        else:
            status = await db_execute(
                "DELETE FROM variables WHERE org_id = $1::uuid "
                "AND project_id IS NULL AND key = $2",
                org_id,
                key,
            )
        try:
            count = int(status.split()[-1])
        except (ValueError, IndexError):
            count = 0
        return count > 0


# ---------------------------------------------------------------------------
# Module-level singleton / provider
# ---------------------------------------------------------------------------

#: Active singleton — None means "lazily create PgVarStore on first call".
_var_store: InMemoryVarStore | PgVarStore | None = None


def get_var_store() -> InMemoryVarStore | PgVarStore:
    """Return (or lazily create) the module-level variable store.

    In production (no override via ``set_var_store``), returns a ``PgVarStore``
    instance.  Tests inject an ``InMemoryVarStore`` via ``set_var_store`` before
    making requests.  Both stores expose the same interface, so route handlers
    keep working without changes.
    """
    global _var_store
    if _var_store is None:
        _var_store = PgVarStore()
    return _var_store


def set_var_store(store: InMemoryVarStore | PgVarStore | None) -> None:
    """Override the module-level store singleton.

    Pass an ``InMemoryVarStore`` instance to inject a test double.  Pass
    ``None`` to reset so the next ``get_var_store()`` call creates a fresh
    ``PgVarStore`` (the production default).
    """
    global _var_store
    _var_store = store


async def load_vars_namespace(
    org_id: str | None, project_id: str | None = None
) -> dict[str, Any]:
    """Return the ``{{ vars.* }}`` namespace for an org (+ optional project).

    Org-global variables (project_id NULL) overlaid with project-scoped ones —
    a project var SHADOWS an org-global var of the same key. Best-effort: an
    empty/None org or any store error yields ``{}`` so a missing var surfaces as
    a clear template miss rather than ever breaking a query/flow run. Shared by
    the /query endpoint and the flow runtime so both resolve vars identically.
    """
    if not org_id:
        return {}
    store = get_var_store()
    try:
        merged: dict[str, Any] = {
            r["key"]: r["value"] for r in await store.list_vars(org_id, None)
        }
        if project_id:
            for r in await store.list_vars(org_id, project_id):
                merged[r["key"]] = r["value"]
        return merged
    except Exception:  # noqa: BLE001 — vars are advisory; never break the run
        return {}
