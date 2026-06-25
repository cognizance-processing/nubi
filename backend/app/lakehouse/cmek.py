"""CMEK (customer-managed encryption keys) — key-provider + envelope crypto.

Two encryption postures:

* ``client`` — AES-256-GCM app-layer envelope encryption.  The crypto layer is
  fully implemented (AES-256-GCM, random 12-byte nonce prepended, AAD binding).

  **IMPORTANT — fail-closed status**: ``client`` mode is implemented here at the
  cryptographic layer only.  It is NOT yet wired into the lake read path: the
  query engine (DuckDB) reads lake objects directly from storage and cannot
  decrypt app-layer blobs.  Writing encrypted objects that DuckDB then reads as
  raw parquet would cause silent data corruption.

  To prevent this, :class:`~app.lakehouse.dedicated.DedicatedBucketProvider`
  **rejects** ``client`` mode at provision time with
  ``ManagedLakehouseError("cmek_client_unsupported", ..., 501)`` (fail-closed).
  No encrypted bytes are ever written to the lake.

  This code is retained so that a future end-to-end decrypt path (e.g. a
  proxy-layer or a custom DuckDB extension) can reuse the crypto primitives
  without another review cycle.  Until that path exists, client mode must not be
  enabled for lake writes.

  Wire format (for reference)::

      | 12 bytes nonce | GCM ciphertext+tag (variable) |

  The GCM tag (16 bytes) is appended by ``cryptography``'s AESGCM so it is part
  of the "ciphertext" slice.  AAD (additional authenticated data) is included in
  the GCM MAC and must match on decryption — use it to bind a blob to its storage
  key path so moving a ciphertext under a different key fails the MAC check.

* ``kms`` — the bucket's default CMEK/KMS key handles encryption at the storage
  layer (GCS ``default_kms_key_name`` / S3 SSE-KMS).  ``encrypt``/``decrypt`` are
  identity operations here — the key never touches app memory.  **This is the
  supported customer-managed-key mode for the dedicated-bucket provider.**

* ``none`` — no app-layer encryption; bucket-level (AES-256 at-rest) only.
  ``encrypt``/``decrypt`` are identity.

Keep this module dependency-light and unit-testable in isolation — no cloud calls.
``cryptography`` is already a project dependency (used by the secret store).
"""

from __future__ import annotations

import base64
import os

_NONCE_BYTES = 12  # GCM standard; 96-bit nonce


class CmekProvider:
    """App-layer envelope encryption (or identity pass-through).

    Instantiate via :func:`get_cmek_provider` rather than directly — the factory
    validates the key and reads custody config for you.

    Attributes
    ----------
    mode:
        The active CMEK mode: ``"client"``, ``"kms"``, or ``"none"``.
    """

    def __init__(self, mode: str, key_bytes: bytes | None = None) -> None:
        """Create a CmekProvider.

        Parameters
        ----------
        mode:
            ``"client"`` | ``"kms"`` | ``"none"``.
        key_bytes:
            Raw 32-byte AES-256 key.  Required (and validated) for ``client``
            mode; ignored for other modes.
        """
        if mode not in {"client", "kms", "none"}:
            raise ValueError(f"Unknown CMEK mode: {mode!r}")
        if mode == "client":
            if not key_bytes or len(key_bytes) != 32:
                raise ValueError(
                    "CMEK client mode requires exactly 32 bytes of key material "
                    f"(NUBI_CMEK_KEY_MATERIAL); got {len(key_bytes or b'')} bytes."
                )
        self.mode = mode
        self._key: bytes | None = key_bytes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encrypt(self, data: bytes, aad: bytes = b"") -> bytes:
        """Encrypt *data*, returning the ciphertext blob.

        For ``client`` mode: ``nonce || AESGCM(key, nonce, data, aad)``.
        For ``kms`` / ``none`` mode: identity (return *data* unchanged).

        Parameters
        ----------
        data:
            Plaintext bytes to encrypt.
        aad:
            Additional authenticated data (not encrypted, but MAC'd).  Use the
            object's storage key path to bind the ciphertext to its location —
            relocating the blob under a different key path will fail the MAC.
        """
        if self.mode != "client":
            return data
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        nonce = os.urandom(_NONCE_BYTES)
        aesgcm = AESGCM(self._key)
        ct = aesgcm.encrypt(nonce, data, aad or None)
        return nonce + ct

    def decrypt(self, blob: bytes, aad: bytes = b"") -> bytes:
        """Decrypt a *blob* produced by :meth:`encrypt`.

        For ``client`` mode: strips the nonce, then AESGCM-decrypts.
        For ``kms`` / ``none`` mode: identity (return *blob* unchanged).

        Raises
        ------
        ValueError
            When the blob is too short (< 12 bytes for client mode).
        cryptography.exceptions.InvalidTag
            When decryption fails due to a wrong key or tampered ciphertext /
            wrong AAD.  Let this propagate — callers should treat it as a
            fatal integrity error, not a retry-able one.
        """
        if self.mode != "client":
            return blob
        if len(blob) < _NONCE_BYTES:
            raise ValueError(
                f"CMEK blob too short: {len(blob)} bytes (expected ≥ {_NONCE_BYTES})."
            )
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        nonce = blob[:_NONCE_BYTES]
        ct = blob[_NONCE_BYTES:]
        aesgcm = AESGCM(self._key)
        return aesgcm.decrypt(nonce, ct, aad or None)


def get_cmek_provider() -> CmekProvider:
    """Return a :class:`CmekProvider` configured from the custody settings.

    Reads :func:`~app.lakehouse.custody.cmek_mode` and the relevant key helper.
    Validates the key for ``client`` mode eagerly so misconfiguration surfaces at
    provider construction time, not at the first encrypt/decrypt call.

    Raises
    ------
    ValueError
        When ``client`` mode is active but ``NUBI_CMEK_KEY_MATERIAL`` is absent,
        empty, or not valid base64 / not exactly 32 bytes.
    """
    from app.lakehouse.custody import cmek_key_material, cmek_mode  # noqa: PLC0415

    mode = cmek_mode()
    key_bytes: bytes | None = None

    if mode == "client":
        raw = cmek_key_material()
        if not raw:
            raise ValueError(
                "CMEK mode is 'client' but NUBI_CMEK_KEY_MATERIAL is not set."
            )
        try:
            key_bytes = base64.b64decode(raw)
        except Exception as exc:
            raise ValueError(
                "NUBI_CMEK_KEY_MATERIAL is not valid base64."
            ) from exc
        if len(key_bytes) != 32:
            raise ValueError(
                f"NUBI_CMEK_KEY_MATERIAL must decode to exactly 32 bytes "
                f"(AES-256); got {len(key_bytes)} bytes."
            )

    return CmekProvider(mode=mode, key_bytes=key_bytes)
