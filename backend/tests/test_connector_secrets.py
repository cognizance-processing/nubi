"""Tests for application-layer connector secret encryption + secret store.

All tests are hermetic: they use an in-process test key injected via os.environ
and InMemorySecretStore (no live DB, no DB mocking needed).  The conftest
autouse fixture resets the settings cache between tests; we additionally call
reset_keys_for_tests() ourselves since the crypto module has its own lazy cache.

Coverage
--------
- encrypt / decrypt roundtrip (bytes)
- encrypt_json / decrypt_json roundtrip (dict)
- Wrong key version raises KeyError with a clear message
- Tampered ciphertext raises cryptography.exceptions.InvalidTag (NOT silent)
- Key rotation: encrypt with v2 key while v1 key still decrypts v1 blobs
- InMemorySecretStore.put / get / delete lifecycle
- Org-scoping: org B cannot read org A's secret (returns None, not error)
- No key configured → RuntimeError with a clear message
- InMemorySecretStore.get returns None for unknown datastore_id
"""

from __future__ import annotations

import base64
import os
import secrets

import pytest
import pytest_asyncio

from cryptography.exceptions import InvalidTag

# ---------------------------------------------------------------------------
# Test key helpers
# ---------------------------------------------------------------------------

def _random_b64_key() -> str:
    """Generate a fresh base64-encoded 32-byte AES key."""
    return base64.b64encode(secrets.token_bytes(32)).decode()


def _set_single_key(b64_key: str, version: int = 1) -> None:
    """Configure simple-form env vars and reset the crypto cache."""
    from app.security.crypto import reset_keys_for_tests
    os.environ["CONNECTOR_SECRET_KEY"] = b64_key
    os.environ["CONNECTOR_SECRET_KEY_VERSION"] = str(version)
    os.environ.pop("CONNECTOR_SECRET_KEYS", None)
    reset_keys_for_tests()


def _set_multi_key(key_map: dict[int, str]) -> None:
    """Configure extended-form env var and reset the crypto cache."""
    import json
    from app.security.crypto import reset_keys_for_tests
    os.environ["CONNECTOR_SECRET_KEYS"] = json.dumps(
        {str(v): k for v, k in key_map.items()}
    )
    os.environ.pop("CONNECTOR_SECRET_KEY", None)
    os.environ.pop("CONNECTOR_SECRET_KEY_VERSION", None)
    reset_keys_for_tests()


def _clear_keys() -> None:
    """Remove all key env vars and reset the crypto cache."""
    from app.security.crypto import reset_keys_for_tests
    os.environ.pop("CONNECTOR_SECRET_KEY", None)
    os.environ.pop("CONNECTOR_SECRET_KEY_VERSION", None)
    os.environ.pop("CONNECTOR_SECRET_KEYS", None)
    reset_keys_for_tests()


# ---------------------------------------------------------------------------
# Autouse: clean up key state before/after each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_crypto_state():
    """Ensure each test starts and ends with a clean crypto cache + env."""
    _clear_keys()
    yield
    _clear_keys()


# ---------------------------------------------------------------------------
# 1. encrypt / decrypt roundtrip — bytes
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip_bytes():
    """encrypt() then decrypt() recovers the original bytes."""
    from app.security.crypto import encrypt, decrypt

    _set_single_key(_random_b64_key(), version=1)

    plaintext = b"hello, connector secrets!"
    ciphertext, nonce, key_version = encrypt(plaintext)

    # Sanity: ciphertext is not the plaintext
    assert ciphertext != plaintext
    # nonce is 12 bytes
    assert len(nonce) == 12
    # version matches configured
    assert key_version == 1

    recovered = decrypt(ciphertext, nonce, key_version)
    assert recovered == plaintext


def test_encrypt_produces_different_ciphertext_each_call():
    """Two encrypt() calls on the same plaintext produce distinct ciphertexts (random nonces)."""
    from app.security.crypto import encrypt

    _set_single_key(_random_b64_key())

    plaintext = b"same plaintext"
    ct1, nonce1, _ = encrypt(plaintext)
    ct2, nonce2, _ = encrypt(plaintext)

    assert nonce1 != nonce2
    assert ct1 != ct2


# ---------------------------------------------------------------------------
# 2. encrypt_json / decrypt_json roundtrip — dict
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_json_roundtrip():
    """encrypt_json() then decrypt_json() recovers the original dict."""
    from app.security.crypto import encrypt_json, decrypt_json

    _set_single_key(_random_b64_key())

    secret = {
        "host": "db.example.com",
        "port": 5432,
        "password": "super-secret-pw",
        "extra": {"nested": True},
    }
    ciphertext, nonce, key_version = encrypt_json(secret)
    recovered = decrypt_json(ciphertext, nonce, key_version)
    assert recovered == secret


# ---------------------------------------------------------------------------
# 3. Wrong key version → clear KeyError
# ---------------------------------------------------------------------------

