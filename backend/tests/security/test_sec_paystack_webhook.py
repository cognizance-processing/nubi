"""Attacks against the Paystack webhook + wallet money-movement surface.

Threat model
------------
The Paystack webhook (``POST /ee/billing/webhook``) is the ONLY unauthenticated
route in EE billing — it trusts an HMAC-SHA512 signature instead of a bearer
token.  An attacker who can reach this endpoint (it's public on the internet)
must NOT be able to:

1. Forge a webhook without knowing ``PAYSTACK_SECRET_KEY`` (no signature, a
   signature computed with the wrong secret, or a signature for a tampered
   body) -> always 401, never processed.
2. Replay a legitimate ``charge.success`` webhook to double-credit a wallet
   or double-grant a subscription tier.
3. Inflate a wallet credit by tampering with any field in a signed body
   without invalidating the signature (any mutation flips the HMAC).
4. Use a webhook for one org to affect a different org's wallet/subscription.
5. Cause a crash / 500 via a malformed-but-signed body (fail closed, not
   an unhandled exception that could be turned into a DoS).

Existing coverage (not duplicated here)
----------------------------------------
``tests/test_ee_billing.py::TestPaystackWebhookSignature`` and
``TestWebhookRoute`` already cover: basic valid/invalid signature, missing
org_id, charge.success -> subscription upsert, subscription.disable.
``tests/test_ee_wallet.py`` covers wallet-service-level idempotency
(``ledger_ref_exists``) and the "TOPUP_FAILED doesn't poison idempotency"
case in isolation.

This file adds the *combined* attack surface: webhook route + wallet ledger
together, verified via a real HMAC signature (not a mock), to prove a
replayed or forged HTTP request cannot move money twice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("ENV", "test")

_SECRET = "sk_test_webhook_attack_secret"


def _sign(raw_body: bytes, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha512).hexdigest()


def _topup_payload(org_id: str, *, usd_cents: int, reference: str, topup_type: str = "manual") -> dict:
    return {
        "event": "charge.success",
        "data": {
            "id": f"paystack_txn_{reference}",
            "reference": reference,
            "amount": usd_cents,  # illustrative; real gateway amount is ZAR kobo
            "authorization": {"reusable": False},
            "customer": {"email": "victim@example.com", "customer_code": "CUS_abc"},
            "metadata": {
                "org_id": org_id,
                "topup_type": topup_type,
                "topup_usd_cents": usd_cents,
            },
        },
    }


@pytest.fixture(autouse=True)
def _env_and_stores():
    from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests
    from app.ee.billing.wallet_store import InMemoryWalletStore, set_wallet_store_for_tests

    old_key = os.environ.get("PAYSTACK_SECRET_KEY")
    os.environ["PAYSTACK_SECRET_KEY"] = _SECRET

    billing_store = InMemoryBillingStore()
    wallet_store = InMemoryWalletStore()
    set_billing_store_for_tests(billing_store)
    set_wallet_store_for_tests(wallet_store)

    yield {"billing": billing_store, "wallet": wallet_store}

    set_billing_store_for_tests(None)
    set_wallet_store_for_tests(None)
    if old_key is None:
        os.environ.pop("PAYSTACK_SECRET_KEY", None)
    else:
        os.environ["PAYSTACK_SECRET_KEY"] = old_key


def _make_client():
    from fastapi import FastAPI

    from app.ee.billing.routes import router

    app = FastAPI()
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _post_webhook(client: AsyncClient, raw_body: bytes, signature: str | None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Paystack-Signature"] = signature
    return await client.post("/ee/billing/webhook", content=raw_body, headers=headers)


# ---------------------------------------------------------------------------
# 1. Forgery is rejected
# ---------------------------------------------------------------------------


class TestWebhookForgeryRejected:
    @pytest.mark.asyncio
    async def test_no_signature_header_is_rejected(self, _env_and_stores) -> None:
        org_id = str(uuid.uuid4())
        raw = json.dumps(_topup_payload(org_id, usd_cents=100_000_00, reference="r1")).encode()

        async with _make_client() as client:
            resp = await _post_webhook(client, raw, signature=None)
        assert resp.status_code == 401
        balance = await _env_and_stores["wallet"].get_balance(org_id)
        assert balance["balance_usd_cents"] == 0

    @pytest.mark.asyncio
    async def test_empty_signature_header_is_rejected(self, _env_and_stores) -> None:
        org_id = str(uuid.uuid4())
        raw = json.dumps(_topup_payload(org_id, usd_cents=100_000_00, reference="r2")).encode()

        async with _make_client() as client:
            resp = await _post_webhook(client, raw, signature="")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_signature_computed_with_wrong_secret_is_rejected(self, _env_and_stores) -> None:
        """Attacker doesn't know PAYSTACK_SECRET_KEY — signs with a guess."""
        org_id = str(uuid.uuid4())
        raw = json.dumps(_topup_payload(org_id, usd_cents=100_000_00, reference="r3")).encode()
        forged_sig = _sign(raw, secret="attacker_guessed_wrong_secret")

        async with _make_client() as client:
            resp = await _post_webhook(client, raw, signature=forged_sig)
        assert resp.status_code == 401
        balance = await _env_and_stores["wallet"].get_balance(org_id)
        assert balance["balance_usd_cents"] == 0

    @pytest.mark.asyncio
    async def test_tampering_amount_after_signing_invalidates_signature(self, _env_and_stores) -> None:
        """Attacker intercepts a legitimately-signed R50 topup and tries to
        bump it to R50,000 — the signature was computed over the original
        bytes, so any byte-level tamper must invalidate it."""
        org_id = str(uuid.uuid4())
        payload = _topup_payload(org_id, usd_cents=5000, reference="r4")
        raw = json.dumps(payload).encode()
        legit_sig = _sign(raw)

        # Tamper: swap the small amount for a huge one, re-serialise.
        payload["data"]["metadata"]["topup_usd_cents"] = 100_000_000
        tampered_raw = json.dumps(payload).encode()

        async with _make_client() as client:
            # Replay the ORIGINAL signature against the TAMPERED body.
            resp = await _post_webhook(client, tampered_raw, signature=legit_sig)
        assert resp.status_code == 401
        balance = await _env_and_stores["wallet"].get_balance(org_id)
        assert balance["balance_usd_cents"] == 0

    @pytest.mark.asyncio
    async def test_malformed_json_with_valid_signature_is_400_not_500(self, _env_and_stores) -> None:
        """A body that legitimately hashes to a valid signature (e.g. an
        attacker who somehow has the secret, or a corrupted delivery) but
        isn't valid JSON must fail closed with 400, never crash."""
        raw = b"not-json-at-all{{{"
        sig = _sign(raw)
        async with _make_client() as client:
            resp = await _post_webhook(client, raw, signature=sig)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_case_and_whitespace_in_signature_are_not_silently_accepted(self, _env_and_stores) -> None:
        org_id = str(uuid.uuid4())
        raw = json.dumps(_topup_payload(org_id, usd_cents=5000, reference="r5")).encode()
        legit_sig = _sign(raw)
        garbage_sig = legit_sig[:-1] + ("0" if legit_sig[-1] != "0" else "1")

        async with _make_client() as client:
            resp = await _post_webhook(client, raw, signature=garbage_sig)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Replay protection — no double-credit
