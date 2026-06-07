"""AES-256-GCM application-layer encryption for connector secrets.

Design
------
- Keys are loaded LAZILY from environment variables; never stored in the DB.
- The DB receives only: ciphertext (bytes), nonce (bytes), key_version (int).
- A 12-byte random nonce is generated per encryption call (NIST SP 800-38D).
- GCM authentication tag is appended to the ciphertext by cryptography lib
  (ciphertext = cipher_bytes + 16-byte tag).
- Key registry supports rotation:
    Simple form  — CONNECTOR_SECRET_KEY (b64 32 bytes) + CONNECTOR_SECRET_KEY_VERSION (int)
    Extended form — CONNECTOR_SECRET_KEYS='{"1":"<b64>","2":"<b64>"}' overrides the above;
                    the highest numeric version key is treated as current.

Environment variables
---------------------
CONNECTOR_SECRET_KEY          Base64-encoded 32-byte AES key (current key).
CONNECTOR_SECRET_KEY_VERSION  Int version label for the key above (default 1).
CONNECTOR_SECRET_KEYS         JSON map {"version": "b64key", ...}. When present,
                              overrides the two vars above and becomes the full registry.

Test helpers
------------
    reset_keys_for_tests()    — clear the lazy key cache so tests can set env vars
                                in-process and have them picked up on next use.
"""

from __future__ import annotations

import json
import os
import base64
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NONCE_BYTES = 12          # NIST-recommended 96-bit nonce for AES-GCM
_KEY_BYTES = 32             # 256-bit AES key


# ---------------------------------------------------------------------------
# Lazy key registry
# ---------------------------------------------------------------------------

# Populated on first use; maps int version -> bytes key.
_key_registry: dict[int, bytes] | None = None
_current_version: int | None = None