def test_decrypt_unknown_version_raises():
    """Requesting decryption with an unknown key version raises KeyError."""
    from app.security.crypto import encrypt, decrypt

    _set_single_key(_random_b64_key(), version=1)
    plaintext = b"some data"
    ciphertext, nonce, _ = encrypt(plaintext)

    with pytest.raises(KeyError, match="Unknown key version"):
        decrypt(ciphertext, nonce, key_version=99)


# ---------------------------------------------------------------------------
# 4. Tampered ciphertext → InvalidTag (not silent)
# ---------------------------------------------------------------------------

def test_tampered_ciphertext_raises_invalid_tag():
    """Flipping a bit in the ciphertext causes AES-GCM auth tag failure."""
    from app.security.crypto import encrypt, decrypt

    _set_single_key(_random_b64_key(), version=1)

    plaintext = b"sensitive connector password"
    ciphertext, nonce, key_version = encrypt(plaintext)

    # Flip the first byte of the ciphertext to simulate tampering.
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]

    with pytest.raises(InvalidTag):
        decrypt(tampered, nonce, key_version)


def test_tampered_nonce_raises_invalid_tag():
    """Flipping a byte in the nonce also causes auth tag failure."""
    from app.security.crypto import encrypt, decrypt

    _set_single_key(_random_b64_key(), version=1)

    plaintext = b"api-token-value"
    ciphertext, nonce, key_version = encrypt(plaintext)

    tampered_nonce = bytes([nonce[0] ^ 0x01]) + nonce[1:]

    with pytest.raises(InvalidTag):
        decrypt(ciphertext, tampered_nonce, key_version)


# ---------------------------------------------------------------------------
# 5. Key rotation: v2 encrypts; v1 still decrypts v1 blobs
# ---------------------------------------------------------------------------

def test_key_rotation_old_version_still_decryptable():
    """After adding key v2 as current, old v1 blobs are still decryptable."""
    from app.security.crypto import encrypt, decrypt, encrypt_json, decrypt_json

    key_v1 = _random_b64_key()
    key_v2 = _random_b64_key()

    # Encrypt a blob with v1 (single-key mode).
    _set_single_key(key_v1, version=1)
    plaintext_v1 = b"old secret encrypted with v1"
    ct_v1, nonce_v1, ver_v1 = encrypt(plaintext_v1)
    assert ver_v1 == 1

    # Switch to multi-key mode with v2 as current, v1 still in registry.
    _set_multi_key({1: key_v1, 2: key_v2})
    from app.security.crypto import encrypt as enc2, decrypt as dec2
    enc2, dec2  # already imported above — reload via same names is fine

    # New encryptions use v2.
    plaintext_v2 = b"new secret encrypted with v2"
    ct_v2, nonce_v2, ver_v2 = encrypt(plaintext_v2)
    assert ver_v2 == 2

    # Old v1 blob is still decryptable.
    recovered_v1 = decrypt(ct_v1, nonce_v1, ver_v1)
    assert recovered_v1 == plaintext_v1

    # New v2 blob decrypts correctly.
    recovered_v2 = decrypt(ct_v2, nonce_v2, ver_v2)
    assert recovered_v2 == plaintext_v2


def test_key_rotation_json():
    """JSON wrappers work correctly across key rotation."""
    from app.security.crypto import encrypt_json, decrypt_json

    key_v1 = _random_b64_key()
    key_v2 = _random_b64_key()

    _set_single_key(key_v1, version=1)
    old_secret = {"db_password": "old-password", "version": 1}
    ct_old, nonce_old, ver_old = encrypt_json(old_secret)

    _set_multi_key({1: key_v1, 2: key_v2})
    new_secret = {"db_password": "new-password", "version": 2}
    ct_new, nonce_new, ver_new = encrypt_json(new_secret)
    assert ver_new == 2

    assert decrypt_json(ct_old, nonce_old, ver_old) == old_secret
    assert decrypt_json(ct_new, nonce_new, ver_new) == new_secret


# ---------------------------------------------------------------------------
# 6. No key configured → RuntimeError
# ---------------------------------------------------------------------------

def test_no_key_configured_encrypt_raises():
    """Attempting to encrypt with no key configured raises RuntimeError."""
    from app.security.crypto import encrypt

    _clear_keys()  # ensure no key env vars

    with pytest.raises(RuntimeError, match="No connector secret key configured"):
        encrypt(b"some data")


def test_no_key_configured_decrypt_raises():
    """Attempting to decrypt with no key configured raises RuntimeError."""
    from app.security.crypto import decrypt

    _clear_keys()

    with pytest.raises(RuntimeError, match="No connector secret key configured"):
        decrypt(b"fake ct", b"fake nonce", 1)


# ---------------------------------------------------------------------------
# 7. InMemorySecretStore lifecycle
# ---------------------------------------------------------------------------

