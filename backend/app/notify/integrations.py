"""Per-org connected integrations — CRUD + live channel resolution.

A *connected integration* is one row in ``org_integrations`` (non-secret config
in the ``config`` jsonb) paired with one encrypted blob in ``integration_secrets``
(AES-256-GCM, mirroring ``app.connectors.secret_store``). One integration powers
BOTH inbound chat and outbound alerts for the org.

Two store implementations (provider pattern, mirroring
:mod:`app.connectors.secret_store` and :mod:`app.auth.api_keys`)
----------------------------------------------------------------
``PgIntegrationStore``       — asyncpg-backed; rows in ``org_integrations`` +
                               encrypted secret in ``integration_secrets``.
``InMemoryIntegrationStore`` — dict-backed; real AES-GCM crypto, no DB.

The module-level singleton is obtained via :func:`get_integration_store`. Tests
swap in an :class:`InMemoryIntegrationStore` via
:func:`set_integration_store_for_tests`.

Secret vs non-secret split (contract Section 1)
-----------------------------------------------
The ``secret`` blob holds the sensitive fields per kind; everything else stays
in the non-secret ``config`` jsonb. ``SECRET_KEYS_BY_KIND`` is the allowlist of
keys that belong in the secret blob.

    | kind         | non-secret config            | secret                    |
    | slack        | channel, mode                | webhook_url OR bot_token  |
    | whatsapp     | phone_number_id, to          | access_token              |
    | google_chat  | space                        | webhook_url               |
    | teams        | name                         | webhook_url               |
    | email        | recipients[]                 | (none) OR smtp_*          |
    | webhook      | url_is_secret                | url                       |

Security contract
-----------------
- Secrets are encrypted with AES-256-GCM via ``app.security.crypto``; the DB
  receives only ciphertext + nonce + key_version.
- No method ever returns secret material in a listing/scrubbed shape.
- Every read/write is scoped by ``org_id`` — a row from another org is invisible.

``channels_for_org``
--------------------
The seam Agent B's dispatcher calls. Loads every ENABLED integration for the
org, merges its non-secret config + decrypted secret, and builds a live
``Channel`` via ``app.notify.channels.get_channel``. Integrations whose
secret/config is incomplete (``get_channel`` → ``NullChannel``) are skipped.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.security.crypto import decrypt_json, encrypt_json

logger = logging.getLogger(__name__)

__all__ = [
    "IntegrationStore",
    "PgIntegrationStore",
    "InMemoryIntegrationStore",
    "get_integration_store",
    "set_integration_store_for_tests",
    "channels_for_org",
    "VALID_KINDS",
    "SECRET_KEYS_BY_KIND",
    "split_secret",
    "merged_channel_config",
]

#: Integration kinds — must match the CHECK in 0011_notifications.sql and the
#: kinds understood by ``app.notify.channels.get_channel``.
VALID_KINDS: frozenset[str] = frozenset(
    {"slack", "whatsapp", "google_chat", "teams", "email", "webhook"}
)

#: The keys that belong in the encrypted secret blob, per kind. Anything not in
#: this set stays in the non-secret ``config`` jsonb.
SECRET_KEYS_BY_KIND: dict[str, frozenset[str]] = {
    "slack": frozenset({"webhook_url", "bot_token"}),
    "whatsapp": frozenset({"access_token"}),
    "google_chat": frozenset({"webhook_url"}),
    "teams": frozenset({"webhook_url"}),
    "email": frozenset({"smtp_password", "smtp_user", "smtp_host", "smtp_port"}),
    "webhook": frozenset({"url"}),
}


def secret_keys_for(kind: str) -> frozenset[str]:
    """Return the secret-key allowlist for *kind* (empty for unknown kinds)."""
    return SECRET_KEYS_BY_KIND.get((kind or "").lower().strip(), frozenset())


def split_secret(kind: str, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split *data* into ``(non_secret_config, secret)`` for *kind*.

    Keys named in :data:`SECRET_KEYS_BY_KIND` for *kind* go into the secret
    blob; everything else stays in the non-secret config. ``None`` values are
    dropped from both sides.
    """
    secret_keys = secret_keys_for(kind)
    config: dict[str, Any] = {}
    secret: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if value is None:
            continue
        if key in secret_keys:
            secret[key] = value
        else:
            config[key] = value
    return config, secret