def _load_keys() -> tuple[dict[int, bytes], int]:
    """Read key material from environment variables and return (registry, current_version).

    Raises
    ------
    RuntimeError
        If no valid key is configured.
    """
    extended_raw = os.environ.get("CONNECTOR_SECRET_KEYS", "").strip()

    if extended_raw:
        # Extended multi-key form: {"1": "<b64>", "2": "<b64>"}
        try:
            raw_map: dict[str, str] = json.loads(extended_raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "CONNECTOR_SECRET_KEYS must be a JSON object mapping version strings "
                f'to base64 keys, e.g. \'{{"1": "<b64>"}}\'. Parse error: {exc}'
            ) from exc

        if not raw_map:
            raise RuntimeError(
                "CONNECTOR_SECRET_KEYS is set but contains no entries. "
                "Provide at least one version -> key mapping."
            )

        registry: dict[int, bytes] = {}
        for ver_str, b64_key in raw_map.items():
            try:
                ver = int(ver_str)
            except ValueError as exc:
                raise RuntimeError(
                    f"CONNECTOR_SECRET_KEYS key {ver_str!r} is not a valid integer version."
                ) from exc
            key_bytes = _decode_key(b64_key, f"CONNECTOR_SECRET_KEYS[{ver_str}]")
            registry[ver] = key_bytes

        current_version = max(registry)
        return registry, current_version

    # Simple form: single key + version.
    simple_key_b64 = os.environ.get("CONNECTOR_SECRET_KEY", "").strip()
    if not simple_key_b64:
        raise RuntimeError(
            "No connector secret key configured. "
            "Set CONNECTOR_SECRET_KEY (base64-encoded 32 bytes) in the environment. "
            "Generate one with: python -c \"import secrets, base64; "
            "print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )

    ver_str = os.environ.get("CONNECTOR_SECRET_KEY_VERSION", "1").strip()
    try:
        current_version = int(ver_str)
    except ValueError as exc:
        raise RuntimeError(
            f"CONNECTOR_SECRET_KEY_VERSION must be an integer, got {ver_str!r}."
        ) from exc

    key_bytes = _decode_key(simple_key_b64, "CONNECTOR_SECRET_KEY")
    return {current_version: key_bytes}, current_version


def _decode_key(b64_value: str, var_name: str) -> bytes:
    """Decode a base64-encoded key, validating length."""
    try:
        raw = base64.b64decode(b64_value)
    except Exception as exc:
        raise RuntimeError(
            f"{var_name} is not valid base64: {exc}"
        ) from exc

    if len(raw) != _KEY_BYTES:
        raise RuntimeError(
            f"{var_name} must decode to exactly {_KEY_BYTES} bytes "
            f"(got {len(raw)} bytes). "
            f"Generate a valid key with: python -c \"import secrets, base64; "
            f"print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )
    return raw


def _get_registry() -> tuple[dict[int, bytes], int]:
    """Return (registry, current_version), loading lazily on first call."""
    global _key_registry, _current_version
    if _key_registry is None:
        _key_registry, _current_version = _load_keys()
    return _key_registry, _current_version  # type: ignore[return-value]


def reset_keys_for_tests() -> None:
    """Clear the lazy key cache so tests can set env vars in-process.

    Call this after modifying CONNECTOR_SECRET_KEY / CONNECTOR_SECRET_KEYS in
    os.environ and before calling encrypt/decrypt to force re-reading env.

    Example::

        import os, base64, secrets
        os.environ["CONNECTOR_SECRET_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
        os.environ["CONNECTOR_SECRET_KEY_VERSION"] = "1"
        from app.security.crypto import reset_keys_for_tests
        reset_keys_for_tests()
    """
    global _key_registry, _current_version
    _key_registry = None
    _current_version = None


# ---------------------------------------------------------------------------
# Core encrypt / decrypt
# ---------------------------------------------------------------------------

def encrypt(plaintext: bytes) -> tuple[bytes, bytes, int]:
    """Encrypt *plaintext* with the current key using AES-256-GCM.

    Parameters
    ----------
    plaintext:
        Raw bytes to encrypt.

    Returns
    -------
    (ciphertext, nonce, key_version)
        ciphertext — encrypted bytes including the 16-byte GCM authentication tag.
        nonce      — 12-byte random nonce (store alongside ciphertext).
        key_version — integer version label of the key used (store alongside ciphertext).
    """
    registry, current_version = _get_registry()
    key = registry[current_version]

    nonce = os.urandom(_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    return ciphertext, nonce, current_version


def decrypt(ciphertext: bytes, nonce: bytes, key_version: int) -> bytes:
    """Decrypt *ciphertext* using the key identified by *key_version*.

    Parameters
    ----------
    ciphertext:
        Encrypted bytes including the GCM authentication tag (as produced by encrypt()).
    nonce:
        The 12-byte nonce used during encryption.
    key_version:
        Integer version of the key to use for decryption.

    Returns
    -------
    bytes
        The original plaintext.

    Raises
    ------
    KeyError
        If *key_version* is not present in the key registry.
    cryptography.exceptions.InvalidTag
        If the GCM authentication tag check fails (tampered ciphertext or wrong key).
    RuntimeError
        If no keys are configured.
    """
    registry, _ = _get_registry()

    if key_version not in registry:
        registered = sorted(registry.keys())
        raise KeyError(
            f"Unknown key version {key_version}. "
            f"Registered versions: {registered}. "
            "If this is a rotated key, add it to CONNECTOR_SECRET_KEYS."
        )

    key = registry[key_version]
    aesgcm = AESGCM(key)
    # AESGCM.decrypt raises cryptography.exceptions.InvalidTag on auth failure.
    return aesgcm.decrypt(nonce, ciphertext, associated_data=None)


# ---------------------------------------------------------------------------
# JSON convenience wrappers
# ---------------------------------------------------------------------------

def encrypt_json(data: dict[str, Any]) -> tuple[bytes, bytes, int]:
    """JSON-encode *data* then encrypt.

    Parameters
    ----------
    data:
        A JSON-serialisable dict (connector credentials, etc.).

    Returns
    -------
    (ciphertext, nonce, key_version)
        Same as ``encrypt()``.
    """
    plaintext = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return encrypt(plaintext)


def decrypt_json(ciphertext: bytes, nonce: bytes, key_version: int) -> dict[str, Any]:
    """Decrypt and JSON-decode a blob produced by ``encrypt_json()``.

    Parameters
    ----------
    ciphertext, nonce, key_version:
        As stored in the DB (output of encrypt_json / encrypt).

    Returns
    -------
    dict
        The original Python dict.

    Raises
    ------
    Same exceptions as ``decrypt()``, plus ``json.JSONDecodeError`` if the
    decrypted bytes are not valid JSON (should never happen with well-formed data).
    """
    plaintext = decrypt(ciphertext, nonce, key_version)
    return json.loads(plaintext.decode("utf-8"))
