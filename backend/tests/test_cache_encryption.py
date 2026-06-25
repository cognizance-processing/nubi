"""Tests for query-cache encryption at rest (app.connectors.cache_encryption).

Coverage
--------
- Round-trip: with ``NUBI_CACHE_ENCRYPTION_KEY`` set + ``NUBI_CUSTODY_ENABLED=true``,
  ``put`` then ``get`` returns the original bytes; the raw blob stored in the inner
  backend is NOT the plaintext (ciphertext ≠ plaintext AND inner backend holds encrypted bytes).

- Per-tenant AAD binding: a value stored under tenant-A's scoped key cannot be
  retrieved by substituting tenant-B's key (wrong AAD → InvalidTag → None).

- Tamper resistance: flipping a byte in the stored ciphertext → ``get`` returns None
  (not an exception).

- Disabled path: when no key is set, ``get_cache()`` returns the plain backend and
  values are stored as-is (no wrapping).

- ``get_cache()`` wrapping selection honours the custody flag correctly.

All tests are hermetic — in-process ``ContentAddressedCache`` only; no Redis required.
"""

from __future__ import annotations

import base64
import os
import secrets

import pytest

from app.connectors.cache import ContentAddressedCache, reset_cache_for_tests
from app.connectors.cache_encryption import EncryptedCache, _decode_master_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_key() -> str:
    """Generate a fresh random base64-encoded 32-byte key."""
    return base64.b64encode(secrets.token_bytes(32)).decode()


# ---------------------------------------------------------------------------
# _decode_master_key validation
# ---------------------------------------------------------------------------


def test_decode_master_key_valid():
    """A well-formed base64 32-byte key decodes to 32 raw bytes."""
    b64 = _make_key()
    raw = _decode_master_key(b64)
    assert isinstance(raw, bytes)
    assert len(raw) == 32


def test_decode_master_key_wrong_length():
    """A key that decodes to the wrong number of bytes raises ValueError."""
    b64_short = base64.b64encode(secrets.token_bytes(16)).decode()
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        _decode_master_key(b64_short)


def test_decode_master_key_invalid_base64():
    """Garbage that is not valid base64 raises ValueError."""
    with pytest.raises(ValueError, match="not valid base64"):
        _decode_master_key("not-base64!!!")


# ---------------------------------------------------------------------------
# Round-trip: put then get returns original bytes
# ---------------------------------------------------------------------------


def test_round_trip_plaintext_returned():
    """put + get round-trip decrypts to the original bytes."""
    inner = ContentAddressedCache()
    enc = EncryptedCache(inner, _make_key())

    plaintext = b"Arrow IPC bytes \x00\x01\x02" * 100
    scoped_key = "abcdef1234567890" * 4  # 64-char hex (realistic scoped key)

    enc.put(scoped_key, plaintext)
    result = enc.get(scoped_key)
    assert result == plaintext


def test_raw_stored_bytes_are_ciphertext():
    """The inner backend stores ciphertext, NOT the plaintext."""
    inner = ContentAddressedCache()
    enc = EncryptedCache(inner, _make_key())

    plaintext = b"secret Arrow data"
    scoped_key = "key_for_org_a"

    enc.put(scoped_key, plaintext)

    # Read directly from the inner store — bypassing decryption.
    raw_stored = inner.get(scoped_key)
    assert raw_stored is not None
    assert raw_stored != plaintext
    # Wire format: nonce (12 bytes) || ciphertext+tag (≥16 bytes).
    assert len(raw_stored) > 12 + 16
    # The nonce prefix is the first 12 bytes; the plaintext must not appear verbatim.
    assert plaintext not in raw_stored


def test_different_puts_produce_different_ciphertext():
    """Two puts of the same plaintext produce different ciphertext (fresh nonce each time)."""
    inner1 = ContentAddressedCache()
    inner2 = ContentAddressedCache()
    key = _make_key()
    enc1 = EncryptedCache(inner1, key)
    enc2 = EncryptedCache(inner2, key)

    plaintext = b"same payload"
    scoped_key = "some_scoped_key"

    enc1.put(scoped_key, plaintext)
    enc2.put(scoped_key, plaintext)

    raw1 = inner1.get(scoped_key)
    raw2 = inner2.get(scoped_key)
    assert raw1 != raw2  # different nonces → different ciphertext


# ---------------------------------------------------------------------------
# Per-tenant AAD binding: cross-tenant replay must fail
# ---------------------------------------------------------------------------


def test_wrong_aad_returns_none():
    """Ciphertext written under tenant-A's scoped key is a MISS under tenant-B's key.

    This simulates a cross-tenant replay attack: an adversary who can copy bytes
    from org-A's cache slot and present them as org-B's slot gets a GCM auth
    failure → None (cache miss), never org-A's plaintext served to org-B.
    """
    inner = ContentAddressedCache()
    enc = EncryptedCache(inner, _make_key())

    key_a = "scoped_key_org_a_ds_1"
    key_b = "scoped_key_org_b_ds_2"
    plaintext = b"org-A's confidential results"

    enc.put(key_a, plaintext)

    # Manually move the ciphertext blob into org-B's slot (adversary controls storage).
    ciphertext_blob = inner.get(key_a)
    assert ciphertext_blob is not None
    inner.put(key_b, ciphertext_blob)  # inject into slot B

    # Reading via EncryptedCache with key_b MUST fail — AAD mismatch.
    result = enc.get(key_b)
    assert result is None, (
        "Cross-tenant replay must return None; got plaintext — AAD binding broken!"
    )

    # Original slot still works.
    assert enc.get(key_a) == plaintext


