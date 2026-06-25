"""Transparent AES-256-GCM encrypting wrapper for the query cache.

Overview
--------
:class:`EncryptedCache` wraps any cache backend (``ContentAddressedCache`` or
``RedisCacheBackend``) and exposes the SAME public surface — ``get`` / ``put`` /
``size`` / ``clear`` / ``stats`` / ``invalidate`` / ``invalidate_all`` — so every
call site is **unchanged**.

Encryption is applied transparently on ``put`` and reversed on ``get``.  If the
GCM authentication tag check fails (tampered ciphertext, wrong AAD, wrong key) the
entry is treated as a **cache miss** (returns ``None``) rather than raising — the
cache must never turn a decrypt error into a request-level 500.

Key derivation + AAD design
---------------------------
We use the master key as-is for AES-256-GCM encryption and bind the per-tenant
*scoped cache key* (which already encodes ``org_id`` + ``effective_datastore_id``
via :func:`scope_cache_key`) into the GCM **Additional Authenticated Data** (AAD).

Rationale:

* **AAD binding** is the simplest and most auditable approach: GCM's authentication
  tag covers BOTH the ciphertext AND the AAD, so decryption succeeds only when the
  same ``key`` string is presented at get-time — i.e. the same tenant.  An attacker
  who copies ciphertext from org-A's slot into org-B's slot gets a guaranteed
  authentication failure, not silently wrong data.  This is the property we need.

* **No per-entry key derivation (HKDF)**: a derived key per entry would add a
  HKDF round-trip per cache access with negligible additional security benefit for
  our threat model (a shared-secret symmetric key, not a public-key scheme).  AAD
  achieves the same tenant-binding property at zero overhead.  This choice is
  documented here to make the decision explicit.

Wire format
-----------
The stored blob is the raw concatenation of::

    nonce (12 bytes) || ciphertext+tag (variable)

The 12-byte random nonce (NIST SP 800-38D) is generated fresh on every ``put`` so
that two ``put`` calls with identical plaintext produce different ciphertext.
The 16-byte GCM authentication tag is appended to the ciphertext by the
``cryptography`` library automatically (AESGCM.encrypt/decrypt convention).

Threading
---------
All delegation to the inner backend is thread-safe if the inner backend is thread-
safe.  ``EncryptedCache`` itself adds no additional shared mutable state.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:  # pragma: no cover
    from app.connectors.cache import ContentAddressedCache, RedisCacheBackend

logger = logging.getLogger("nubi.connectors.cache_encryption")

# NIST SP 800-38D recommended nonce size for AES-GCM.
_NONCE_BYTES: int = 12
# AES-256 requires a 32-byte (256-bit) key.
_KEY_BYTES: int = 32


def _decode_master_key(b64_key: str) -> bytes:
    """Decode and validate the base64-encoded master key.

    Parameters
    ----------
    b64_key:
        A base64-encoded string representing a 32-byte AES-256 key (as produced
        by ``base64.b64encode(os.urandom(32)).decode()``).

    Returns
    -------
    bytes
        The raw 32-byte key material.

    Raises
    ------
    ValueError
        If the key is not valid base64 or does not decode to exactly 32 bytes.
    """
    try:
        raw = base64.b64decode(b64_key)
    except Exception as exc:
        raise ValueError(
            "NUBI_CACHE_ENCRYPTION_KEY is not valid base64. "
            "Generate a valid key with: "
            "python -c \"import secrets, base64; "
            "print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        ) from exc

    if len(raw) != _KEY_BYTES:
        raise ValueError(
            f"NUBI_CACHE_ENCRYPTION_KEY must decode to exactly {_KEY_BYTES} bytes "
            f"(got {len(raw)} bytes). "
            "Generate a valid key with: "
            "python -c \"import secrets, base64; "
            "print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )
    return raw


class EncryptedCache:
    """Transparent AES-256-GCM encrypting wrapper over a cache backend.

    Wraps any object that implements the cache backend interface (``get`` /
    ``put`` / ``size`` / ``clear`` / ``stats`` / ``invalidate`` /
    ``invalidate_all``) and adds per-entry AES-256-GCM encryption so the
    backing store never holds plaintext Arrow IPC bytes.

    Parameters
    ----------
    inner:
        The underlying cache backend to wrap.  All non-encrypting methods
        (``size``, ``clear``, ``stats``, ``invalidate``, ``invalidate_all``)
        are delegated verbatim — this wrapper only intercepts ``get`` and ``put``.
    b64_key:
        Base64-encoded 32-byte AES-256 key (as returned by
        :func:`~app.lakehouse.custody.cache_encryption_key`).

    Security properties
    -------------------
    * The scoped cache key (``key`` argument to ``put``/``get``) is passed as
      GCM **Additional Authenticated Data** (AAD).  Any attempt to replay an
      entry from one tenant's slot under another tenant's scoped key will fail
      authentication → cache miss.
    * A fresh 12-byte random nonce is generated on every ``put`` so identical
      plaintexts produce different ciphertexts.
    * Authentication failures (``InvalidTag``) are caught and treated as a
      miss rather than an exception (fail-safe: never turn a decrypt error into
      a request-level 500).
    """

    def __init__(
        self,
        inner: "ContentAddressedCache | RedisCacheBackend",
        b64_key: str,
    ) -> None:
        self._inner = inner
        self._key = _decode_master_key(b64_key)

    # ------------------------------------------------------------------
    # Encrypting methods — the core of this class
    # ------------------------------------------------------------------

    def get(self, key: str) -> bytes | None:
        """Fetch and AES-256-GCM decrypt the entry for *key*.

        Parameters
        ----------
        key:
            The org+datastore-scoped cache key (as returned by
            :func:`~app.connectors.cache_key.scope_cache_key`).  This value is
            used as GCM AAD so the ciphertext is bound to this exact key string.

        Returns
        -------
        bytes | None
            The original plaintext Arrow IPC bytes on a hit, or ``None`` on:
            - a cache miss (key absent or expired),
            - a GCM authentication failure (tampered/foreign ciphertext), or
            - any other decrypt error.

        Notes
        -----
        Authentication failures are logged at DEBUG (not WARNING) because they
        can occur legitimately — e.g. after an in-place key rotation when old
        ciphertext encrypted with the previous key is still in the store.  The
        outcome is a cache miss (cold re-execution), not data loss.
        """
        raw = self._inner.get(key)
        if raw is None:
            return None
        return self._decrypt(raw, key)

    def put(self, key: str, value: bytes, tags: list[str] | None = None) -> None:
        """AES-256-GCM encrypt *value* then store in the inner backend.

        Parameters
        ----------
        key:
            The org+datastore-scoped cache key.  Bound into the GCM AAD so
            the ciphertext can only be decrypted when the same *key* is
            presented — linking ciphertext to the tenant that wrote it.
        value:
            Plaintext Arrow IPC bytes to cache.
        tags:
            Optional invalidation tags forwarded unchanged to the inner backend.
        """
        blob = self._encrypt(value, key)
        self._inner.put(key, blob, tags=tags)

    # ------------------------------------------------------------------
    # Pass-throughs — delegate everything else unchanged
    # ------------------------------------------------------------------

    def size(self) -> int:
        """Delegate to the inner backend's size()."""
        return self._inner.size()

    def clear(self) -> None:
        """Delegate to the inner backend's clear()."""
        self._inner.clear()

    def stats(self) -> dict:
        """Delegate to the inner backend's stats()."""
        return self._inner.stats()

    def invalidate(self, tag: str) -> int:
        """Delegate tag-based invalidation to the inner backend."""
        return self._inner.invalidate(tag)

    def invalidate_all(self) -> int:
        """Delegate full-cache invalidation to the inner backend."""
        return self._inner.invalidate_all()

    # ------------------------------------------------------------------
    # Internal crypto helpers
    # ------------------------------------------------------------------

    def _encrypt(self, plaintext: bytes, aad: str) -> bytes:
        """Return ``nonce || ciphertext+tag`` for *plaintext* with AAD = *aad*.

        A fresh 12-byte nonce is generated per call so that two ``put`` calls
        with the same plaintext produce different ciphertext blobs.

        Parameters
        ----------
        plaintext:
            The raw bytes to encrypt (Arrow IPC stream).
        aad:
            Additional Authenticated Data bound into the GCM tag — here the
            org+datastore-scoped cache key.

        Returns
        -------
        bytes
            Wire-format blob: ``nonce (12 B) || ciphertext+tag``.
        """
        nonce = os.urandom(_NONCE_BYTES)
        aesgcm = AESGCM(self._key)
        # GCM tag (16 bytes) is appended to ciphertext by the cryptography lib.
        ciphertext_tag = aesgcm.encrypt(nonce, plaintext, associated_data=aad.encode("utf-8"))
        return nonce + ciphertext_tag

    def _decrypt(self, blob: bytes, aad: str) -> bytes | None:
        """Verify and decrypt a blob produced by :meth:`_encrypt`.

        Returns ``None`` on any failure (short blob, wrong AAD / key, tamper)
        rather than propagating an exception — the caller treats the result as
        a cache miss and re-executes the query.

        Parameters
        ----------
        blob:
            Wire-format blob from the inner backend: ``nonce (12 B) || ciphertext+tag``.
        aad:
            The same AAD string used during encryption (the scoped cache key).
            A mismatch causes GCM authentication to fail → None.

        Returns
        -------
        bytes | None
            Decrypted plaintext, or ``None`` on any failure.
        """
        if len(blob) <= _NONCE_BYTES:
            # Too short to contain a nonce + any ciphertext — corrupt entry.
            logger.debug(
                "cache_encryption: blob for key too short (%d bytes); treating as MISS",
                len(blob),
            )
            return None
        nonce = blob[:_NONCE_BYTES]
        ciphertext_tag = blob[_NONCE_BYTES:]
        aesgcm = AESGCM(self._key)
        try:
            return aesgcm.decrypt(nonce, ciphertext_tag, associated_data=aad.encode("utf-8"))
        except InvalidTag:
            # Authentication failure: ciphertext tampered or wrong AAD/key.
            # Treat as a miss — do NOT propagate; never turn this into a 500.
            logger.debug(
                "cache_encryption: GCM auth failure for key=%r "
                "(tampered ciphertext, wrong AAD, or key rotation); treating as MISS",
                aad,  # aad == scoped key; log it for ops debugging
            )
            return None
        except Exception as exc:  # noqa: BLE001 — any other crypto error → miss
            logger.warning(
                "cache_encryption: unexpected decrypt error for key=%r: %s; treating as MISS",
                aad,
                exc,
            )
            return None
