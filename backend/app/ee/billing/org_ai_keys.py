"""Per-org BYO (bring-your-own) AI provider API keys — EE billing.

An organisation on ANY PAID tier may store its own vendor API key
(Anthropic / OpenAI / Gemini).  When a key is present for the provider a
request would otherwise use, ``resolve_provider_for_org`` swaps it in and
tags the resulting :class:`~app.ai.provider.LiteLLMProvider` as BYO
(``provider.is_byo = True``) — the metering hook
(:mod:`app.ee.billing.token_billing`) skips charging the org's wallet for
BYO calls (their key, their vendor bill), while still recording the token
usage for visibility.

Free-tier orgs cannot set a BYO key: :func:`can_org_byo` gates both the
storage routes (``POST``/``DELETE /ai/keys``) and the resolution path.

Storage
-------
Two implementations mirroring ``app.connectors.secret_store``:

``PgOrgAiKeyStore``
    asyncpg-backed; ciphertext in the ``org_ai_keys`` table
    (migration ``database/migrations/ee/0029_org_ai_keys.sql``).  Reuses
    :func:`app.security.crypto.encrypt_json` / ``decrypt_json`` — the SAME
    AES-256-GCM mechanism (``CONNECTOR_SECRET_KEY``) as connector secrets.

``InMemoryOrgAiKeyStore``
    dict-backed; for tests (no DB required, encrypts/decrypts with the same
    real crypto so correctness is still exercised).

Provider
--------
The module-level singleton is obtained via :func:`get_org_ai_key_store`.
Tests swap in an :class:`InMemoryOrgAiKeyStore` via
:func:`set_org_ai_key_store_for_tests`.

This module is EE-only and must NEVER be imported by open-source core code.
"""

from __future__ import annotations

import logging
from typing import Any

from app.security.crypto import decrypt_json, encrypt_json

logger = logging.getLogger("nubi.billing.ai_keys")

#: Vendor ids an org may store a BYO key for — mirrors
#: ``app.ai.provider.ALLOWED_MODELS`` keys.
SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"anthropic", "openai", "gemini"})


# ---------------------------------------------------------------------------
# Store interface
# ---------------------------------------------------------------------------


