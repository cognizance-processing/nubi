"""Tests for app.lakehouse.cmek — CMEK key-provider + envelope crypto.

Coverage
--------
1. Client-mode round-trip: encrypt → decrypt returns original bytes.
2. Nonce randomisation: two encryptions of the same plaintext produce different blobs.
3. Wrong key fails decryption (InvalidTag).
4. AAD mismatch fails decryption (InvalidTag).
5. get_cmek_provider() dispatches correctly from custody settings env vars.
6. Misconfigurations (absent key, wrong length, bad base64) raise ValueError.
7. kms / none modes are identity (encrypt/decrypt are pass-through).
8. Blob too short for client mode raises ValueError (not a cryptography error).

All tests are hermetic — no cloud calls, no network.
"""

from __future__ import annotations

import base64
import os

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_KEY_BYTES = os.urandom(32)
_GOOD_KEY_B64 = base64.b64encode(_GOOD_KEY_BYTES).decode()


def _provider(mode: str = "client", key_bytes: bytes | None = None):
    from app.lakehouse.cmek import CmekProvider

    if mode == "client" and key_bytes is None:
        key_bytes = _GOOD_KEY_BYTES
    return CmekProvider(mode=mode, key_bytes=key_bytes)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings(monkeypatch):
    """Ensure clean settings state around every test."""
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# CmekProvider — client mode
# ---------------------------------------------------------------------------


def test_client_mode_round_trip():
    """Encrypting then decrypting returns the original plaintext."""
    p = _provider("client")
    plaintext = b"hello dedicated-bucket world"
    blob = p.encrypt(plaintext)
    assert p.decrypt(blob) == plaintext


def test_client_mode_round_trip_with_aad():
    """AAD is included in the MAC; round-trip succeeds when AAD matches."""
    p = _provider("client")
    plaintext = b"sensitive parquet row"
    aad = b"orgs/org-1/lake/ds-1/demo/sales.parquet"
    blob = p.encrypt(plaintext, aad=aad)
    assert p.decrypt(blob, aad=aad) == plaintext


def test_client_mode_aad_mismatch_fails():
    """Different AAD on decrypt must raise (GCM authentication failure)."""
    from cryptography.exceptions import InvalidTag

    p = _provider("client")
    plaintext = b"confidential data"
    blob = p.encrypt(plaintext, aad=b"correct-aad")
    with pytest.raises(InvalidTag):
        p.decrypt(blob, aad=b"wrong-aad")


def test_client_mode_nonce_randomised():
    """Two encryptions of the same plaintext must produce distinct blobs."""
    p = _provider("client")
    plaintext = b"same message"
    blob1 = p.encrypt(plaintext)
    blob2 = p.encrypt(plaintext)
    assert blob1 != blob2, "Nonces must be random; identical blobs indicate a broken nonce."


def test_client_mode_wrong_key_fails():
    """Decrypting with a different key must fail (GCM tag mismatch)."""
    from cryptography.exceptions import InvalidTag

    p_enc = _provider("client", key_bytes=_GOOD_KEY_BYTES)
    p_dec = _provider("client", key_bytes=os.urandom(32))
    blob = p_enc.encrypt(b"secret")
    with pytest.raises(InvalidTag):
        p_dec.decrypt(blob)


def test_client_mode_tampered_ciphertext_fails():
    """Flipping a byte in the ciphertext (after the nonce) must fail."""
    from cryptography.exceptions import InvalidTag

    p = _provider("client")
    blob = p.encrypt(b"sensitive")
    # Flip the last byte of the blob (inside the GCM tag).
    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        p.decrypt(bytes(tampered))


def test_client_mode_short_blob_raises_value_error():
    """A blob shorter than the nonce (12 bytes) raises ValueError, not AttributeError."""
    p = _provider("client")
    with pytest.raises(ValueError, match="too short"):
        p.decrypt(b"short")


# ---------------------------------------------------------------------------
# CmekProvider — kms / none modes (identity pass-through)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["kms", "none"])
def test_non_client_modes_are_identity(mode: str):
    """kms and none modes must be transparent (encrypt/decrypt = identity)."""
    p = _provider(mode, key_bytes=None)
    data = b"raw parquet bytes"
    assert p.encrypt(data) == data
    assert p.decrypt(data) == data