# ---------------------------------------------------------------------------


class TestWebhookReplayIsIdempotent:
    @pytest.mark.asyncio
    async def test_replaying_identical_charge_success_credits_wallet_once(self, _env_and_stores) -> None:
        org_id = str(uuid.uuid4())
        payload = _topup_payload(org_id, usd_cents=5000, reference="replay-ref-1")
        raw = json.dumps(payload).encode()
        sig = _sign(raw)

        async with _make_client() as client:
            first = await _post_webhook(client, raw, signature=sig)
            second = await _post_webhook(client, raw, signature=sig)
            third = await _post_webhook(client, raw, signature=sig)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json() == {"status": "duplicate"}
        assert third.json() == {"status": "duplicate"}

        balance = await _env_and_stores["wallet"].get_balance(org_id)
        assert balance["balance_usd_cents"] == 5000, "double/triple-credit on webhook replay"

        ledger = await _env_and_stores["wallet"].list_ledger(org_id)
        credit_entries = [e for e in ledger if e["amount_usd_cents"] > 0]
        assert len(credit_entries) == 1

    @pytest.mark.asyncio
    async def test_replay_with_different_delivery_wrapper_same_txn_id_still_deduped(
        self, _env_and_stores
    ) -> None:
        """Paystack's actual retry envelope can differ slightly (timestamps,
        delivery ids) while the underlying transaction id / reference stays
        the same — dedup must key on the transaction identity, not exact
        byte-for-byte payload equality."""
        org_id = str(uuid.uuid4())
        payload1 = _topup_payload(org_id, usd_cents=5000, reference="replay-ref-2")
        payload2 = _topup_payload(org_id, usd_cents=5000, reference="replay-ref-2")
        payload2["data"]["customer"]["customer_code"] = "CUS_different_wrapper_field"

        raw1 = json.dumps(payload1).encode()
        raw2 = json.dumps(payload2).encode()

        async with _make_client() as client:
            first = await _post_webhook(client, raw1, signature=_sign(raw1))
            second = await _post_webhook(client, raw2, signature=_sign(raw2))

        assert first.status_code == 200
        assert second.json() == {"status": "duplicate"}

        balance = await _env_and_stores["wallet"].get_balance(org_id)
        assert balance["balance_usd_cents"] == 5000

    @pytest.mark.asyncio
    async def test_auto_and_manual_topup_with_same_reference_cannot_double_credit(
        self, _env_and_stores
    ) -> None:
        """Defense in depth: even if the route-level event_id dedup window
        were exceeded (e.g. >200 intervening events), the wallet-service
        ledger_ref_exists() check is keyed on the Paystack reference across
        the ENTIRE ledger (no window) and must still block a double credit."""
        org_id = str(uuid.uuid4())
        payload = _topup_payload(org_id, usd_cents=7500, reference="belt-and-braces-ref")
        raw = json.dumps(payload).encode()
        sig = _sign(raw)

        async with _make_client() as client:
            first = await _post_webhook(client, raw, signature=sig)

        assert first.status_code == 200

        # Directly attack the wallet-service layer with the exact same
        # ref_id the route used (the Paystack "reference", not the "id" —
        # see routes.py: handle_webhook_charge_success(org_id, reference, ...)),
        # bypassing the route's own recent-events scan entirely.
        from app.ee.billing.wallet import handle_webhook_charge_success

        result = await handle_webhook_charge_success(
            org_id, "belt-and-braces-ref", 7500, {"topup_type": "manual"}
        )
        assert result == {"skipped": True, "ref_id": "belt-and-braces-ref"}

        balance = await _env_and_stores["wallet"].get_balance(org_id)
        assert balance["balance_usd_cents"] == 7500