class OrgAiKeyStore:
    """Interface for per-org AI key storage."""

    async def put(self, org_id: str, provider: str, api_key: str, *, created_by: str | None = None) -> None:
        """Encrypt *api_key* and upsert into the store (one key per (org, provider))."""
        raise NotImplementedError

    async def get(self, org_id: str, provider: str) -> str | None:
        """Return the decrypted API key for (org_id, provider), or None if absent."""
        raise NotImplementedError

    async def delete(self, org_id: str, provider: str) -> None:
        """Delete the key for (org_id, provider); no-op if absent."""
        raise NotImplementedError

    async def list_providers(self, org_id: str) -> list[str]:
        """Return the provider ids this org has a BYO key configured for.

        Never returns plaintext — this is a presence check for
        ``GET /ai/providers`` (``byo_configured`` per provider), not a key
        retrieval endpoint.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgOrgAiKeyStore(OrgAiKeyStore):
    """asyncpg-backed store using the ``org_ai_keys`` table."""

    async def put(self, org_id: str, provider: str, api_key: str, *, created_by: str | None = None) -> None:
        from app.db import execute  # noqa: PLC0415

        ciphertext, nonce, key_version = encrypt_json({"api_key": api_key})
        await execute(
            """
            INSERT INTO org_ai_keys
                (org_id, provider, ciphertext, nonce, key_version, created_by)
            VALUES
                ($1::uuid, $2, $3, $4, $5, $6::uuid)
            ON CONFLICT (org_id, provider) DO UPDATE
                SET ciphertext  = EXCLUDED.ciphertext,
                    nonce       = EXCLUDED.nonce,
                    key_version = EXCLUDED.key_version,
                    updated_at  = now()
            """,
            org_id,
            provider,
            ciphertext,
            nonce,
            key_version,
            created_by,
        )

    async def get(self, org_id: str, provider: str) -> str | None:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            SELECT ciphertext, nonce, key_version
            FROM org_ai_keys
            WHERE org_id = $1::uuid AND provider = $2
            """,
            org_id,
            provider,
        )
        if row is None:
            return None
        secret = decrypt_json(bytes(row["ciphertext"]), bytes(row["nonce"]), int(row["key_version"]))
        return secret.get("api_key")

    async def delete(self, org_id: str, provider: str) -> None:
        from app.db import execute  # noqa: PLC0415

        await execute(
            "DELETE FROM org_ai_keys WHERE org_id = $1::uuid AND provider = $2",
            org_id,
            provider,
        )

    async def list_providers(self, org_id: str) -> list[str]:
        from app.db import fetch  # noqa: PLC0415

        rows = await fetch(
            "SELECT provider FROM org_ai_keys WHERE org_id = $1::uuid",
            org_id,
        )
        return [str(r["provider"]) for r in rows]


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryOrgAiKeyStore(OrgAiKeyStore):
    """Dict-backed store for tests.  Real AES-GCM encrypt/decrypt (no DB)."""

    def __init__(self) -> None:
        # (org_id, provider) -> {"ciphertext", "nonce", "key_version"}
        self._store: dict[tuple[str, str], dict[str, Any]] = {}

    def reset(self) -> None:
        self._store.clear()

    async def put(self, org_id: str, provider: str, api_key: str, *, created_by: str | None = None) -> None:
        ciphertext, nonce, key_version = encrypt_json({"api_key": api_key})
        self._store[(str(org_id), provider)] = {
            "ciphertext": ciphertext,
            "nonce": nonce,
            "key_version": key_version,
        }

    async def get(self, org_id: str, provider: str) -> str | None:
        row = self._store.get((str(org_id), provider))
        if row is None:
            return None
        secret = decrypt_json(row["ciphertext"], row["nonce"], row["key_version"])
        return secret.get("api_key")

    async def delete(self, org_id: str, provider: str) -> None:
        self._store.pop((str(org_id), provider), None)

    async def list_providers(self, org_id: str) -> list[str]:
        return [p for (oid, p) in self._store if oid == str(org_id)]


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_store: OrgAiKeyStore | None = None


def set_org_ai_key_store_for_tests(store: OrgAiKeyStore | None) -> None:
    """Inject a test double (or reset to the default PgOrgAiKeyStore)."""
    global _store  # noqa: PLW0603
    _store = store


def get_org_ai_key_store() -> OrgAiKeyStore:
    """Return the active :class:`OrgAiKeyStore` singleton."""
    global _store  # noqa: PLW0603
    if _store is None:
        _store = PgOrgAiKeyStore()
    return _store


# ---------------------------------------------------------------------------
# BYO eligibility + provider resolution
# ---------------------------------------------------------------------------


async def can_org_byo(org_id: str | None) -> bool:
    """Return True when *org_id* is eligible to store/use a BYO AI key.

    Eligibility = any PAID tier (Starter and above).  FREE-tier orgs (no
    subscription, or an explicit FREE tier/override) cannot BYO.  Fails
    closed (returns False) on any resolution error — a broken tier lookup
    must never accidentally grant BYO eligibility.
    """
    if not org_id:
        return False
    try:
        from app.ee.billing.quota import resolve_org_limits  # noqa: PLC0415
        from app.ee.billing.tiers import BillingTier  # noqa: PLC0415

        tier, _limits, _billing_disabled = await resolve_org_limits(org_id)
        return tier != BillingTier.FREE
    except Exception as exc:  # noqa: BLE001 — fail closed
        logger.warning("ai_keys: could not resolve tier for org=%s — denying BYO: %s", org_id, exc)
        return False


