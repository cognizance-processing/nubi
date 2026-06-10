"""Environment / version store — InMemoryEnvStore (tests) + PgEnvStore (prod).

Backs the project-scoped environments + resource versioning feature
(0005_environments_versions.sql):

- ``environments``           — named deployment targets (dev, prod, …) per project,
                               each bound to a git branch (``git_branch``,
                               ``last_synced_sha``).
- ``resource_versions``      — immutable snapshots of a resource's definition
                               (flow spec / board config / query config),
                               polymorphic over ``kind`` ('flow'|'board'|'query'),
                               with optional lineage (``parent_version_id``) and
                               git stamping (``git_commit_sha``).
- ``resource_environments``  — pointer table: which version each environment of
                               a resource is pinned to.

Provider
--------
``get_env_store()`` returns the configured singleton store.  By default it
returns a ``PgEnvStore`` (suitable for production); tests inject an
``InMemoryEnvStore`` via ``set_env_store(store)``.  This mirrors the pattern
used in ``app/flows/store.py``.

Design
------
- All methods return plain dicts with **str uuids** and **ISO datetimes**.
- ``create_version`` dedupes: when the canonical-JSON sha256 of the new config
  equals the LATEST version's hash, the existing version is returned with
  ``deduped=True`` instead of inserting a new row.
- ``InMemoryEnvStore`` uses ``deepcopy`` for all returned objects so callers
  cannot mutate internal state.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Environment = dict[str, Any]
ResourceVersion = dict[str, Any]
Pointer = dict[str, Any]

#: Valid polymorphic resource kinds (matches the CHECK constraint in the
#: environments/versions migration).
VALID_KINDS: frozenset[str] = frozenset({"flow", "board", "query"})


def default_git_branch(key: str) -> str:
    """Return the creation-default git branch for an environment *key*.

    The protected production env maps to ``'main'``; every other environment
    maps to its own key (``dev`` → ``dev``, ``staging`` → ``staging``, …).
    """
    return "main" if str(key) == "prod" else str(key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def config_hash(config: dict[str, Any]) -> str:
    """Return the sha256 hex digest of *config*'s canonical JSON form."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _iso(val: Any) -> Any:
    """Coerce a datetime to a tz-aware ISO string; pass other values through."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    return val


def _str_or_none(val: Any) -> str | None:
    return str(val) if val is not None else None


# ---------------------------------------------------------------------------
# InMemoryEnvStore
# ---------------------------------------------------------------------------


class InMemoryEnvStore:
    """Dict-backed store for environments, resource versions, and pointers.

    Environment shape
    -----------------
    ``{id, project_id, key, name, is_default, protected, position,
    git_branch, last_synced_sha, created_at}``

    ResourceVersion shape
    ---------------------
    ``{id, org_id, project_id, kind, resource_id, version, config,
    config_hash, message, parent_version_id, git_commit_sha, created_by,
    created_at}``

    Pointer shape
    -------------
    ``{kind, resource_id, environment_id, version_id, promoted_by, promoted_at}``
    """

    def __init__(self) -> None:
        self._envs: dict[str, Environment] = {}                       # env_id → env
        self._versions: dict[str, ResourceVersion] = {}               # version_id → version
        # (kind, resource_id, environment_id) → pointer
        self._pointers: dict[tuple[str, str, str], Pointer] = {}

    # ------------------------------------------------------------------
    # Environment operations
    # ------------------------------------------------------------------

    async def ensure_project_envs(self, project_id: str) -> list[Environment]:
        """Idempotently create the dev + prod pair for *project_id*.

        ``dev`` (checkpoint target, position 0, git branch ``dev``) and
        ``prod`` (default-resolved, protected, position 1, git branch
        ``main``).  Returns the project's full environment list.
        """
        existing = {e["key"] for e in await self.list_environments(project_id)}
        if "dev" not in existing:
            await self.create_environment(
                project_id, "dev", "Development",
                is_default=False, protected=False, position=0,
            )
        if "prod" not in existing:
            await self.create_environment(
                project_id, "prod", "Production",
                is_default=True, protected=True, position=1,
            )
        return await self.list_environments(project_id)

    async def list_environments(self, project_id: str) -> list[Environment]:
        """Return all environments for *project_id*, ordered by position."""
        rows = [
            deepcopy(e)
            for e in self._envs.values()
            if str(e["project_id"]) == str(project_id)
        ]
        rows.sort(key=lambda e: (e.get("position", 0), e["created_at"]))
        return rows

    async def create_environment(
        self,
        project_id: str,
        key: str,
        name: str,
        *,
        is_default: bool = False,
        protected: bool = False,
        position: int = 0,
        git_branch: str | None = None,
    ) -> Environment:
        """Create and store a new environment; return the stored dict.

        ``git_branch`` defaults to ``'main'`` for ``key='prod'`` and to the
        env key otherwise (see :func:`default_git_branch`).
        """
        env: Environment = {
            "id": str(uuid.uuid4()),
            "project_id": str(project_id),
            "key": str(key),
            "name": name,
            "is_default": bool(is_default),
            "protected": bool(protected),
            "position": int(position),
            "git_branch": str(git_branch) if git_branch else default_git_branch(key),
            "last_synced_sha": None,
            "created_at": _now_iso(),
        }
        self._envs[env["id"]] = env
        return deepcopy(env)

    async def update_environment(
        self, env_id: str, fields: dict[str, Any]
    ) -> Environment | None:
        """Update allowed fields on an environment; return the updated copy."""
        env = self._envs.get(str(env_id))
        if env is None:
            return None
        for field in (
            "name", "is_default", "protected", "position",
            "git_branch", "last_synced_sha",
        ):
            if field in fields and fields[field] is not None:
                env[field] = fields[field]
        return deepcopy(env)

    async def delete_environment(self, env_id: str) -> bool:
        """Delete an environment (and its pointers); return True if deleted.

        Policy checks (refuse delete of default/protected envs) are the
        caller's responsibility — the store deletes unconditionally.
        """
        env_id = str(env_id)
        if env_id not in self._envs:
            return False
        del self._envs[env_id]
        for ptr_key in [k for k in self._pointers if k[2] == env_id]:
            del self._pointers[ptr_key]
        return True

    async def get_environment(self, env_id: str) -> Environment | None:
        """Return a copy of the environment, or None if not found."""
        env = self._envs.get(str(env_id))
        return deepcopy(env) if env is not None else None

    async def get_environment_by_key(
        self, project_id: str, key: str
    ) -> Environment | None:
        """Return the environment with *key* in *project_id*, or None."""
        for env in self._envs.values():
            if str(env["project_id"]) == str(project_id) and env["key"] == str(key):
                return deepcopy(env)
        return None

    # ------------------------------------------------------------------
    # Version operations
    # ------------------------------------------------------------------

    def _versions_for(self, kind: str, resource_id: str) -> list[ResourceVersion]:
        rows = [
            v
            for v in self._versions.values()
            if v["kind"] == kind and str(v["resource_id"]) == str(resource_id)
        ]
        rows.sort(key=lambda v: v["version"])
        return rows

    async def create_version(
        self,
        org_id: str,
        project_id: str | None,
        kind: str,
        resource_id: str,
        config: dict[str, Any],
        created_by: str | None,
        message: str | None = None,
        parent_version_id: str | None = None,
        git_commit_sha: str | None = None,
    ) -> ResourceVersion:
        """Snapshot *config* as the next version; dedupe against the latest.

        Returns the version dict with a ``deduped`` flag: True when the
        canonical-JSON hash matched the latest version (no insert happened).
        ``parent_version_id`` defaults to the previous latest version's id
        when not supplied (lineage chain); ``git_commit_sha`` stays ``None``
        until a git commit is stamped on the version.
        """
        digest = config_hash(config)
        existing = self._versions_for(kind, resource_id)
        latest = existing[-1] if existing else None
        if latest is not None and latest["config_hash"] == digest:
            out = deepcopy(latest)
            out["deduped"] = True
            return out

        record: ResourceVersion = {
            "id": str(uuid.uuid4()),
            "org_id": str(org_id),
            "project_id": _str_or_none(project_id),
            "kind": kind,
            "resource_id": str(resource_id),
            "version": (latest["version"] if latest else 0) + 1,
            "config": deepcopy(config),
            "config_hash": digest,
            "message": message,
            "parent_version_id": _str_or_none(parent_version_id)
                or (latest["id"] if latest else None),
            "git_commit_sha": _str_or_none(git_commit_sha),
            "created_by": _str_or_none(created_by),
            "created_at": _now_iso(),
        }
        self._versions[record["id"]] = record
        out = deepcopy(record)
        out["deduped"] = False
        return out

    async def list_versions(
        self, kind: str, resource_id: str
    ) -> list[ResourceVersion]:
        """Return version summaries (no ``config``), newest first."""
        rows = self._versions_for(kind, resource_id)
        return [
            {
                "id": v["id"],
                "version": v["version"],
                "config_hash": v["config_hash"],
                "message": v["message"],
                "parent_version_id": v.get("parent_version_id"),
                "git_commit_sha": v.get("git_commit_sha"),
                "created_by": v["created_by"],
                "created_at": v["created_at"],
            }
            for v in reversed(rows)
        ]

    async def get_version(
        self, kind: str, resource_id: str, version: int
    ) -> ResourceVersion | None:
        """Return the full version (incl ``config``), or None."""
        for v in self._versions_for(kind, resource_id):
            if int(v["version"]) == int(version):
                return deepcopy(v)
        return None

    async def get_version_by_id(self, version_id: str) -> ResourceVersion | None:
        """Return the full version by id (incl ``config``), or None."""
        v = self._versions.get(str(version_id))
        return deepcopy(v) if v is not None else None

    async def set_version_git_commit(
        self, version_id: str, git_commit_sha: str
    ) -> ResourceVersion | None:
        """Stamp ``git_commit_sha`` on a version; return the updated copy."""
        v = self._versions.get(str(version_id))
        if v is None:
            return None
        v["git_commit_sha"] = _str_or_none(git_commit_sha)
        return deepcopy(v)

    # ------------------------------------------------------------------
    # Pointer operations
    # ------------------------------------------------------------------

    async def set_pointer(
        self,
        kind: str,
        resource_id: str,
        environment_id: str,
        version_id: str,
        promoted_by: str | None = None,
    ) -> Pointer:
        """Upsert the (kind, resource, environment) → version pointer."""
        pointer: Pointer = {
            "kind": kind,
            "resource_id": str(resource_id),
            "environment_id": str(environment_id),
            "version_id": str(version_id),
            "promoted_by": _str_or_none(promoted_by),
            "promoted_at": _now_iso(),
        }
        self._pointers[(kind, str(resource_id), str(environment_id))] = pointer
        return deepcopy(pointer)

    async def get_pointer(
        self, kind: str, resource_id: str, environment_id: str
    ) -> Pointer | None:
        """Return the pointer for (kind, resource, environment), or None."""
        ptr = self._pointers.get((kind, str(resource_id), str(environment_id)))
        return deepcopy(ptr) if ptr is not None else None

    async def list_pointers(self, kind: str, resource_id: str) -> list[Pointer]:
        """Return enriched pointers for a resource (env key + version number)."""
        out: list[Pointer] = []
        for ptr in self._pointers.values():
            if ptr["kind"] != kind or str(ptr["resource_id"]) != str(resource_id):
                continue
            env = self._envs.get(ptr["environment_id"])
            ver = self._versions.get(ptr["version_id"])
            if env is None or ver is None:
                continue
            out.append(
                {
                    "environment_id": ptr["environment_id"],
                    "env_key": env["key"],
                    "version_id": ptr["version_id"],
                    "version": ver["version"],
                    "promoted_at": ptr["promoted_at"],
                    "promoted_by": ptr["promoted_by"],
                }
            )
        out.sort(
            key=lambda p: self._envs.get(p["environment_id"], {}).get("position", 0)
        )
        return out

    async def list_pointers_bulk(
        self, kind: str, resource_ids: list[str]
    ) -> dict[str, list[Pointer]]:
        """Batched :meth:`list_pointers` for many resources at once.

        Returns ``{resource_id: [enriched pointer, ...]}`` with an entry for
        EVERY requested id (empty list when the resource has no pointers).
        Used by list endpoints to attach ``pinned_envs`` per row without an
        N+1 lookup.
        """
        wanted = {str(rid) for rid in resource_ids}
        out: dict[str, list[Pointer]] = {rid: [] for rid in wanted}
        for ptr in self._pointers.values():
            rid = str(ptr["resource_id"])
            if ptr["kind"] != kind or rid not in wanted:
                continue
            env = self._envs.get(ptr["environment_id"])
            ver = self._versions.get(ptr["version_id"])
            if env is None or ver is None:
                continue
            out[rid].append(
                {
                    "environment_id": ptr["environment_id"],
                    "env_key": env["key"],
                    "version_id": ptr["version_id"],
                    "version": ver["version"],
                    "promoted_at": ptr["promoted_at"],
                    "promoted_by": ptr["promoted_by"],
                }
            )
        for rid in out:
            out[rid].sort(
                key=lambda p: self._envs.get(p["environment_id"], {}).get("position", 0)
            )
        return out

    async def list_env_pointers(self, environment_id: str) -> list[Pointer]:
        """Return ALL pointers pinned in one environment (any kind/resource).

        Used by the git-env layer (push / take_env) to serialize an
        environment's full pinned state.  Ordered by (kind, resource_id).
        """
        eid = str(environment_id)
        out = [
            deepcopy(ptr)
            for ptr in self._pointers.values()
            if str(ptr["environment_id"]) == eid
        ]
        out.sort(key=lambda p: (p["kind"], p["resource_id"]))
        return out

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def delete_resource_data(self, kind: str, resource_id: str) -> None:
        """Remove all versions + pointers for a deleted resource."""
        rid = str(resource_id)
        for vid in [
            v["id"]
            for v in self._versions.values()
            if v["kind"] == kind and str(v["resource_id"]) == rid
        ]:
            del self._versions[vid]
        for key in [k for k in self._pointers if k[0] == kind and k[1] == rid]:
            del self._pointers[key]


# ---------------------------------------------------------------------------
# PgEnvStore — asyncpg-backed production implementation
# ---------------------------------------------------------------------------


def _row_to_env(row: Any) -> Environment:
    """Convert an asyncpg Record (or dict) to an Environment dict."""
    d = dict(row)
    for key in ("id", "project_id"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    d["created_at"] = _iso(d.get("created_at"))
    return d


def _row_to_version(row: Any) -> ResourceVersion:
    """Convert an asyncpg Record (or dict) to a ResourceVersion dict."""
    d = dict(row)
    for key in (
        "id", "org_id", "project_id", "resource_id",
        "parent_version_id", "created_by",
    ):
        if d.get(key) is not None:
            d[key] = str(d[key])
    d.setdefault("parent_version_id", None)
    d.setdefault("git_commit_sha", None)
    d["created_at"] = _iso(d.get("created_at"))
    if "config" in d and not isinstance(d["config"], dict):
        d["config"] = json.loads(d["config"])
    return d


def _row_to_pointer(row: Any) -> Pointer:
    """Convert an asyncpg Record (or dict) to a Pointer dict."""
    d = dict(row)
    for key in ("resource_id", "environment_id", "version_id", "promoted_by"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    d["promoted_at"] = _iso(d.get("promoted_at"))
    return d


class PgEnvStore:
    """asyncpg-backed environment/version store for production use.

    Uses the ``fetch`` / ``fetchrow`` / ``execute`` helpers from ``app.db``
    (lazy imports, like ``PgFlowStore``).  All SQL is parameterised; column
    names match the tables from 0005_environments_versions.sql.
    """

    # ------------------------------------------------------------------
    # Environment operations
    # ------------------------------------------------------------------

    async def ensure_project_envs(self, project_id: str) -> list[Environment]:
        """Idempotently create dev + prod for *project_id*.

        ``dev`` is bound to git branch ``dev``; ``prod`` (default, protected)
        is bound to ``main``.
        """
        from app.db import execute as db_execute  # noqa: PLC0415

        await db_execute(
            """
            INSERT INTO environments (project_id, key, name, is_default, protected, position, git_branch)
            VALUES ($1::uuid, 'dev', 'Development', false, false, 0, 'dev')
            ON CONFLICT (project_id, key) DO NOTHING
            """,
            project_id,
        )
        await db_execute(
            """
            INSERT INTO environments (project_id, key, name, is_default, protected, position, git_branch)
            VALUES ($1::uuid, 'prod', 'Production', true, true, 1, 'main')
            ON CONFLICT (project_id, key) DO NOTHING
            """,
            project_id,
        )
        return await self.list_environments(project_id)

    async def list_environments(self, project_id: str) -> list[Environment]:
        """Return all environments for *project_id*, ordered by position."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT * FROM environments
            WHERE project_id = $1::uuid
            ORDER BY position ASC, created_at ASC
            """,
            project_id,
        )
        return [_row_to_env(r) for r in rows]

    async def create_environment(
        self,
        project_id: str,
        key: str,
        name: str,
        *,
        is_default: bool = False,
        protected: bool = False,
        position: int = 0,
        git_branch: str | None = None,
    ) -> Environment:
        """Insert a new environment row and return the stored dict.

        ``git_branch`` defaults to ``'main'`` for ``key='prod'`` and to the
        env key otherwise (see :func:`default_git_branch`).
        """
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            INSERT INTO environments (project_id, key, name, is_default, protected, position, git_branch)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            project_id,
            str(key),
            name,
            bool(is_default),
            bool(protected),
            int(position),
            str(git_branch) if git_branch else default_git_branch(key),
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO environments returned no row.")
        return _row_to_env(row)

    async def update_environment(
        self, env_id: str, fields: dict[str, Any]
    ) -> Environment | None:
        """Update allowed fields on an environment; return the updated dict."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        updates: list[str] = []
        values: list[Any] = []
        idx = 1
        for field in (
            "name", "is_default", "protected", "position",
            "git_branch", "last_synced_sha",
        ):
            if field in fields and fields[field] is not None:
                updates.append(f"{field} = ${idx}")
                values.append(fields[field])
                idx += 1

        if not updates:
            return await self.get_environment(env_id)

        values.append(env_id)
        row = await db_fetchrow(
            f"UPDATE environments SET {', '.join(updates)} "
            f"WHERE id = ${idx}::uuid RETURNING *",
            *values,
        )
        return _row_to_env(row) if row is not None else None

    async def delete_environment(self, env_id: str) -> bool:
        """Delete an environment (pointers cascade); return True if deleted."""
        from app.db import execute as db_execute  # noqa: PLC0415

        status = await db_execute(
            "DELETE FROM environments WHERE id = $1::uuid",
            env_id,
        )
        try:
            return int(status.split()[-1]) > 0
        except (ValueError, IndexError, AttributeError):
            return False

    async def get_environment(self, env_id: str) -> Environment | None:
        """Return the environment dict, or None if not found."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT * FROM environments WHERE id = $1::uuid",
            env_id,
        )
        return _row_to_env(row) if row is not None else None

    async def get_environment_by_key(
        self, project_id: str, key: str
    ) -> Environment | None:
        """Return the environment with *key* in *project_id*, or None."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT * FROM environments WHERE project_id = $1::uuid AND key = $2",
            project_id,
            str(key),
        )
        return _row_to_env(row) if row is not None else None

    # ------------------------------------------------------------------
    # Version operations
    # ------------------------------------------------------------------

    async def create_version(
        self,
        org_id: str,
        project_id: str | None,
        kind: str,
        resource_id: str,
        config: dict[str, Any],
        created_by: str | None,
        message: str | None = None,
        parent_version_id: str | None = None,
        git_commit_sha: str | None = None,
    ) -> ResourceVersion:
        """Snapshot *config* as the next version; dedupe against the latest.

        ``parent_version_id`` defaults to the previous latest version's id
        when not supplied (lineage chain); ``git_commit_sha`` stays ``NULL``
        until a git commit is stamped on the version.
        """
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        digest = config_hash(config)
        latest = await db_fetchrow(
            """
            SELECT * FROM resource_versions
            WHERE kind = $1 AND resource_id = $2::uuid
            ORDER BY version DESC
            LIMIT 1
            """,
            kind,
            resource_id,
        )
        if latest is not None and dict(latest).get("config_hash") == digest:
            out = _row_to_version(latest)
            out["deduped"] = True
            return out

        parent_id = parent_version_id or (
            str(dict(latest)["id"]) if latest is not None else None
        )

        row = await db_fetchrow(
            """
            INSERT INTO resource_versions
                (org_id, project_id, kind, resource_id, version,
                 config, config_hash, message, parent_version_id,
                 git_commit_sha, created_by)
            VALUES ($1::uuid, $2::uuid, $3, $4::uuid,
                    COALESCE((SELECT max(version) FROM resource_versions
                              WHERE kind = $3 AND resource_id = $4::uuid), 0) + 1,
                    $5::jsonb, $6, $7, $8::uuid, $9, $10::uuid)
            RETURNING *
            """,
            org_id,
            project_id,
            kind,
            resource_id,
            json.dumps(config),
            digest,
            message,
            parent_id,
            git_commit_sha,
            created_by,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO resource_versions returned no row.")
        out = _row_to_version(row)
        out["deduped"] = False
        return out

    async def list_versions(
        self, kind: str, resource_id: str
    ) -> list[ResourceVersion]:
        """Return version summaries (no ``config``), newest first."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT id, version, config_hash, message, parent_version_id,
                   git_commit_sha, created_by, created_at
            FROM resource_versions
            WHERE kind = $1 AND resource_id = $2::uuid
            ORDER BY version DESC
            """,
            kind,
            resource_id,
        )
        out: list[ResourceVersion] = []
        for r in rows:
            d = dict(r)
            for key in ("id", "parent_version_id", "created_by"):
                if d.get(key) is not None:
                    d[key] = str(d[key])
            d["created_at"] = _iso(d.get("created_at"))
            out.append(d)
        return out

    async def get_version(
        self, kind: str, resource_id: str, version: int
    ) -> ResourceVersion | None:
        """Return the full version (incl ``config``), or None."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            SELECT * FROM resource_versions
            WHERE kind = $1 AND resource_id = $2::uuid AND version = $3
            """,
            kind,
            resource_id,
            int(version),
        )
        return _row_to_version(row) if row is not None else None

    async def get_version_by_id(self, version_id: str) -> ResourceVersion | None:
        """Return the full version by id (incl ``config``), or None."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT * FROM resource_versions WHERE id = $1::uuid",
            version_id,
        )
        return _row_to_version(row) if row is not None else None

    async def set_version_git_commit(
        self, version_id: str, git_commit_sha: str
    ) -> ResourceVersion | None:
        """Stamp ``git_commit_sha`` on a version; return the updated dict."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            UPDATE resource_versions SET git_commit_sha = $1
            WHERE id = $2::uuid RETURNING *
            """,
            git_commit_sha,
            version_id,
        )
        return _row_to_version(row) if row is not None else None

    # ------------------------------------------------------------------
    # Pointer operations
    # ------------------------------------------------------------------

    async def set_pointer(
        self,
        kind: str,
        resource_id: str,
        environment_id: str,
        version_id: str,
        promoted_by: str | None = None,
    ) -> Pointer:
        """Upsert the (kind, resource, environment) → version pointer."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            INSERT INTO resource_environments
                (kind, resource_id, environment_id, version_id, promoted_by)
            VALUES ($1, $2::uuid, $3::uuid, $4::uuid, $5::uuid)
            ON CONFLICT (kind, resource_id, environment_id)
            DO UPDATE SET version_id  = EXCLUDED.version_id,
                          promoted_by = EXCLUDED.promoted_by,
                          promoted_at = now()
            RETURNING *
            """,
            kind,
            resource_id,
            environment_id,
            version_id,
            promoted_by,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("UPSERT INTO resource_environments returned no row.")
        return _row_to_pointer(row)

    async def get_pointer(
        self, kind: str, resource_id: str, environment_id: str
    ) -> Pointer | None:
        """Return the pointer for (kind, resource, environment), or None."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            SELECT * FROM resource_environments
            WHERE kind = $1 AND resource_id = $2::uuid AND environment_id = $3::uuid
            """,
            kind,
            resource_id,
            environment_id,
        )
        return _row_to_pointer(row) if row is not None else None

    async def list_pointers(self, kind: str, resource_id: str) -> list[Pointer]:
        """Return enriched pointers for a resource (env key + version number)."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT re.environment_id,
                   e.key       AS env_key,
                   re.version_id,
                   rv.version,
                   re.promoted_at,
                   re.promoted_by
            FROM resource_environments re
            JOIN environments e       ON e.id  = re.environment_id
            JOIN resource_versions rv ON rv.id = re.version_id
            WHERE re.kind = $1 AND re.resource_id = $2::uuid
            ORDER BY e.position ASC
            """,
            kind,
            resource_id,
        )
        out: list[Pointer] = []
        for r in rows:
            d = dict(r)
            for key in ("environment_id", "version_id", "promoted_by"):
                if d.get(key) is not None:
                    d[key] = str(d[key])
            d["promoted_at"] = _iso(d.get("promoted_at"))
            out.append(d)
        return out

    async def list_pointers_bulk(
        self, kind: str, resource_ids: list[str]
    ) -> dict[str, list[Pointer]]:
        """Batched :meth:`list_pointers` for many resources at once.

        Returns ``{resource_id: [enriched pointer, ...]}`` with an entry for
        EVERY requested id (empty list when the resource has no pointers).
        One ``ANY($2::uuid[])`` query — used by list endpoints to attach
        ``pinned_envs`` per row without an N+1 lookup.
        """
        from app.db import fetch as db_fetch  # noqa: PLC0415

        wanted = [str(rid) for rid in resource_ids]
        out: dict[str, list[Pointer]] = {rid: [] for rid in wanted}
        if not wanted:
            return out
        rows = await db_fetch(
            """
            SELECT re.resource_id,
                   re.environment_id,
                   e.key       AS env_key,
                   re.version_id,
                   rv.version,
                   re.promoted_at,
                   re.promoted_by
            FROM resource_environments re
            JOIN environments e       ON e.id  = re.environment_id
            JOIN resource_versions rv ON rv.id = re.version_id
            WHERE re.kind = $1 AND re.resource_id = ANY($2::uuid[])
            ORDER BY re.resource_id, e.position ASC
            """,
            kind,
            wanted,
        )
        for r in rows:
            d = dict(r)
            rid = str(d.pop("resource_id"))
            for key in ("environment_id", "version_id", "promoted_by"):
                if d.get(key) is not None:
                    d[key] = str(d[key])
            d["promoted_at"] = _iso(d.get("promoted_at"))
            out.setdefault(rid, []).append(d)
        return out

    async def list_env_pointers(self, environment_id: str) -> list[Pointer]:
        """Return ALL pointers pinned in one environment (any kind/resource).

        Used by the git-env layer (push / take_env) to serialize an
        environment's full pinned state.  Ordered by (kind, resource_id).
        """
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT * FROM resource_environments
            WHERE environment_id = $1::uuid
            ORDER BY kind ASC, resource_id ASC
            """,
            environment_id,
        )
        return [_row_to_pointer(r) for r in rows]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def delete_resource_data(self, kind: str, resource_id: str) -> None:
        """Remove all versions + pointers for a deleted resource."""
        from app.db import execute as db_execute  # noqa: PLC0415

        await db_execute(
            "DELETE FROM resource_environments WHERE kind = $1 AND resource_id = $2::uuid",
            kind,
            resource_id,
        )
        await db_execute(
            "DELETE FROM resource_versions WHERE kind = $1 AND resource_id = $2::uuid",
            kind,
            resource_id,
        )