def merged_channel_config(kind: str, config: dict[str, Any], secret: dict[str, Any]) -> dict[str, Any]:
    """Merge non-secret *config* + decrypted *secret* into a ``get_channel`` config.

    Maps the stored field names onto the keyword names ``get_channel`` /
    each ``Channel`` constructor expects (e.g. whatsapp ``access_token`` →
    ``token``, ``to`` → ``recipient``; webhook ``url`` → ``webhook_url``).
    """
    kind = (kind or "").lower().strip()
    cfg = {**(config or {}), **(secret or {})}

    if kind == "whatsapp":
        merged: dict[str, Any] = {
            "token": cfg.get("access_token") or cfg.get("token") or "",
            "phone_number_id": cfg.get("phone_number_id") or "",
            "recipient": cfg.get("to") or cfg.get("recipient") or "",
        }
        return merged

    if kind == "email":
        merged = {
            "recipient": _first_recipient(cfg.get("recipients") or cfg.get("recipient")),
        }
        # Pass through optional smtp_* if present (EmailChannel uses app SMTP today).
        for key in ("smtp_host", "smtp_port", "smtp_user", "smtp_password"):
            if cfg.get(key) is not None:
                merged[key] = cfg[key]
        return merged

    if kind == "webhook":
        # The generic webhook kind delivers via a Google-Chat-style {"text": ...}
        # POST, so reuse GoogleChatChannel by mapping url -> webhook_url.
        return {"webhook_url": cfg.get("url") or cfg.get("webhook_url") or ""}

    # slack / google_chat / teams already use webhook_url / bot_token / channel.
    return dict(cfg)


