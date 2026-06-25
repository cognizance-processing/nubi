"""CMEK / CACHE — fail-closed CMEK client mode + cache-encryption isolation.

Covers:
  - CMEK client mode fails closed at EVERY entry point (provision, ingest open,
    export, tables-list) — never silently writes app-encrypted blobs DuckDB
    can't read.
  - Cache encryption: cross-tenant ciphertext replay fails (AAD binding).
  - Tampered ciphertext → cache miss (not a 500, not wrong data).
  - Misconfigured key → fail-closed at construction (no plaintext fallback).
"""

from __future__ import annotations

import base64
import os

import pytest

from app.connectors.cache_encryption import EncryptedCache, _decode_master_key
from app.lakehouse.cmek import assert_cmek_readable, get_cmek_provider
from app.lakehouse.managed import ManagedLakehouseError
from tests.security._custody_fixtures import auth_headers, custody_env  # noqa: F401


# ---------------------------------------------------------------------------
# CMEK fail-closed guard (canonical)
# ---------------------------------------------------------------------------


def test_assert_cmek_readable_blocks_client():
    with pytest.raises(ManagedLakehouseError) as ei:
        assert_cmek_readable("client")
    assert ei.value.code == "cmek_client_unsupported"
    assert ei.value.status == 501


@pytest.mark.parametrize("mode", ["none", "kms"])
def test_assert_cmek_readable_allows_supported(mode):
    assert_cmek_readable(mode)  # must not raise


def _enable_client_cmek(monkeypatch) -> None:
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_CMEK_MODE", "client")
    monkeypatch.setenv(
        "NUBI_CMEK_KEY_MATERIAL", base64.b64encode(os.urandom(32)).decode()
    )
    from app.config import get_settings
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_client_cmek_fails_closed_on_ingest_open(custody_env, monkeypatch):
    e = custody_env
    _enable_client_cmek(monkeypatch)
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions",
        json={"mode": "full_replace", "schema": [], "idempotency_key": "k"},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "cmek_client_unsupported"


@pytest.mark.asyncio
async def test_client_cmek_fails_closed_on_export(custody_env, monkeypatch):
    e = custody_env
    _enable_client_cmek(monkeypatch)
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export",
        json={"dest_uri": "file:///tmp/out", "sql": "SELECT 1"},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "cmek_client_unsupported"


@pytest.mark.asyncio
async def test_client_cmek_fails_closed_on_tables_list(custody_env, monkeypatch):
    e = custody_env
    _enable_client_cmek(monkeypatch)
    r = await e["client"].get(
        f"/api/v1/lake/{e['alice_ds']}/tables",
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "cmek_client_unsupported"


def test_client_cmek_misconfig_fails_closed(monkeypatch):
    """client mode + missing/short key → ValueError at construction (no fallback)."""
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_CMEK_MODE", "client")
    monkeypatch.delenv("NUBI_CMEK_KEY_MATERIAL", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        get_cmek_provider()

    # Wrong-length key also fails closed.
    monkeypatch.setenv("NUBI_CMEK_KEY_MATERIAL", base64.b64encode(b"too-short").decode())
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        get_cmek_provider()


# ---------------------------------------------------------------------------
# Cache encryption — cross-tenant replay + tamper
# ---------------------------------------------------------------------------


class _DictBackend:
    """Minimal cache backend that stores raw bytes by key."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def put(self, key, value, tags=None):
        self.store[key] = value

    def size(self):
        return len(self.store)

    def clear(self):
        self.store.clear()

    def stats(self):
        return {}

    def invalidate(self, tag):
        return 0

    def invalidate_all(self):
        return 0


def _key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_cache_roundtrip_same_tenant():
    backend = _DictBackend()
    cache = EncryptedCache(backend, _key_b64())
    cache.put("org:A|ds:1|hash", b"plaintext-arrow")
    assert cache.get("org:A|ds:1|hash") == b"plaintext-arrow"
    # The backing store never holds plaintext.
    assert backend.store["org:A|ds:1|hash"] != b"plaintext-arrow"


def test_cache_cross_tenant_replay_fails():
    """Copy org A's ciphertext into org B's slot → AAD mismatch → miss."""
    backend = _DictBackend()
    cache = EncryptedCache(backend, _key_b64())
    key_a = "org:A|ds:1|hash"
    key_b = "org:B|ds:1|hash"
    cache.put(key_a, b"tenant-A-secret")
    # Attacker copies the raw ciphertext from A's slot into B's slot.
    backend.store[key_b] = backend.store[key_a]
    # Reading under B's key fails authentication → cache miss (None), never A's data.
    assert cache.get(key_b) is None


def test_cache_tampered_ciphertext_fails():
    backend = _DictBackend()
    cache = EncryptedCache(backend, _key_b64())
    key = "org:A|ds:1|hash"
    cache.put(key, b"important")
    blob = bytearray(backend.store[key])
    blob[-1] ^= 0xFF  # flip a bit in the GCM tag/ciphertext
    backend.store[key] = bytes(blob)
    assert cache.get(key) is None  # tamper → miss, not garbage


def test_cache_wrong_key_fails():
    backend = _DictBackend()
    EncryptedCache(backend, _key_b64()).put("k", b"data")
    other = EncryptedCache(backend, _key_b64())  # different master key
    assert other.get("k") is None


def test_cache_key_misconfig_rejected():
    with pytest.raises(ValueError):
        _decode_master_key("not-base64!!!")
    with pytest.raises(ValueError):
        _decode_master_key(base64.b64encode(b"short").decode())  # not 32 bytes
