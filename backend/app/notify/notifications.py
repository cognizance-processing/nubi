"""In-app notification feed store — provider pattern (Pg + InMemory).

A notification is a row in the ``notifications`` table (migration 0011). It is
either **targeted** (``user_id`` set ⇒ visible to one member) or a **broadcast**
(``user_id`` NULL ⇒ visible to every member of the org).

Read-state
----------
Targeted rows carry their read timestamp directly in ``notifications.read_at``.
Broadcasts can't: one row is shared by every member, so per-user read-state lives
in ``notification_reads(notification_id, user_id, read_at)``. ``list_for_user``
LEFT-JOINs that table for the calling user and folds the result into a single
``read_at`` field, so the caller sees a uniform shape regardless of kind.

Provider pattern
----------------
Mirrors :mod:`app.auth.api_keys` / :mod:`app.connectors.secret_store`: a
module-level singleton via :func:`get_notification_store`; tests swap in an
:class:`InMemoryNotificationStore` via :func:`set_notification_store_for_tests`.
Every read/write is scoped by ``org_id`` to prevent cross-tenant leakage.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_VALID_SEVERITY = {"info", "success", "warning", "error"}


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return the API-safe shape of a notification row.

    ``read_at`` is the *resolved* per-user value (folded from notification_reads
    for broadcasts) and ``read`` is a convenience boolean.
    """
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            metadata = {}
    read_at = row.get("read_at")
    return {
        "id": str(row["id"]),
        "type": row.get("type"),
        "severity": row.get("severity") or "info",
        "title": row.get("title"),
        "body": row.get("body") or "",
        "link": row.get("link"),
        "metadata": metadata or {},
        "broadcast": row.get("user_id") is None,
        "read": read_at is not None,
        "read_at": _iso(read_at),
        "created_at": _iso(row.get("created_at")),
    }


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class NotificationStore:
    """Interface for notification persistence (structural duck-typing, no ABC)."""

    async def create(
        self,
        org_id: str,
        *,
        type: str,
        title: str,
        body: str = "",
        severity: str = "info",
        link: str | None = None,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a notification (broadcast when *user_id* is None). Return the row."""
        raise NotImplementedError

    async def list_for_user(
        self,
        org_id: str,
        user_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the user's feed: targeted rows + org broadcasts, newest first.

        Read-state is resolved per-user (broadcasts via ``notification_reads``).
        ``before`` is an ISO timestamp cursor (rows strictly older than it).
        """
        raise NotImplementedError

    async def mark_read(self, notification_id: str, user_id: str) -> bool:
        """Mark one notification read for *user_id*. Return True if it applied."""
        raise NotImplementedError

    async def mark_all_read(self, org_id: str, user_id: str) -> int:
        """Mark every visible notification read for *user_id*. Return the count."""
        raise NotImplementedError

    async def unread_count(self, org_id: str, user_id: str) -> int:
        """Return the number of unread notifications visible to *user_id*."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgNotificationStore(NotificationStore):
    """asyncpg-backed store over ``notifications`` + ``notification_reads``."""

    async def create(
        self,
        org_id: str,
        *,
        type: str,
        title: str,
        body: str = "",
        severity: str = "info",
        link: str | None = None,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        from app.db import fetchrow  # local import to avoid circular load

        sev = severity if severity in _VALID_SEVERITY else "info"
        row = await fetchrow(
            """
            INSERT INTO notifications
                (id, org_id, user_id, type, severity, title, body, link, metadata)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::jsonb)
            RETURNING id, org_id, user_id, type, severity, title, body, link,
                      metadata, read_at, created_at
            """,
            str(uuid.uuid4()),
            org_id,
            str(user_id) if user_id else None,
            type,
            sev,
            title,
            body or "",
            link,
            json.dumps(metadata or {}),
        )
        return dict(row) if row is not None else {}

    async def list_for_user(
        self,
        org_id: str,
        user_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        from app.db import fetch  # local import

        limit = max(1, min(int(limit or 50), 200))
        # Resolve read-state: targeted rows use their own read_at; broadcasts use
        # the calling user's notification_reads row (if any).
        params: list[Any] = [org_id, user_id]
        clauses = [
            "n.org_id = $1::uuid",
            "(n.user_id IS NULL OR n.user_id = $2::uuid)",
        ]
        if before:
            params.append(before)
            clauses.append(f"n.created_at < ${len(params)}::timestamptz")
        where = " AND ".join(clauses)
        having = ""
        if unread_only:
            having = "AND (CASE WHEN n.user_id IS NULL THEN nr.read_at ELSE n.read_at END) IS NULL"
        params.append(limit)
        rows = await fetch(
            f"""
            SELECT n.id, n.org_id, n.user_id, n.type, n.severity, n.title, n.body,
                   n.link, n.metadata, n.created_at,
                   CASE WHEN n.user_id IS NULL THEN nr.read_at ELSE n.read_at END
                       AS read_at
            FROM notifications n
            LEFT JOIN notification_reads nr
                   ON nr.notification_id = n.id AND nr.user_id = $2::uuid
            WHERE {where} {having}
            ORDER BY n.created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        return [_public_row(dict(r)) for r in rows]

    async def mark_read(self, notification_id: str, user_id: str) -> bool:
        from app.db import execute, fetchrow  # local import

        row = await fetchrow(
            "SELECT id, user_id FROM notifications WHERE id = $1::uuid",
            notification_id,
        )
        if row is None:
            return False
        if row["user_id"] is None:
            # Broadcast: record a per-user read receipt (idempotent).
            await execute(
                """
                INSERT INTO notification_reads (notification_id, user_id)
                VALUES ($1::uuid, $2::uuid)
                ON CONFLICT (notification_id, user_id) DO NOTHING
                """,
                notification_id,
                user_id,
            )
            return True
        # Targeted: only the addressee can mark it.
        if str(row["user_id"]) != str(user_id):
            return False
        await execute(
            "UPDATE notifications SET read_at = now() WHERE id = $1::uuid AND read_at IS NULL",
            notification_id,
        )
        return True

    async def mark_all_read(self, org_id: str, user_id: str) -> int:
        from app.db import execute  # local import

        # Targeted rows for this user.
        await execute(
            """
            UPDATE notifications
            SET read_at = now()
            WHERE org_id = $1::uuid AND user_id = $2::uuid AND read_at IS NULL
            """,
            org_id,
            user_id,
        )
        # Broadcast rows: backfill missing read receipts for this user.
        await execute(
            """
            INSERT INTO notification_reads (notification_id, user_id)
            SELECT n.id, $2::uuid
            FROM notifications n
            WHERE n.org_id = $1::uuid AND n.user_id IS NULL
            ON CONFLICT (notification_id, user_id) DO NOTHING
            """,
            org_id,
            user_id,
        )
        return await self.unread_count(org_id, user_id)

    async def unread_count(self, org_id: str, user_id: str) -> int:
        from app.db import fetchrow  # local import

        row = await fetchrow(
            """
            SELECT count(*) AS c
            FROM notifications n
            LEFT JOIN notification_reads nr
                   ON nr.notification_id = n.id AND nr.user_id = $2::uuid
            WHERE n.org_id = $1::uuid
              AND (n.user_id IS NULL OR n.user_id = $2::uuid)
              AND (CASE WHEN n.user_id IS NULL THEN nr.read_at ELSE n.read_at END) IS NULL
            """,
            org_id,
            user_id,
        )
        if row is None:
            return 0
        try:
            return int(row["c"])
        except (KeyError, TypeError, ValueError):
            return 0


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryNotificationStore(NotificationStore):
    """Dict-backed notification store for tests (no DB)."""

    def __init__(self) -> None:
        # notification_id -> row dict
        self._store: dict[str, dict[str, Any]] = {}
        # (notification_id, user_id) -> read_at datetime
        self._reads: dict[tuple[str, str], datetime] = {}

    def reset(self) -> None:
        self._store.clear()
        self._reads.clear()

    async def create(
        self,
        org_id: str,
        *,
        type: str,
        title: str,
        body: str = "",
        severity: str = "info",
        link: str | None = None,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        notification_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)
        row = {
            "id": notification_id,
            "org_id": str(org_id),
            "user_id": str(user_id) if user_id else None,
            "type": type,
            "severity": severity if severity in _VALID_SEVERITY else "info",
            "title": title,
            "body": body or "",
            "link": link,
            "metadata": dict(metadata or {}),
            "read_at": None,
            "created_at": now,
        }
        self._store[notification_id] = row
        return dict(row)

    def _resolved_read_at(self, row: dict[str, Any], user_id: str) -> Any:
        if row["user_id"] is None:
            return self._reads.get((row["id"], str(user_id)))
        return row["read_at"]

    def _visible(self, org_id: str, user_id: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self._store.values()
            if r["org_id"] == str(org_id)
            and (r["user_id"] is None or r["user_id"] == str(user_id))
        ]

    async def list_for_user(
        self,
        org_id: str,
        user_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._visible(org_id, user_id)
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        out: list[dict[str, Any]] = []
        before_dt = None
        if before:
            try:
                before_dt = datetime.fromisoformat(before)
            except (ValueError, TypeError):
                before_dt = None
        for r in rows:
            if before_dt is not None and r["created_at"] >= before_dt:
                continue
            read_at = self._resolved_read_at(r, user_id)
            if unread_only and read_at is not None:
                continue
            view = dict(r)
            view["read_at"] = read_at
            out.append(_public_row(view))
            if len(out) >= max(1, min(int(limit or 50), 200)):
                break
        return out

    async def mark_read(self, notification_id: str, user_id: str) -> bool:
        row = self._store.get(str(notification_id))
        if row is None:
            return False
        if row["user_id"] is None:
            self._reads.setdefault(
                (str(notification_id), str(user_id)), datetime.now(tz=timezone.utc)
            )
            return True
        if row["user_id"] != str(user_id):
            return False
        if row["read_at"] is None:
            row["read_at"] = datetime.now(tz=timezone.utc)
        return True

    async def mark_all_read(self, org_id: str, user_id: str) -> int:
        now = datetime.now(tz=timezone.utc)
        for r in self._visible(org_id, user_id):
            if r["user_id"] is None:
                self._reads.setdefault((r["id"], str(user_id)), now)
            elif r["read_at"] is None:
                r["read_at"] = now
        return await self.unread_count(org_id, user_id)

    async def unread_count(self, org_id: str, user_id: str) -> int:
        count = 0
        for r in self._visible(org_id, user_id):
            if self._resolved_read_at(r, user_id) is None:
                count += 1
        return count


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_store: Optional[NotificationStore] = None


def set_notification_store_for_tests(store: NotificationStore | None) -> None:
    """Inject a test double (or pass None to restore the default Pg store)."""
    global _store
    _store = store


def get_notification_store() -> NotificationStore:
    """Return the active :class:`NotificationStore` singleton (lazy Pg default)."""
    global _store
    if _store is None:
        _store = PgNotificationStore()
    return _store