# ---------------------------------------------------------------------------
# Module-level singleton / provider
# ---------------------------------------------------------------------------

#: Active singleton — None means "lazily create PgEnvStore on first call".
_env_store: InMemoryEnvStore | PgEnvStore | None = None


def get_env_store() -> InMemoryEnvStore | PgEnvStore:
    """Return (or lazily create) the module-level environment store.

    In production (no override via ``set_env_store``), returns a ``PgEnvStore``
    instance.  Tests inject an ``InMemoryEnvStore`` via ``set_env_store``
    before making requests.
    """
    global _env_store
    if _env_store is None:
        _env_store = PgEnvStore()
    return _env_store


async def attach_pinned_envs(kind: str, rows: list[dict[str, Any]]) -> None:
    """Attach ``pinned_envs: [env_key, ...]`` to every row dict, in place.

    Used by list endpoints (boards/queries/flows) so the UI can render
    "not in <env>" badges.  ALWAYS sets the key (``[]`` default); the batched
    pointer lookup itself is best-effort — a missing/failing environments
    layer never breaks a list endpoint.
    """
    for row in rows:
        row["pinned_envs"] = []
    if not rows:
        return
    try:
        bulk = await get_env_store().list_pointers_bulk(
            kind, [str(r["id"]) for r in rows]
        )
    except Exception:  # noqa: BLE001 — env layer must never break listing.
        return
    for row in rows:
        row["pinned_envs"] = [
            p["env_key"] for p in bulk.get(str(row["id"]), [])
        ]