def _first_recipient(value: Any) -> str:
    """Return the first email recipient from a string or list, else ""."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and value:
        return str(value[0])
    return ""


def _channel_kind_for(kind: str) -> str:
    """Map a stored integration kind to the ``get_channel`` kind it builds."""
    # The generic webhook kind is delivered through the Google-Chat webhook path.
    return "google_chat" if kind == "webhook" else kind


def public_row(row: dict[str, Any], *, configured: bool) -> dict[str, Any]:
    """Return the listing-safe shape of an integration row (never secrets)."""

    def _iso(value: Any) -> Any:
        return value.isoformat() if hasattr(value, "isoformat") else value

    config = row.get("config")
    return {
        "id": str(row["id"]),
        "org_id": str(row["org_id"]),
        "kind": row.get("kind"),
        "name": row.get("name"),
        "config": dict(config) if isinstance(config, dict) else {},
        "enabled": bool(row.get("enabled", True)),
        "configured": configured,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class IntegrationStore:
    """Interface for per-org integration persistence (structural duck-typing)."""

    async def create(
        self,
        *,
        org_id: str,
        created_by: str,
        kind: str,
        name: str,
        config: dict[str, Any],
        secret: dict[str, Any],
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create an integration row + encrypted secret. Return the (non-secret) row."""
        raise NotImplementedError

    async def list_for_org(self, org_id: str, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        """Return the org's integration rows (no secret material)."""
        raise NotImplementedError

    async def get(self, integration_id: str, org_id: str) -> dict[str, Any] | None:
        """Return one integration row scoped to *org_id*, or ``None``."""
        raise NotImplementedError

    async def update(
        self,
        integration_id: str,
        org_id: str,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        secret: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update non-secret fields and/or rotate the secret. Return the row or ``None``."""
        raise NotImplementedError

    async def delete(self, integration_id: str, org_id: str) -> bool:
        """Delete the integration + secret. Return ``True`` if a row was removed."""
        raise NotImplementedError

    async def get_secret(self, integration_id: str, org_id: str) -> dict[str, Any] | None:
        """Return the decrypted secret for *integration_id* scoped to *org_id*, or ``None``."""
        raise NotImplementedError

    async def has_secret(self, integration_id: str, org_id: str) -> bool:
        """Return ``True`` if a (non-empty) secret exists for this integration."""
        secret = await self.get_secret(integration_id, org_id)
        return bool(secret)


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgIntegrationStore(IntegrationStore):
    """asyncpg-backed store over ``org_integrations`` + ``integration_secrets``."""

    async def create(
        self,
        *,
        org_id: str,
        created_by: str,
        kind: str,
        name: str,
        config: dict[str, Any],
        secret: dict[str, Any],
        enabled: bool = True,
    ) -> dict[str, Any]:
        from app.db import fetchrow  # local import to avoid circular load
        import json

        integration_id = str(uuid.uuid4())
        row = await fetchrow(
            """
            INSERT INTO org_integrations (id, org_id, created_by, kind, name, config, enabled)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb, $7)
            RETURNING id, org_id, created_by, kind, name, config, enabled,
                      created_at, updated_at
            """,
            integration_id,
            org_id,
            created_by,
            kind,
            name,
            json.dumps(config or {}),
            enabled,
        )
        if secret:
            await self._put_secret(integration_id, org_id, secret)
        return _coerce_row(row)

    async def list_for_org(self, org_id: str, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        from app.db import fetch  # local import

        clause = "AND enabled = true" if enabled_only else ""
        rows = await fetch(
            f"""
            SELECT id, org_id, created_by, kind, name, config, enabled,
                   created_at, updated_at
            FROM org_integrations
            WHERE org_id = $1::uuid {clause}
            ORDER BY created_at DESC
            """,
            org_id,
        )
        return [_coerce_row(r) for r in rows]

    async def get(self, integration_id: str, org_id: str) -> dict[str, Any] | None:
        from app.db import fetchrow  # local import

        row = await fetchrow(
            """
            SELECT id, org_id, created_by, kind, name, config, enabled,
                   created_at, updated_at
            FROM org_integrations
            WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            integration_id,
            org_id,
        )
        return _coerce_row(row) if row is not None else None

    async def update(
        self,
        integration_id: str,
        org_id: str,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        secret: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        from app.db import fetchrow  # local import
        import json

        existing = await self.get(integration_id, org_id)
        if existing is None:
            return None

        new_name = name if name is not None else existing["name"]
        new_enabled = enabled if enabled is not None else existing["enabled"]
        new_config = existing.get("config") or {}
        if config is not None:
            new_config = {**new_config, **config}

        row = await fetchrow(
            """
            UPDATE org_integrations
            SET name = $3, config = $4::jsonb, enabled = $5, updated_at = now()
            WHERE id = $1::uuid AND org_id = $2::uuid
            RETURNING id, org_id, created_by, kind, name, config, enabled,
                      created_at, updated_at
            """,
            integration_id,
            org_id,
            new_name,
            json.dumps(new_config),
            new_enabled,
        )
        if row is None:
            return None
        if secret is not None and secret:
            await self._put_secret(integration_id, org_id, secret)
        return _coerce_row(row)

    async def delete(self, integration_id: str, org_id: str) -> bool:
        from app.db import execute  # local import

        status = await execute(
            """
            DELETE FROM org_integrations
            WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            integration_id,
            org_id,
        )
        # integration_secrets cascades on FK delete.
        try:
            return int(status.split()[-1]) > 0
        except (IndexError, ValueError, AttributeError):
            return False

    async def get_secret(self, integration_id: str, org_id: str) -> dict[str, Any] | None:
        from app.db import fetchrow  # local import

        row = await fetchrow(
            """
            SELECT ciphertext, nonce, key_version
            FROM integration_secrets
            WHERE integration_id = $1::uuid AND org_id = $2::uuid
            """,
            integration_id,
            org_id,
        )
        if row is None:
            return None
        return decrypt_json(
            bytes(row["ciphertext"]),
            bytes(row["nonce"]),
            int(row["key_version"]),
        )

    async def _put_secret(self, integration_id: str, org_id: str, secret: dict[str, Any]) -> None:
        from app.db import execute  # local import

        ciphertext, nonce, key_version = encrypt_json(secret)
        await execute(
            """
            INSERT INTO integration_secrets
                (integration_id, org_id, ciphertext, nonce, key_version)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5)
            ON CONFLICT (integration_id) DO UPDATE
                SET ciphertext  = EXCLUDED.ciphertext,
                    nonce       = EXCLUDED.nonce,
                    key_version = EXCLUDED.key_version,
                    org_id      = EXCLUDED.org_id,
                    updated_at  = now()
            """,
            integration_id,
            org_id,
            ciphertext,
            nonce,
            key_version,
        )


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryIntegrationStore(IntegrationStore):
    """Dict-backed store for tests. Encrypts secrets with real AES-GCM."""

    def __init__(self) -> None:
        # integration_id -> non-secret row dict
        self._rows: dict[str, dict[str, Any]] = {}
        # integration_id -> {"ciphertext", "nonce", "key_version", "org_id"}
        self._secrets: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self._rows.clear()
        self._secrets.clear()

    async def create(
        self,
        *,
        org_id: str,
        created_by: str,
        kind: str,
        name: str,
        config: dict[str, Any],
        secret: dict[str, Any],
        enabled: bool = True,
    ) -> dict[str, Any]:
        integration_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)
        row = {
            "id": integration_id,
            "org_id": str(org_id),
            "created_by": str(created_by),
            "kind": kind,
            "name": name,
            "config": dict(config or {}),
            "enabled": bool(enabled),
            "created_at": now,
            "updated_at": now,
        }
        self._rows[integration_id] = row
        if secret:
            self._put_secret(integration_id, org_id, secret)
        return _coerce_row(row)

    async def list_for_org(self, org_id: str, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        rows = [
            r
            for r in self._rows.values()
            if str(r["org_id"]) == str(org_id) and (not enabled_only or r["enabled"])
        ]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [_coerce_row(r) for r in rows]

    async def get(self, integration_id: str, org_id: str) -> dict[str, Any] | None:
        row = self._rows.get(str(integration_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return None
        return _coerce_row(row)

    async def update(
        self,
        integration_id: str,
        org_id: str,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        secret: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        row = self._rows.get(str(integration_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return None
        if name is not None:
            row["name"] = name
        if config is not None:
            row["config"] = {**(row.get("config") or {}), **config}
        if enabled is not None:
            row["enabled"] = bool(enabled)
        row["updated_at"] = datetime.now(tz=timezone.utc)
        if secret is not None and secret:
            self._put_secret(integration_id, org_id, secret)
        return _coerce_row(row)

    async def delete(self, integration_id: str, org_id: str) -> bool:
        row = self._rows.get(str(integration_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return False
        del self._rows[str(integration_id)]
        self._secrets.pop(str(integration_id), None)
        return True

    async def get_secret(self, integration_id: str, org_id: str) -> dict[str, Any] | None:
        blob = self._secrets.get(str(integration_id))
        if blob is None or blob["org_id"] != str(org_id):
            return None
        return decrypt_json(blob["ciphertext"], blob["nonce"], blob["key_version"])

    def _put_secret(self, integration_id: str, org_id: str, secret: dict[str, Any]) -> None:
        ciphertext, nonce, key_version = encrypt_json(secret)
        self._secrets[str(integration_id)] = {
            "ciphertext": ciphertext,
            "nonce": nonce,
            "key_version": key_version,
            "org_id": str(org_id),
        }


def _coerce_row(row: Any) -> dict[str, Any]:
    """Normalise a DB/in-memory row into a plain dict (config as a dict)."""
    import json

    data = dict(row)
    config = data.get("config")
    if isinstance(config, str):
        try:
            data["config"] = json.loads(config)
        except (TypeError, ValueError):
            data["config"] = {}
    elif config is None:
        data["config"] = {}
    return data


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_store: Optional[IntegrationStore] = None


def set_integration_store_for_tests(store: IntegrationStore | None) -> None:
    """Inject a test double (or pass ``None`` to restore the default Pg store)."""
    global _store
    _store = store


def get_integration_store() -> IntegrationStore:
    """Return the active :class:`IntegrationStore` singleton (lazy Pg default)."""
    global _store
    if _store is None:
        _store = PgIntegrationStore()
    return _store


# ---------------------------------------------------------------------------
# channels_for_org — the seam Agent B's dispatcher calls
# ---------------------------------------------------------------------------


async def channels_for_org(org_id: str) -> list[Any]:
    """Build a live ``Channel`` for every ENABLED integration of *org_id*.

    For each enabled integration: merge its non-secret config + decrypted secret
    and build a channel via ``app.notify.channels.get_channel``. Integrations
    whose secret/config is incomplete (``get_channel`` → ``NullChannel``) are
    skipped, so the returned list contains only deliverable channels.

    Best-effort: a failure resolving any single integration is logged and
    skipped — it never breaks resolution of the others. Returns an empty list
    when nothing is configured (callers treat that as a no-op).

    Parameters
    ----------
    org_id:
        UUID string of the organisation whose channels to resolve.

    Returns
    -------
    list[Channel]
        Live channels (never the placeholder ``NullChannel``).
    """
    if not org_id:
        return []

    from app.notify.channels import NullChannel, get_channel  # noqa: PLC0415

    store = get_integration_store()
    try:
        rows = await store.list_for_org(str(org_id), enabled_only=True)
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort.
        logger.warning("channels_for_org(%s): list failed: %s", org_id, exc)
        return []

    channels: list[Any] = []
    for row in rows:
        kind = (row.get("kind") or "").lower().strip()
        if kind not in VALID_KINDS:
            continue
        try:
            secret = await store.get_secret(str(row["id"]), str(org_id)) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "channels_for_org(%s): secret decrypt failed for %s: %s",
                org_id,
                row.get("id"),
                exc,
            )
            continue

        merged = merged_channel_config(kind, row.get("config") or {}, secret)
        ch = get_channel(_channel_kind_for(kind), merged)
        if isinstance(ch, NullChannel):
            # Incomplete config/secret — skip it.
            logger.debug(
                "channels_for_org(%s): integration %s (%s) incomplete — skipped.",
                org_id,
                row.get("id"),
                kind,
            )
            continue
        channels.append(ch)

    return channels
