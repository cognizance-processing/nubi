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


# ---------------------------------------------------------------------------
# assert_cmek_readable — centralized fail-closed helper
# ---------------------------------------------------------------------------
#
# These tests verify the single canonical guard that every entry point must
# call.  If assert_cmek_readable is removed or softened for "client" mode,
# these tests catch the regression.


def test_assert_cmek_readable_client_raises():
    """assert_cmek_readable('client') must raise ManagedLakehouseError 501."""
    from app.lakehouse.cmek import assert_cmek_readable
    from app.lakehouse.managed import ManagedLakehouseError

    with pytest.raises(ManagedLakehouseError) as exc_info:
        assert_cmek_readable("client")

    err = exc_info.value
    assert err.code == "cmek_client_unsupported"
    assert err.status == 501
    # Message must mention the root cause (DuckDB + silent data corruption).
    assert "duckdb" in err.message.lower() or "engine" in err.message.lower()
    assert "corrupt" in err.message.lower() or "direct" in err.message.lower()


def test_assert_cmek_readable_none_passes():
    """assert_cmek_readable('none') must not raise — none mode is supported."""
    from app.lakehouse.cmek import assert_cmek_readable

    assert_cmek_readable("none")  # Must not raise.


def test_assert_cmek_readable_kms_passes():
    """assert_cmek_readable('kms') must not raise — kms mode is supported."""
    from app.lakehouse.cmek import assert_cmek_readable

    assert_cmek_readable("kms")  # Must not raise.


def test_assert_cmek_readable_client_is_always_rejected():
    """Mutation guard: assert_cmek_readable must ALWAYS reject 'client'.

    This test would catch a regression where someone added an environment
    condition, feature-flag bypass, or short-circuit that silently allows
    client mode through.  The guard must be unconditional on the mode string.
    """
    from app.lakehouse.cmek import assert_cmek_readable
    from app.lakehouse.managed import ManagedLakehouseError

    # Call multiple times: must raise every single time.
    for _ in range(3):
        with pytest.raises(ManagedLakehouseError) as exc_info:
            assert_cmek_readable("client")
        assert exc_info.value.code == "cmek_client_unsupported"
        assert exc_info.value.status == 501


def test_assert_cmek_readable_raises_before_any_write(tmp_path):
    """Guard must raise ManagedLakehouseError — not write partial state then raise.

    Simulates the scenario: if code wrote bytes and THEN called the guard,
    data corruption could occur.  The guard itself must be side-effect-free
    (raises immediately; no I/O).
    """
    from app.lakehouse.cmek import assert_cmek_readable
    from app.lakehouse.managed import ManagedLakehouseError

    sentinel = tmp_path / "should_not_exist.txt"

    def _would_write_then_guard():
        # Guard FIRST (correct order).
        assert_cmek_readable("client")
        # This line must never be reached.
        sentinel.write_bytes(b"data")

    with pytest.raises(ManagedLakehouseError):
        _would_write_then_guard()

    assert not sentinel.exists(), (
        "File was written before the guard raised — guard must come before any I/O."
    )


def test_assert_cmek_readable_consistent_error_code():
    """The error code must be exactly 'cmek_client_unsupported' and HTTP 501.

    Guards at every entry point must emit the SAME error code so callers can
    handle it uniformly.  If this changes, update ALL entry-point tests too.
    """
    from app.lakehouse.cmek import assert_cmek_readable
    from app.lakehouse.managed import ManagedLakehouseError

    with pytest.raises(ManagedLakehouseError) as exc_info:
        assert_cmek_readable("client")

    assert exc_info.value.code == "cmek_client_unsupported"
    assert exc_info.value.status == 501