async def resolve_default_env_config(
    kind: str,
    resource_id: str,
    project_id: str | None,
    org_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a resource's pinned config in its project's DEFAULT environment.

    Strict-visibility rule for embed/viewer identities (the default env is the
    protected ``prod`` env in standard projects):

    - default env has a pointer for the resource → return that pinned
      version's ``config`` (the caller substitutes it for the draft);
    - default env is PROTECTED and has NO pointer → raise ``AppError`` 404
      (``not_published``) — drafts are never visible to embed identities in a
      protected environment;
    - default env exists, is NOT protected, and has no pointer → return
      ``None`` (caller serves the draft);
    - no resolvable project / default env / version (including any lookup
      failure) → return ``None`` (draft; environments layer is optional).

    ``project_id`` falls back to the org's default project when missing.
    """
    pid = project_id
    try:
        if not pid and org_id:
            from app.routes._org import resolve_org_default_project_id  # noqa: PLC0415

            pid = await resolve_org_default_project_id(org_id)
        if not pid:
            return None
        store = get_env_store()
        envs = await store.list_environments(str(pid))
        default_env = next((e for e in envs if e.get("is_default")), None)
        if default_env is None:
            return None
        pointer = await store.get_pointer(kind, str(resource_id), default_env["id"])
        protected = bool(default_env.get("protected"))
        env_key = default_env.get("key")
    except Exception:  # noqa: BLE001 — env layer is optional; serve the draft.
        return None

    if pointer is None:
        if protected:
            from app.errors import AppError  # noqa: PLC0415

            raise AppError(
                "not_published",
                f"Resource is not published to the {env_key!r} environment.",
                404,
            )
        return None

    try:
        version = await get_env_store().get_version_by_id(pointer["version_id"])
    except Exception:  # noqa: BLE001
        return None
    if version is None:
        return None
    config = version.get("config")
    return config if isinstance(config, dict) else None


def set_env_store(store: InMemoryEnvStore | PgEnvStore | None) -> None:
    """Override the module-level store singleton.

    Pass an ``InMemoryEnvStore`` instance to inject a test double.
    Pass ``None`` to reset so the next ``get_env_store()`` call creates a
    fresh ``PgEnvStore`` (the production default).
    """
    global _env_store
    _env_store = store