async def set_org_ai_key(org_id: str, provider: str, api_key: str, *, created_by: str | None = None) -> None:
    """Store *api_key* for (org_id, provider) — PAID tiers only.

    Raises
    ------
    AppError("ai_key_provider_invalid", 400)
        *provider* is not one of :data:`SUPPORTED_PROVIDERS`.
    AppError("ai_key_requires_paid_tier", 402)
        *org_id* is not eligible for BYO (see :func:`can_org_byo`).
    """
    from app.errors import AppError  # noqa: PLC0415

    if provider not in SUPPORTED_PROVIDERS:
        raise AppError(
            "ai_key_provider_invalid",
            f"Unknown provider {provider!r}. Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}.",
            400,
        )
    if not await can_org_byo(org_id):
        raise AppError(
            "ai_key_requires_paid_tier",
            "Bringing your own AI provider key requires a paid plan. Upgrade to set one.",
            402,
        )
    if not api_key or not api_key.strip():
        raise AppError("validation_error", "api_key must not be empty.", 400)
    await get_org_ai_key_store().put(org_id, provider, api_key.strip(), created_by=created_by)
    logger.info("ai_keys: BYO key set for org=%s provider=%s", org_id, provider)


async def clear_org_ai_key(org_id: str, provider: str) -> None:
    """Delete the BYO key for (org_id, provider); no-op if absent."""
    from app.errors import AppError  # noqa: PLC0415

    if provider not in SUPPORTED_PROVIDERS:
        raise AppError(
            "ai_key_provider_invalid",
            f"Unknown provider {provider!r}. Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}.",
            400,
        )
    await get_org_ai_key_store().delete(org_id, provider)
    logger.info("ai_keys: BYO key cleared for org=%s provider=%s", org_id, provider)


async def org_byo_status(org_id: str | None) -> dict[str, Any]:
    """Return ``{can_byo, providers: {<id>: bool}}`` for *org_id*.

    Consumed by ``GET /ai/providers`` so the frontend can render BYO
    affordances without a separate round-trip.  Never raises.
    """
    can_byo = await can_org_byo(org_id)
    providers: dict[str, bool] = dict.fromkeys(sorted(SUPPORTED_PROVIDERS), False)
    if org_id and can_byo:
        try:
            configured = set(await get_org_ai_key_store().list_providers(org_id))
            for p in providers:
                providers[p] = p in configured
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("ai_keys: could not list BYO providers for org=%s: %s", org_id, exc)
    return {"can_byo": can_byo, "providers": providers}


async def resolve_provider_for_org(org_id: str | None) -> tuple[Any, bool]:
    """Return ``(provider, is_byo)`` — the operator default, or an org's BYO key.

    Resolution
    ----------
    1. Resolve the operator-configured default via
       :func:`app.ai.provider.get_provider`.
    2. When that default is a :class:`~app.ai.provider.LiteLLMProvider` routed
       to a known vendor (``provider.vendor``) AND *org_id* is BYO-eligible
       (:func:`can_org_byo`) AND the org has stored a key for that SAME
       vendor, construct a NEW ``LiteLLMProvider`` using the org's key
       (same model + allowlist) and return it with ``is_byo=True``.
    3. Otherwise return the operator default unchanged, ``is_byo=False``.

    A raw operator ``LITELLM_MODEL`` string with no recognisable vendor
    (``provider.vendor is None``) is never BYO-eligible — there is no vendor
    identity to match an org key against.  Fails open to the operator
    default on any error (never blocks the call on a BYO-resolution bug).
    """
    from app.ai.provider import LiteLLMProvider, get_provider, make_litellm_for_vendor  # noqa: PLC0415

    provider = get_provider()
    if not isinstance(provider, LiteLLMProvider) or not provider.vendor:
        return provider, False
    if not org_id:
        return provider, False

    try:
        if not await can_org_byo(org_id):
            return provider, False
        key = await get_org_ai_key_store().get(org_id, provider.vendor)
        if not key:
            return provider, False
        byo_provider = make_litellm_for_vendor(provider.vendor, key, is_byo=True)
        return byo_provider, True
    except Exception as exc:  # noqa: BLE001 — fail open to the operator default
        logger.warning("ai_keys: BYO resolution failed for org=%s — using operator key: %s", org_id, exc)
        return provider, False