def test_direct_aad_mismatch_returns_none():
    """Directly verify that decrypting with wrong AAD returns None, not an exception."""
    inner = ContentAddressedCache()
    enc = EncryptedCache(inner, _make_key())

    plaintext = b"data for tenant X"
    enc.put("real_key", plaintext)

    blob = inner.get("real_key")
    assert blob is not None

    # Call _decrypt with the wrong AAD (simulates wrong tenant scoped key).
    result = enc._decrypt(blob, "wrong_key")  # noqa: SLF001 — testing internals
    assert result is None


# ---------------------------------------------------------------------------
# Tamper resistance
# ---------------------------------------------------------------------------


def test_tampered_ciphertext_returns_none():
    """Flipping a byte in the stored ciphertext causes get() to return None."""
    inner = ContentAddressedCache()
    enc = EncryptedCache(inner, _make_key())

    plaintext = b"important data"
    scoped_key = "tamper_test_key"

    enc.put(scoped_key, plaintext)

    # Retrieve the ciphertext blob and flip one byte in the ciphertext portion.
    blob = inner.get(scoped_key)
    assert blob is not None
    blob_list = bytearray(blob)
    # Flip the last byte (well within the ciphertext+tag region, past the 12-byte nonce).
    blob_list[-1] ^= 0xFF
    inner.put(scoped_key, bytes(blob_list))  # overwrite with tampered blob

    # get() must return None (not raise) — fail-safe on tamper.
    result = enc.get(scoped_key)
    assert result is None


def test_short_blob_returns_none():
    """A blob shorter than the nonce (12 bytes) returns None, not an exception."""
    inner = ContentAddressedCache()
    enc = EncryptedCache(inner, _make_key())

    # Inject a corrupt/truncated blob directly into the inner store.
    inner.put("short_key", b"tooshort")
    result = enc.get("short_key")
    assert result is None


# ---------------------------------------------------------------------------
# Disabled path: no key → plain backend, values stored as-is
# ---------------------------------------------------------------------------


def test_no_encryption_key_stores_plaintext(monkeypatch):
    """When no encryption key is set, get_cache() returns the plain backend."""
    # Ensure custody is NOT enabled (or key is empty) so no wrapping occurs.
    monkeypatch.delenv("NUBI_CACHE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("NUBI_CUSTODY_ENABLED", raising=False)
    monkeypatch.setattr("app.config.get_settings.cache_clear", lambda: None, raising=False)

    from app.config import get_settings
    get_settings.cache_clear()
    reset_cache_for_tests()

    from app.connectors.cache import get_cache
    cache = get_cache()

    # Should be a plain ContentAddressedCache, NOT an EncryptedCache.
    assert not isinstance(cache, EncryptedCache)

    # Values must be stored verbatim.
    payload = b"raw plaintext data"
    cache.put("plain_key", payload)
    assert cache.get("plain_key") == payload

    # Cleanup singleton for other tests.
    reset_cache_for_tests()
    get_settings.cache_clear()


def test_encryption_enabled_wraps_backend(monkeypatch):
    """When NUBI_CACHE_ENCRYPTION_KEY + NUBI_CUSTODY_ENABLED are set, get_cache() wraps."""
    enc_key = _make_key()
    monkeypatch.setenv("NUBI_CACHE_ENCRYPTION_KEY", enc_key)
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")

    from app.config import get_settings
    get_settings.cache_clear()
    reset_cache_for_tests()

    from app.connectors.cache import get_cache
    cache = get_cache()

    assert isinstance(cache, EncryptedCache), (
        "get_cache() should return an EncryptedCache when NUBI_CACHE_ENCRYPTION_KEY is set"
    )

    # Round-trip should still work.
    payload = b"encrypted at rest"
    cache.put("enc_key_test", payload)
    assert cache.get("enc_key_test") == payload

    # Cleanup.
    reset_cache_for_tests()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Pass-through methods delegate correctly
# ---------------------------------------------------------------------------


def test_pass_through_methods_delegate():
    """size, clear, stats, invalidate, invalidate_all delegate to the inner backend."""
    inner = ContentAddressedCache()
    enc = EncryptedCache(inner, _make_key())

    enc.put("k1", b"val1", tags=["org:1"])
    enc.put("k2", b"val2", tags=["org:1"])
    enc.put("k3", b"val3", tags=["org:2"])

    assert enc.size() == 3

    evicted = enc.invalidate("org:1")
    assert evicted == 2
    assert enc.size() == 1

    # Remaining entry is still readable.
    assert enc.get("k3") == b"val3"

    stats = enc.stats()
    assert "entries" in stats

    enc.clear()
    assert enc.size() == 0
    assert enc.get("k3") is None


def test_invalidate_all_delegates():
    """invalidate_all clears the inner store via the wrapper."""
    inner = ContentAddressedCache()
    enc = EncryptedCache(inner, _make_key())

    enc.put("a", b"aaa")
    enc.put("b", b"bbb")
    n = enc.invalidate_all()
    assert n == 2
    assert enc.size() == 0
