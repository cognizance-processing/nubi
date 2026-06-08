"""Tests for the Nubi secrets subsystem.

Coverage
--------
1. Encryption round-trip (crypto.py)
   a. encrypt() returns bytes.
   b. decrypt(encrypt(x)) == x for ASCII and Unicode values.
   c. encrypt produces distinct ciphertext on repeated calls (probabilistic Fernet).
   d. decrypt raises ValueError on corrupted ciphertext.
   e. encrypt/decrypt raise RuntimeError when NUBI_SECRETS_KEY is unset.
   f. encrypt/decrypt raise RuntimeError when NUBI_SECRETS_KEY is invalid.

2. InMemorySecretStore — set/get
   a. set_secret returns a dict with expected keys (no value_encrypted).
   b. get_secret returns decrypted plaintext.
   c. get_secret returns None for unknown name.
   d. set_secret is idempotent (upsert): second call updates value.

3. InMemorySecretStore — list (never includes value)
   a. list_secrets returns empty list when no secrets exist.
   b. list_secrets returns entries sorted by name.
   c. list_secrets NEVER includes value_encrypted in any returned dict.
   d. list_secrets is org-scoped (does not leak cross-org secrets).

4. InMemorySecretStore — delete
   a. delete_secret returns True when secret existed.
   b. delete_secret returns False for unknown name.
   c. After delete, get_secret returns None.
   d. After delete, secret absent from list_secrets.

5. InMemorySecretStore — resolve_all
   a. resolve_all returns {name: plaintext} mapping.
   b. resolve_all is org-scoped.
   c. resolve_all returns empty dict when no secrets exist.

6. Isolation
   a. Store instances are independent.
   b. Returned dicts are deep copies (mutating them does not affect store).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Environment: set a valid NUBI_SECRETS_KEY before importing crypto / store.
# ---------------------------------------------------------------------------
# Generate a key deterministically for tests so the suite is self-contained.
# This is done at module level before any imports so the env var is in place
# when lazy imports fire inside the functions under test.

from cryptography.fernet import Fernet  # noqa: E402 — must import before env set

_TEST_KEY = Fernet.generate_key().decode()
os.environ["NUBI_SECRETS_KEY"] = _TEST_KEY  # set before any lazy import fires

from app.secrets.crypto import decrypt, encrypt  # noqa: E402
from app.secrets.store import InMemorySecretStore  # noqa: E402

# Async tests use asyncio mode; apply the mark only to those test classes.
# The crypto round-trip tests are synchronous (no store, no await).


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _org() -> str:
    return str(uuid.uuid4())


def _user() -> str:
    return str(uuid.uuid4())


async def _set(
    store: InMemorySecretStore,
    org_id: str,
    name: str = "MY_SECRET",
    value: str = "s3cr3t",
    created_by: str | None = None,
) -> dict[str, Any]:
    return await store.set_secret(
        org_id=org_id,
        name=name,
        value=value,
        created_by=created_by or _user(),
    )


# ===========================================================================
# 1. Encryption round-trip
# ===========================================================================


class TestCryptoRoundTrip:
    """encrypt / decrypt contract."""

    def test_encrypt_returns_bytes(self):
        result = encrypt("hello")
        assert isinstance(result, bytes)

    def test_decrypt_roundtrip_ascii(self):
        plaintext = "my-super-secret-password-123"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_decrypt_roundtrip_unicode(self):
        plaintext = "pää \U0001f511 secret"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_encrypt_produces_distinct_ciphertext(self):
        blob1 = encrypt("same")
        blob2 = encrypt("same")
        # Fernet uses a random IV; two encryptions of the same plaintext differ.
        assert blob1 != blob2

    def test_decrypt_raises_value_error_on_corrupt_blob(self):
        with pytest.raises(ValueError, match="decryption failed"):
            decrypt(b"this-is-not-a-valid-fernet-token")

    def test_missing_key_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("NUBI_SECRETS_KEY", raising=False)
        # Reload the module function so it picks up the missing env var.
        import importlib  # noqa: PLC0415
        import app.secrets.crypto as _mod  # noqa: PLC0415

        importlib.reload(_mod)
        with pytest.raises(RuntimeError, match="NUBI_SECRETS_KEY is not set"):
            _mod.encrypt("test")
        # Restore for subsequent tests.
        monkeypatch.setenv("NUBI_SECRETS_KEY", _TEST_KEY)
        importlib.reload(_mod)

    def test_invalid_key_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("NUBI_SECRETS_KEY", "not-a-valid-fernet-key")
        import importlib  # noqa: PLC0415
        import app.secrets.crypto as _mod  # noqa: PLC0415

        importlib.reload(_mod)
        with pytest.raises(RuntimeError, match="NUBI_SECRETS_KEY is invalid"):
            _mod.encrypt("test")
        # Restore for subsequent tests.
        monkeypatch.setenv("NUBI_SECRETS_KEY", _TEST_KEY)
        importlib.reload(_mod)


# ===========================================================================
# 2. set / get
# ===========================================================================


@pytest.mark.asyncio
class TestSetGet:
    """InMemorySecretStore.set_secret and get_secret."""

    async def test_set_returns_dict_with_expected_keys(self):
        store = InMemorySecretStore()
        org = _org()
        result = await _set(store, org)
        assert isinstance(result, dict)
        for key in ("id", "org_id", "name", "created_by", "created_at", "updated_at"):
            assert key in result, f"Missing key: {key}"

    async def test_set_does_not_expose_value_encrypted(self):
        store = InMemorySecretStore()
        result = await _set(store, _org())
        assert "value_encrypted" not in result

    async def test_set_then_get_returns_plaintext(self):
        store = InMemorySecretStore()
        org = _org()
        await _set(store, org, name="API_KEY", value="tok_abc123")
        assert await store.get_secret(org, "API_KEY") == "tok_abc123"

    async def test_get_returns_none_for_unknown_name(self):
        store = InMemorySecretStore()
        assert await store.get_secret(_org(), "NO_SUCH_SECRET") is None

    async def test_set_is_upsert_updates_value(self):
        store = InMemorySecretStore()
        org = _org()
        await _set(store, org, name="FOO", value="old")
        await _set(store, org, name="FOO", value="new")
        assert await store.get_secret(org, "FOO") == "new"

    async def test_upsert_preserves_id(self):
        store = InMemorySecretStore()
        org = _org()
        first = await _set(store, org, name="KEY", value="v1")
        second = await _set(store, org, name="KEY", value="v2")
        assert first["id"] == second["id"]

    async def test_org_scoped_set_get(self):
        store = InMemorySecretStore()
        org1, org2 = _org(), _org()
        await _set(store, org1, name="TOKEN", value="org1-token")
        await _set(store, org2, name="TOKEN", value="org2-token")
        assert await store.get_secret(org1, "TOKEN") == "org1-token"
        assert await store.get_secret(org2, "TOKEN") == "org2-token"


# ===========================================================================
# 3. list (never includes value)
# ===========================================================================


@pytest.mark.asyncio
class TestListSecrets:
    """InMemorySecretStore.list_secrets contract."""

    async def test_list_empty(self):
        store = InMemorySecretStore()
        assert await store.list_secrets(_org()) == []

    async def test_list_returns_sorted_by_name(self):
        store = InMemorySecretStore()
        org = _org()
        for name in ("ZEBRA", "ALPHA", "MIDDLE"):
            await _set(store, org, name=name)
        results = await store.list_secrets(org)
        assert [r["name"] for r in results] == ["ALPHA", "MIDDLE", "ZEBRA"]

    async def test_list_never_includes_value_encrypted(self):
        store = InMemorySecretStore()
        org = _org()
        await _set(store, org, name="SECRET_A", value="some-value")
        await _set(store, org, name="SECRET_B", value="other-value")
        for row in await store.list_secrets(org):
            assert "value_encrypted" not in row
            assert "value" not in row

    async def test_list_is_org_scoped(self):
        store = InMemorySecretStore()
        org1, org2 = _org(), _org()
        await _set(store, org1, name="ORG1_SECRET")
        await _set(store, org2, name="ORG2_SECRET")
        names1 = {r["name"] for r in await store.list_secrets(org1)}
        names2 = {r["name"] for r in await store.list_secrets(org2)}
        assert names1 == {"ORG1_SECRET"}
        assert names2 == {"ORG2_SECRET"}

    async def test_list_count(self):
        store = InMemorySecretStore()
        org = _org()
        for i in range(3):
            await _set(store, org, name=f"S{i}")
        assert len(await store.list_secrets(org)) == 3


# ===========================================================================
# 4. delete
# ===========================================================================


@pytest.mark.asyncio
class TestDeleteSecret:
    """InMemorySecretStore.delete_secret contract."""

    async def test_delete_existing_returns_true(self):
        store = InMemorySecretStore()
        org = _org()
        await _set(store, org, name="GONE")
        assert await store.delete_secret(org, "GONE") is True

    async def test_delete_nonexistent_returns_false(self):
        store = InMemorySecretStore()
        assert await store.delete_secret(_org(), "NOT_HERE") is False

    async def test_get_returns_none_after_delete(self):
        store = InMemorySecretStore()
        org = _org()
        await _set(store, org, name="TEMP", value="data")
        await store.delete_secret(org, "TEMP")
        assert await store.get_secret(org, "TEMP") is None

    async def test_list_excludes_deleted_secret(self):
        store = InMemorySecretStore()
        org = _org()
        await _set(store, org, name="KEEP", value="a")
        await _set(store, org, name="DEL", value="b")
        await store.delete_secret(org, "DEL")
        names = {r["name"] for r in await store.list_secrets(org)}
        assert names == {"KEEP"}


# ===========================================================================
# 5. resolve_all
# ===========================================================================


@pytest.mark.asyncio
class TestResolveAll:
    """InMemorySecretStore.resolve_all contract."""

    async def test_resolve_all_returns_plaintext_mapping(self):
        store = InMemorySecretStore()
        org = _org()
        await _set(store, org, name="DB_PASS", value="hunter2")
        await _set(store, org, name="API_KEY", value="abc-def")
        resolved = await store.resolve_all(org)
        assert resolved == {"DB_PASS": "hunter2", "API_KEY": "abc-def"}

    async def test_resolve_all_is_org_scoped(self):
        store = InMemorySecretStore()
        org1, org2 = _org(), _org()
        await _set(store, org1, name="X", value="org1")
        await _set(store, org2, name="Y", value="org2")
        resolved = await store.resolve_all(org1)
        assert "X" in resolved
        assert "Y" not in resolved

    async def test_resolve_all_empty(self):
        store = InMemorySecretStore()
        assert await store.resolve_all(_org()) == {}


# ===========================================================================
# 6. Isolation
# ===========================================================================


@pytest.mark.asyncio
class TestIsolation:
    """Store instances are independent; returned dicts are copies."""

    async def test_store_instances_are_independent(self):
        store_a = InMemorySecretStore()
        store_b = InMemorySecretStore()
        org = _org()
        await _set(store_a, org, name="ONLY_A", value="secret")
        assert await store_b.get_secret(org, "ONLY_A") is None

    async def test_mutating_returned_set_dict_does_not_affect_store(self):
        store = InMemorySecretStore()
        org = _org()
        result = await _set(store, org, name="IMMUTABLE", value="orig")
        # Mutate the returned dict.
        result["name"] = "CHANGED"
        # The store must be unaffected.
        listed = await store.list_secrets(org)
        assert any(r["name"] == "IMMUTABLE" for r in listed)

    async def test_mutating_listed_dict_does_not_affect_store(self):
        store = InMemorySecretStore()
        org = _org()
        await _set(store, org, name="SAFE", value="original")
        listed = await store.list_secrets(org)
        for row in listed:
            row["name"] = "HACKED"
        # List again — must still return original name.
        fresh = await store.list_secrets(org)
        assert any(r["name"] == "SAFE" for r in fresh)