@pytest.mark.parametrize("mode", ["kms", "none"])
def test_non_client_modes_aad_ignored(mode: str):
    """kms and none modes ignore AAD — identity in, identity out."""
    p = _provider(mode)
    data = b"raw data"
    assert p.encrypt(data, aad=b"any-aad") == data
    assert p.decrypt(data, aad=b"any-aad") == data


# ---------------------------------------------------------------------------
# CmekProvider — constructor validation
# ---------------------------------------------------------------------------


def test_client_mode_requires_32_byte_key():
    """Constructing client-mode with wrong key length raises ValueError."""
    from app.lakehouse.cmek import CmekProvider

    with pytest.raises(ValueError, match="32 bytes"):
        CmekProvider(mode="client", key_bytes=os.urandom(16))


def test_client_mode_requires_non_empty_key():
    """Constructing client-mode without a key raises ValueError."""
    from app.lakehouse.cmek import CmekProvider

    with pytest.raises(ValueError, match="32 bytes"):
        CmekProvider(mode="client", key_bytes=None)


def test_unknown_mode_raises():
    """An unrecognised mode string raises ValueError."""
    from app.lakehouse.cmek import CmekProvider

    with pytest.raises(ValueError, match="Unknown CMEK mode"):
        CmekProvider(mode="chacha20")


# ---------------------------------------------------------------------------
# get_cmek_provider() — factory + env dispatch
# ---------------------------------------------------------------------------


def test_get_cmek_provider_none_mode(monkeypatch):
    """With CMEK off, get_cmek_provider() returns a none-mode provider."""
    from app.config import get_settings
    from app.lakehouse.cmek import get_cmek_provider

    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "false")
    get_settings.cache_clear()
    p = get_cmek_provider()
    assert p.mode == "none"
    # Identity: encrypt/decrypt are pass-through.
    assert p.encrypt(b"data") == b"data"


def test_get_cmek_provider_client_mode(monkeypatch):
    """With client mode set, factory returns a CmekProvider with mode=client."""
    from app.config import get_settings
    from app.lakehouse.cmek import get_cmek_provider

    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_CMEK_MODE", "client")
    monkeypatch.setenv("NUBI_CMEK_KEY_MATERIAL", _GOOD_KEY_B64)
    get_settings.cache_clear()

    p = get_cmek_provider()
    assert p.mode == "client"
    # Functional round-trip via factory.
    plaintext = b"factory-provisioned secret"
    assert p.decrypt(p.encrypt(plaintext)) == plaintext


def test_get_cmek_provider_kms_mode(monkeypatch):
    """With kms mode, factory returns a provider with mode=kms (identity ops)."""
    from app.config import get_settings
    from app.lakehouse.cmek import get_cmek_provider

    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_CMEK_MODE", "kms")
    monkeypatch.setenv("NUBI_CMEK_KEY_URI", "projects/p/locations/l/keyRings/r/cryptoKeys/k")
    get_settings.cache_clear()

    p = get_cmek_provider()
    assert p.mode == "kms"


def test_get_cmek_provider_client_missing_key_raises(monkeypatch):
    """Client mode without key material raises ValueError."""
    from app.config import get_settings
    from app.lakehouse.cmek import get_cmek_provider

    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_CMEK_MODE", "client")
    monkeypatch.delenv("NUBI_CMEK_KEY_MATERIAL", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="not set"):
        get_cmek_provider()


def test_get_cmek_provider_bad_b64_raises(monkeypatch):
    """Non-base64 key material raises ValueError."""
    from app.config import get_settings
    from app.lakehouse.cmek import get_cmek_provider

    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_CMEK_MODE", "client")
    monkeypatch.setenv("NUBI_CMEK_KEY_MATERIAL", "not-valid-base64!!!")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="valid base64"):
        get_cmek_provider()


def test_get_cmek_provider_wrong_length_raises(monkeypatch):
    """Base64 key that decodes to != 32 bytes raises ValueError."""
    from app.config import get_settings
    from app.lakehouse.cmek import get_cmek_provider

    # 16 bytes → not 32.
    short_key = base64.b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_CMEK_MODE", "client")
    monkeypatch.setenv("NUBI_CMEK_KEY_MATERIAL", short_key)
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="32 bytes"):
        get_cmek_provider()