@pytest.fixture()
def secret_store():
    """Fresh InMemorySecretStore with a configured test key."""
    from app.connectors.secret_store import InMemorySecretStore, set_secret_store_for_tests

    _set_single_key(_random_b64_key(), version=1)
    store = InMemorySecretStore()
    set_secret_store_for_tests(store)
    yield store
    set_secret_store_for_tests(None)


@pytest.mark.asyncio
async def test_secret_store_put_get_roundtrip(secret_store):
    """put() then get() returns the original secret dict."""
    ds_id = "aaaaaaaa-0000-0000-0000-000000000001"
    org_id = "bbbbbbbb-0000-0000-0000-000000000001"
    secret = {"db_password": "hunter2", "host": "db.internal"}

    await secret_store.put(ds_id, org_id, secret)
    result = await secret_store.get(ds_id, org_id)

    assert result == secret


@pytest.mark.asyncio
async def test_secret_store_get_unknown_returns_none(secret_store):
    """get() for a datastore_id that was never stored returns None."""
    result = await secret_store.get(
        "aaaaaaaa-0000-0000-0000-000000000099",
        "bbbbbbbb-0000-0000-0000-000000000001",
    )
    assert result is None


@pytest.mark.asyncio
async def test_secret_store_delete(secret_store):
    """delete() removes the secret; subsequent get() returns None."""
    ds_id = "aaaaaaaa-0000-0000-0000-000000000002"
    org_id = "bbbbbbbb-0000-0000-0000-000000000001"
    secret = {"api_token": "tok_abc123"}

    await secret_store.put(ds_id, org_id, secret)
    assert await secret_store.get(ds_id, org_id) == secret

    await secret_store.delete(ds_id, org_id)
    assert await secret_store.get(ds_id, org_id) is None


@pytest.mark.asyncio
async def test_secret_store_delete_no_op_when_absent(secret_store):
    """delete() on a non-existent secret does not raise."""
    # Should not raise.
    await secret_store.delete(
        "aaaaaaaa-0000-0000-0000-999999999999",
        "bbbbbbbb-0000-0000-0000-000000000001",
    )


@pytest.mark.asyncio
async def test_secret_store_upsert_replaces(secret_store):
    """A second put() on the same datastore_id overwrites the previous secret."""
    ds_id = "aaaaaaaa-0000-0000-0000-000000000003"
    org_id = "bbbbbbbb-0000-0000-0000-000000000001"

    await secret_store.put(ds_id, org_id, {"password": "old"})
    await secret_store.put(ds_id, org_id, {"password": "new"})

    result = await secret_store.get(ds_id, org_id)
    assert result == {"password": "new"}


# ---------------------------------------------------------------------------
# 8. Org-scoping: org B cannot read org A's secret
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_org_scoping_cross_org_returns_none(secret_store):
    """A secret stored for org A is invisible to org B (returns None)."""
    ds_id = "aaaaaaaa-0000-0000-0000-000000000004"
    org_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    org_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    secret = {"bq_service_account": '{"type":"service_account"}'}

    await secret_store.put(ds_id, org_a, secret)

    # org_a can read it.
    assert await secret_store.get(ds_id, org_a) == secret
    # org_b gets None — NOT the secret.
    assert await secret_store.get(ds_id, org_b) is None


@pytest.mark.asyncio
async def test_org_scoping_delete_by_wrong_org_is_no_op(secret_store):
    """delete() by a different org does NOT remove the secret."""
    ds_id = "aaaaaaaa-0000-0000-0000-000000000005"
    org_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    org_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    secret = {"key": "value"}

    await secret_store.put(ds_id, org_a, secret)
    # org_b tries to delete org_a's secret.
    await secret_store.delete(ds_id, org_b)
    # org_a's secret is still there.
    assert await secret_store.get(ds_id, org_a) == secret


# ---------------------------------------------------------------------------
# 9. get_secret_store() provider returns InMemorySecretStore after injection
# ---------------------------------------------------------------------------

def test_get_secret_store_returns_injected_store():
    """get_secret_store() returns whatever was set via set_secret_store_for_tests()."""
    from app.connectors.secret_store import (
        InMemorySecretStore,
        get_secret_store,
        set_secret_store_for_tests,
    )

    store = InMemorySecretStore()
    set_secret_store_for_tests(store)
    try:
        assert get_secret_store() is store
    finally:
        set_secret_store_for_tests(None)


def test_get_secret_store_default_is_pg():
    """get_secret_store() creates a PgSecretStore lazily when no override is set."""
    from app.connectors.secret_store import (
        PgSecretStore,
        get_secret_store,
        set_secret_store_for_tests,
    )

    # Ensure no override.
    set_secret_store_for_tests(None)
    store = get_secret_store()
    assert isinstance(store, PgSecretStore)
    # Clean up the lazily created singleton so other tests are not affected.
    set_secret_store_for_tests(None)
