"""MCP auth-token encryption must fail cleanly when the secret key is unset.

Regression: registering an MCP server *with* an ``auth_token`` while the server
has no ``CONNECTOR_SECRET_KEY`` configured used to surface the crypto layer's
``RuntimeError`` as an opaque HTTP 500. It must instead be a clear, actionable
503 (``secret_encryption_unconfigured``).
"""

from __future__ import annotations

import base64

import pytest

from app.errors import AppError
from app.mcp.store import _encrypt_auth_token


def test_encrypt_auth_token_raises_503_when_key_unset(monkeypatch):
    monkeypatch.delenv("CONNECTOR_SECRET_KEY", raising=False)
    monkeypatch.delenv("CONNECTOR_SECRET_KEYS", raising=False)
    # Reset the module-level key cache so the unset env is re-read.
    import app.security.crypto as crypto

    monkeypatch.setattr(crypto, "_key_registry", None, raising=False)
    monkeypatch.setattr(crypto, "_current_version", None, raising=False)

    with pytest.raises(AppError) as ei:
        _encrypt_auth_token("super-secret-token")

    assert ei.value.status == 503
    assert ei.value.code == "secret_encryption_unconfigured"
    # The clear message must not leak the secret.
    assert "super-secret-token" not in str(ei.value)


def test_encrypt_auth_token_succeeds_with_key(monkeypatch):
    monkeypatch.delenv("CONNECTOR_SECRET_KEYS", raising=False)
    monkeypatch.setenv("CONNECTOR_SECRET_KEY", base64.b64encode(b"0" * 32).decode())
    monkeypatch.setenv("CONNECTOR_SECRET_KEY_VERSION", "1")
    import app.security.crypto as crypto

    monkeypatch.setattr(crypto, "_key_registry", None, raising=False)
    monkeypatch.setattr(crypto, "_current_version", None, raising=False)

    ct, nonce, kv = _encrypt_auth_token("super-secret-token")
    assert isinstance(ct, (bytes, bytearray)) and len(ct) > 0
    assert isinstance(nonce, (bytes, bytearray)) and len(nonce) > 0
    assert kv == 1