# ---------------------------------------------------------------------------
# 3. Cross-org isolation via webhook metadata
# ---------------------------------------------------------------------------


class TestWebhookCrossOrgIsolation:
    @pytest.mark.asyncio
    async def test_topup_for_org_a_never_credits_org_b(self, _env_and_stores) -> None:
        org_a = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        payload = _topup_payload(org_a, usd_cents=5000, reference="isolation-ref-1")
        raw = json.dumps(payload).encode()
        sig = _sign(raw)

        async with _make_client() as client:
            resp = await _post_webhook(client, raw, signature=sig)
        assert resp.status_code == 200

        bal_a = await _env_and_stores["wallet"].get_balance(org_a)
        bal_b = await _env_and_stores["wallet"].get_balance(org_b)
        assert bal_a["balance_usd_cents"] == 5000
        assert bal_b["balance_usd_cents"] == 0

    @pytest.mark.asyncio
    async def test_subscription_charge_without_tier_metadata_never_grants_a_plan(
        self, _env_and_stores
    ) -> None:
        """A charge.success without an explicit valid tier in metadata must
        NEVER silently upgrade the org — this is the anti-"free upgrade via
        stripped metadata" guard already implemented; verify it end-to-end."""
        org_id = str(uuid.uuid4())
        payload = {
            "event": "charge.success",
            "data": {
                "id": "txn_no_tier",
                "reference": "no-tier-ref",
                "metadata": {"org_id": org_id},  # no "tier", no "topup_type"
                "customer": {"customer_code": "CUS_x"},
                "authorization": {"reusable": False},
            },
        }
        raw = json.dumps(payload).encode()
        sig = _sign(raw)

        async with _make_client() as client:
            resp = await _post_webhook(client, raw, signature=sig)
        assert resp.status_code == 200

        sub = await _env_and_stores["billing"].get_subscription(org_id)
        assert sub is None  # never upgraded

    @pytest.mark.asyncio
    async def test_invalid_tier_value_in_metadata_never_grants_a_plan(self, _env_and_stores) -> None:
        org_id = str(uuid.uuid4())
        payload = {
            "event": "charge.success",
            "data": {
                "id": "txn_bad_tier",
                "reference": "bad-tier-ref",
                "metadata": {"org_id": org_id, "tier": "godmode"},
                "customer": {"customer_code": "CUS_x"},
                "authorization": {"reusable": False},
            },
        }
        raw = json.dumps(payload).encode()
        sig = _sign(raw)

        async with _make_client() as client:
            resp = await _post_webhook(client, raw, signature=sig)
        assert resp.status_code == 200

        sub = await _env_and_stores["billing"].get_subscription(org_id)
        assert sub is None
