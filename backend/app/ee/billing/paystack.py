"""Paystack payment client for Nubi EE billing.

Provides:
- :func:`initialize_transaction` — create a Paystack checkout session.
- :func:`verify_transaction` — verify a transaction by reference.
- :func:`verify_webhook_signature` — validate HMAC-SHA512 webhook payloads.

Design
------
- ``PAYSTACK_SECRET_KEY`` is read from the environment at call-time, not at
  import time, so tests can patch ``os.environ`` or replace the getter.
- HTTP is performed via ``httpx`` (async) with a lazy import so that the
  module can be imported in environments where httpx is absent without
  crashing the OSS server.
- The client is test-mockable via :func:`set_client_for_tests` /
  :func:`get_client`.  Tests should supply an object with the same async
  ``get`` / ``post`` interface as :class:`PaystackClient`.
- No network calls are made at import time; no live keys are hard-coded.

Example
-------
>>> import os, asyncio
>>> os.environ["PAYSTACK_SECRET_KEY"] = "sk_test_..."
>>> from app.ee.billing.paystack import initialize_transaction
>>> result = asyncio.run(initialize_transaction(
...     email="user@example.com",
...     amount_kobo=149900,
...     reference="nubi-sub-123",
...     callback_url="https://app.nubi.io/billing/confirm",
...     metadata={"org_id": "abc"},
... ))
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PAYSTACK_BASE_URL = "https://api.paystack.co"


def _get_secret_key() -> str:
    """Return PAYSTACK_SECRET_KEY from env; raise if missing."""
    key = os.environ.get("PAYSTACK_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "PAYSTACK_SECRET_KEY is not set.  "
            "Configure it in the environment before using billing."
        )
    return key


# ---------------------------------------------------------------------------
# Testable client wrapper
# ---------------------------------------------------------------------------


class PaystackClient:
    """Thin async HTTP wrapper around the Paystack REST API.

    All methods are async and accept a *secret_key* parameter so that
    callers can provide per-request keys (useful in tests).

    HTTP calls are made via ``httpx.AsyncClient`` imported lazily.
    """

    async def _headers(self, secret_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        }

    async def post(
        self,
        path: str,
        *,
        secret_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """POST *payload* to *path* on the Paystack API.

        Parameters
        ----------
        path:
            API path, e.g. ``"/transaction/initialize"``.
        secret_key:
            Paystack secret key (``sk_...``).
        payload:
            JSON body.

        Returns
        -------
        dict
            Parsed JSON response body.

        Raises
        ------
        RuntimeError
            When the Paystack API returns a non-2xx status.
        """
        import httpx  # noqa: PLC0415

        url = f"{_PAYSTACK_BASE_URL}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                headers=await self._headers(secret_key),
                json=payload,
            )
        if not resp.is_success:
            raise RuntimeError(
                f"Paystack POST {path} failed: {resp.status_code} — {resp.text[:200]}"
            )
        return resp.json()

    async def get(
        self,
        path: str,
        *,
        secret_key: str,
    ) -> dict[str, Any]:
        """GET from *path* on the Paystack API.

        Parameters
        ----------
        path:
            API path, e.g. ``"/transaction/verify/ref123"``.
        secret_key:
            Paystack secret key.

        Returns
        -------
        dict
            Parsed JSON response body.

        Raises
        ------
        RuntimeError
            When the Paystack API returns a non-2xx status.
        """
        import httpx  # noqa: PLC0415

        url = f"{_PAYSTACK_BASE_URL}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers=await self._headers(secret_key),
            )
        if not resp.is_success:
            raise RuntimeError(
                f"Paystack GET {path} failed: {resp.status_code} — {resp.text[:200]}"
            )
        return resp.json()


# ---------------------------------------------------------------------------
# Client provider (injectable for tests)
# ---------------------------------------------------------------------------

_client: PaystackClient | None = None


def get_client() -> PaystackClient:
    """Return the active :class:`PaystackClient` singleton.

    Tests replace this via :func:`set_client_for_tests`.
    """
    global _client  # noqa: PLW0603
    if _client is None:
        _client = PaystackClient()
    return _client


def set_client_for_tests(client: PaystackClient | None) -> None:
    """Inject a test double or reset to default.

    Parameters
    ----------
    client:
        A mock/stub with the same ``get`` / ``post`` async interface, or
        ``None`` to restore the default ``PaystackClient``.
    """
    global _client  # noqa: PLW0603
    _client = client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def initialize_transaction(
    *,
    email: str,
    amount_kobo: int,
    reference: str,
    callback_url: str,
    metadata: dict[str, Any] | None = None,
    secret_key: str | None = None,
) -> dict[str, Any]:
    """Create a Paystack transaction and return the checkout URL.

    Parameters
    ----------
    email:
        Customer's email address.
    amount_kobo:
        Amount in *kobo* (smallest currency unit).  For ZAR, 1 rand = 100 kobo.
        Pass ``149900`` for R 1 499.00.
    reference:
        Unique transaction reference (must be unique per transaction).
    callback_url:
        URL Paystack redirects the customer to after payment.
    metadata:
        Optional dict of extra metadata stored against the transaction.
    secret_key:
        Override the env-based secret key (primarily for tests).

    Returns
    -------
    dict
        Full Paystack ``initialize`` response body.  The checkout URL is at
        ``result["data"]["authorization_url"]``.

    Raises
    ------
    RuntimeError
        When ``PAYSTACK_SECRET_KEY`` is unset or Paystack returns an error.
    """
    key = secret_key or _get_secret_key()
    payload: dict[str, Any] = {
        "email": email,
        "amount": amount_kobo,
        "reference": reference,
        "callback_url": callback_url,
        "currency": "ZAR",
    }
    if metadata:
        payload["metadata"] = metadata

    return await get_client().post(
        "/transaction/initialize",
        secret_key=key,
        payload=payload,
    )


async def verify_transaction(
    reference: str,
    *,
    secret_key: str | None = None,
) -> dict[str, Any]:
    """Verify a Paystack transaction by its reference.

    Parameters
    ----------
    reference:
        The transaction reference string used in :func:`initialize_transaction`.
    secret_key:
        Override the env-based secret key (primarily for tests).

    Returns
    -------
    dict
        Full Paystack ``verify`` response body.  Status is at
        ``result["data"]["status"]`` (``"success"`` / ``"failed"`` / …).

    Raises
    ------
    RuntimeError
        When ``PAYSTACK_SECRET_KEY`` is unset or Paystack returns an error.
    """
    key = secret_key or _get_secret_key()
    return await get_client().get(
        f"/transaction/verify/{reference}",
        secret_key=key,
    )


def verify_webhook_signature(
    raw_body: bytes,
    x_paystack_signature: str,
    *,
    secret_key: str | None = None,
) -> bool:
    """Verify a Paystack webhook request signature.

    Paystack signs webhook payloads with HMAC-SHA512 using the secret key.
    The signature is sent in the ``X-Paystack-Signature`` header.

    Parameters
    ----------
    raw_body:
        The raw (undecoded) request body bytes.
    x_paystack_signature:
        The value of the ``X-Paystack-Signature`` header.
    secret_key:
        Override the env-based secret key.  When ``None``, reads from
        ``PAYSTACK_SECRET_KEY`` env var.  If the env var is also absent,
        returns ``False`` rather than raising (webhook handlers must not crash
        on bad config).

    Returns
    -------
    bool
        ``True`` when the signature is valid.  ``False`` when the signature
        does not match or the secret key is missing.
    """
    if secret_key is None:
        secret_key = os.environ.get("PAYSTACK_SECRET_KEY", "").strip()
    if not secret_key:
        return False

    expected = hmac.new(
        secret_key.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha512,
    ).hexdigest()

    return hmac.compare_digest(expected, x_paystack_signature.lower())
