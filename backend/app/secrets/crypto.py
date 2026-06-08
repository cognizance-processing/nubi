"""Encryption helpers for Nubi secrets.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the ``cryptography`` package.
The master key is a 32-byte URL-safe base64-encoded value read from the
environment variable ``NUBI_SECRETS_KEY``.  Generate one with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Public API
----------
encrypt(plaintext: str) -> bytes
    Encrypt *plaintext* with the master key; return the ciphertext blob.

decrypt(blob: bytes) -> str
    Decrypt *blob* and return the original plaintext string.

Both functions raise ``RuntimeError`` with a clear, actionable message when
``NUBI_SECRETS_KEY`` is absent or malformed.

Design notes
------------
- The key is read from the environment lazily (inside each call) so that
  misconfiguration surfaces at call time rather than import time.
- Fernet tokens are self-contained (include an HMAC and timestamp), so no
  separate nonce column is required — unlike the AES-GCM scheme used in
  ``connector_secrets``.  This keeps the secrets table schema simpler.
- ``cryptography`` is imported lazily inside functions so the module-level
  import does not fail in environments where the package is absent (though it
  IS listed in requirements.txt and is available in all Nubi environments).
"""

from __future__ import annotations

import os


def _get_fernet() -> "Fernet":  # type: ignore[name-defined]  # noqa: F821
    """Return a Fernet instance using the master key from the environment.

    Raises
    ------
    RuntimeError
        If ``NUBI_SECRETS_KEY`` is unset or is not a valid Fernet key.
    """
    from cryptography.fernet import Fernet, InvalidToken  # noqa: PLC0415

    raw = os.environ.get("NUBI_SECRETS_KEY", "").strip()
    if not raw:
        raise RuntimeError(
            "NUBI_SECRETS_KEY is not set. "
            "Generate a key with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "then set the environment variable before starting the server."
        )
    try:
        return Fernet(raw.encode())
    except (ValueError, Exception) as exc:  # noqa: BLE001
        raise RuntimeError(
            f"NUBI_SECRETS_KEY is invalid: {exc}. "
            "Generate a valid key with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


def encrypt(plaintext: str) -> bytes:
    """Encrypt *plaintext* and return the Fernet ciphertext blob.

    Parameters
    ----------
    plaintext:
        The secret value to encrypt.

    Returns
    -------
    bytes
        The Fernet token (URL-safe base64-encoded ciphertext + HMAC).

    Raises
    ------
    RuntimeError
        If ``NUBI_SECRETS_KEY`` is unset or invalid.
    """
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8"))


def decrypt(blob: bytes) -> str:
    """Decrypt a Fernet ciphertext *blob* and return the plaintext string.

    Parameters
    ----------
    blob:
        The Fernet token returned by :func:`encrypt`.

    Returns
    -------
    str
        The original plaintext.

    Raises
    ------
    RuntimeError
        If ``NUBI_SECRETS_KEY`` is unset or invalid.
    ValueError
        If the token is corrupted, tampered with, or encrypted under a
        different key (wraps ``cryptography.fernet.InvalidToken``).
    """
    from cryptography.fernet import InvalidToken  # noqa: PLC0415

    f = _get_fernet()
    try:
        return f.decrypt(blob).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Secret decryption failed: the token is invalid, expired, or was "
            "encrypted under a different key. Check NUBI_SECRETS_KEY."
        ) from exc
