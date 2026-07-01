"""Cross-org IDOR + authorization enforcement for EE billing HTTP routes.

Every ``app.ee.billing.routes`` endpoint that accepts a caller-supplied
``org_id`` MUST call ``_require_org_access`` before touching any store data —
otherwise any authenticated user could read (or charge!) another org's
billing.  See the module docstring in ``app/ee/billing/routes.py``.

Why this file exists
---------------------
``_require_org_access`` resolves membership via ``get_org_role(user_id,
org_id, get_repo())``.  When the DB pool is not initialised (the situation in
essentially every other billing test in this repo, which never call
``set_repo(...)``), the check is deliberately SKIPPED with a warning — safe in
production (the pool is always initialised before serving requests), but it
means none of the existing billing route tests actually exercise real
membership enforcement; they all pass through the "no pool -> allow" escape
hatch.

This file forces the REAL enforcement path by injecting an
``InMemoryRepo`` (which satisfies ``get_org_role`` without touching
``app.db`` at all — see ``app/auth/roles.py::get_org_role``'s
``hasattr(repo, "_org_members")`` branch), and seeds explicit org
memberships.  It then attacks every EE billing route as a cross-org and
same-org-but-under-privileged caller.

Attack classes covered
-----------------------
1. Cross-org read: a caller who is not a member of ``org_id`` -> 403 on every
   read route (tier, events, invoices, invoices/current-cycle, wallet).
2. Cross-org money-move / config-mutate: a non-member -> 403 on checkout,
   wallet/topup, wallet/autotopup.
3. Same-org privilege escalation: a plain "member"/"viewer" (non-admin) ->
   403 on checkout, wallet/topup, wallet/autotopup (admin/owner required),
   but 200 on the read routes.
4. Legitimate admin/owner -> 200 on every route.
5. Invoice PDF: org_id must match both the caller's membership AND the
   invoice's actual owning org (double check).
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_idor_secret")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_stores():
    from app.ee.billing.invoice_store import (
        InMemoryInvoiceStore,
        set_invoice_store_for_tests,
    )
    from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests
    from app.ee.billing.wallet_store import (
        InMemoryWalletStore,
        set_wallet_store_for_tests,
    )
    from app.repos.provider import set_repo

    set_billing_store_for_tests(InMemoryBillingStore())
    set_wallet_store_for_tests(InMemoryWalletStore())
    set_invoice_store_for_tests(InMemoryInvoiceStore())
    yield
    set_billing_store_for_tests(None)
    set_wallet_store_for_tests(None)
    set_invoice_store_for_tests(None)
    set_repo(None)


def _seed_repo(*memberships: tuple[str, str, str]):
    """memberships: iterable of (org_id, user_id, role). Returns the repo."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    for org_id, user_id, role in memberships:
        repo.seed_org_member(org_id=org_id, user_id=user_id, role=role)
    set_repo(repo)
    return repo


def _app():
    from fastapi import FastAPI

    from app.ee.billing.routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def _client(app, user_id: str) -> AsyncClient:
    from app.auth.deps import current_user

    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "email": "attacker-or-legit@nubi.io",
    }
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# 1 & 3. Read routes — membership required, admin NOT required
# ---------------------------------------------------------------------------

READ_ROUTES = [
    "/ee/billing/tier",
    "/ee/billing/events",
    "/ee/billing/invoices",
    "/ee/billing/invoices/current-cycle",
    "/ee/billing/wallet",
]


class TestReadRoutesEnforceMembership:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", READ_ROUTES)
    async def test_non_member_gets_403(self, path: str) -> None:
        user_id = str(uuid.uuid4())
        my_org = str(uuid.uuid4())
        victim_org = str(uuid.uuid4())
        _seed_repo((my_org, user_id, "owner"))  # member of a DIFFERENT org only
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.get(f"{path}?org_id={victim_org}")
        assert resp.status_code == 403, f"{path} leaked cross-org access: {resp.text}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", READ_ROUTES)
    async def test_unauthenticated_org_id_alone_is_insufficient(self, path: str) -> None:
        """Simply knowing a valid-looking org_id (e.g. from a URL shared by a
        team-mate) must not grant access without real membership."""
        user_id = str(uuid.uuid4())
        random_org = str(uuid.uuid4())
        _seed_repo()  # caller is a member of NOTHING
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.get(f"{path}?org_id={random_org}")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", READ_ROUTES)
    async def test_plain_member_can_read_own_org(self, path: str) -> None:
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "member"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.get(f"{path}?org_id={org_id}")
        assert resp.status_code == 200, f"{path} wrongly denied a real member: {resp.text}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", READ_ROUTES)
    async def test_viewer_can_read_own_org(self, path: str) -> None:
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "viewer"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.get(f"{path}?org_id={org_id}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 2 & 3. Money-moving / config-mutating routes — admin/owner required
# ---------------------------------------------------------------------------


class TestCheckoutRequiresOrgAdmin:
    @pytest.mark.asyncio
    async def test_non_member_cannot_create_checkout_for_victim_org(self) -> None:
        user_id = str(uuid.uuid4())
        my_org = str(uuid.uuid4())
        victim_org = str(uuid.uuid4())
        _seed_repo((my_org, user_id, "owner"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.post(
                "/ee/billing/checkout",
                json={"org_id": victim_org, "tier": "pro"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_plain_member_cannot_create_checkout(self) -> None:
        """A non-admin member of the org must not be able to move money for it."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "member"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.post(
                "/ee/billing/checkout",
                json={"org_id": org_id, "tier": "pro"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_viewer_cannot_create_checkout(self) -> None:
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "viewer"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.post(
                "/ee/billing/checkout",
                json={"org_id": org_id, "tier": "pro"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_create_checkout_for_own_org(self) -> None:
        from app.ee.billing.paystack import PaystackClient, set_client_for_tests

        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "admin"))

        mock_client = AsyncMock(spec=PaystackClient)
        mock_client.post.return_value = {
            "status": True,
            "data": {
                "authorization_url": "https://checkout.paystack.com/xyz",
                "reference": "nubi-sub-test",
                "access_code": "abc",
            },
        }
        set_client_for_tests(mock_client)
        try:
            app = _app()
            async with _client(app, user_id) as client:
                resp = await client.post(
                    "/ee/billing/checkout",
                    json={"org_id": org_id, "tier": "pro"},
                )
            assert resp.status_code == 200, resp.text
            assert resp.json()["authorization_url"] == "https://checkout.paystack.com/xyz"
        finally:
            set_client_for_tests(None)

    @pytest.mark.asyncio
    async def test_owner_can_create_checkout_for_own_org(self) -> None:
        from app.ee.billing.paystack import PaystackClient, set_client_for_tests

        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "owner"))

        mock_client = AsyncMock(spec=PaystackClient)
        mock_client.post.return_value = {
            "status": True,
            "data": {"authorization_url": "https://x", "reference": "r", "access_code": "a"},
        }
        set_client_for_tests(mock_client)
        try:
            app = _app()
            async with _client(app, user_id) as client:
                resp = await client.post(
                    "/ee/billing/checkout",
                    json={"org_id": org_id, "tier": "pro"},
                )
            assert resp.status_code == 200
        finally:
            set_client_for_tests(None)


class TestWalletTopupRequiresOrgAdmin:
    @pytest.mark.asyncio
    async def test_non_member_cannot_topup_victim_wallet(self) -> None:
        user_id = str(uuid.uuid4())
        my_org = str(uuid.uuid4())
        victim_org = str(uuid.uuid4())
        _seed_repo((my_org, user_id, "owner"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.post(
                "/ee/billing/wallet/topup",
                json={"org_id": victim_org, "amount_usd_cents": 5000},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_plain_member_cannot_topup_wallet(self) -> None:
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "member"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.post(
                "/ee/billing/wallet/topup",
                json={"org_id": org_id, "amount_usd_cents": 5000},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_without_saved_card_gets_402_not_403(self) -> None:
        """Admin passes the authz gate; the *business* rule (no card saved)
        must surface as 402, proving authz ran before the card check."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "admin"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.post(
                "/ee/billing/wallet/topup",
                json={"org_id": org_id, "amount_usd_cents": 5000},
            )
        assert resp.status_code == 402

    @pytest.mark.asyncio
    async def test_admin_can_topup_own_wallet_with_saved_card(self) -> None:
        from app.ee.billing.paystack import PaystackClient, set_client_for_tests
        from app.ee.billing.wallet_store import get_wallet_store

        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "admin"))

        await get_wallet_store().upsert_topup_config(
            org_id,
            paystack_authorization_code="AUTH_abc123",
            paystack_customer_email="billing@victim-is-not-me.io",
            paystack_auth_reusable=True,
        )

        mock_client = AsyncMock(spec=PaystackClient)
        mock_client.post.return_value = {
            "status": True,
            "data": {"status": "success", "gateway_response": "Approved"},
        }
        set_client_for_tests(mock_client)
        try:
            app = _app()
            async with _client(app, user_id) as client:
                resp = await client.post(
                    "/ee/billing/wallet/topup",
                    json={"org_id": org_id, "amount_usd_cents": 5000},
                )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["topup_usd_cents"] == 5000

            balance = await get_wallet_store().get_balance(org_id)
            assert balance["balance_usd_cents"] == 5000
        finally:
            set_client_for_tests(None)

    @pytest.mark.asyncio
    async def test_negative_amount_rejected_with_422(self) -> None:
        """amount_usd_cents <= 0 must be rejected before any card charge is
        attempted (a negative amount could otherwise be used to try to
        reverse a charge / drain confusion). Card is pre-saved so the 402
        "no card" branch doesn't mask the amount-validation check."""
        from app.ee.billing.wallet_store import get_wallet_store

        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "admin"))
        await get_wallet_store().upsert_topup_config(
            org_id,
            paystack_authorization_code="AUTH_abc123",
            paystack_customer_email="billing@example.io",
            paystack_auth_reusable=True,
        )
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.post(
                "/ee/billing/wallet/topup",
                json={"org_id": org_id, "amount_usd_cents": -5000},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_zero_amount_rejected_with_422(self) -> None:
        from app.ee.billing.wallet_store import get_wallet_store

        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "admin"))
        await get_wallet_store().upsert_topup_config(
            org_id,
            paystack_authorization_code="AUTH_abc123",
            paystack_customer_email="billing@example.io",
            paystack_auth_reusable=True,
        )
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.post(
                "/ee/billing/wallet/topup",
                json={"org_id": org_id, "amount_usd_cents": 0},
            )
        assert resp.status_code == 422


class TestAutoTopupConfigRequiresOrgAdmin:
    @pytest.mark.asyncio
    async def test_non_member_cannot_configure_victim_autotopup(self) -> None:
        user_id = str(uuid.uuid4())
        my_org = str(uuid.uuid4())
        victim_org = str(uuid.uuid4())
        _seed_repo((my_org, user_id, "owner"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.put(
                "/ee/billing/wallet/autotopup",
                json={
                    "org_id": victim_org,
                    "auto_topup_enabled": True,
                    "threshold_usd_cents": 100,
                    "topup_amount_usd_cents": 100_000_000,
                },
            )
        assert resp.status_code == 403

        # Confirm the victim org's config was NOT mutated by the rejected call.
        from app.ee.billing.wallet_store import get_wallet_store

        cfg = await get_wallet_store().get_topup_config(victim_org)
        assert cfg["auto_topup_enabled"] is False

    @pytest.mark.asyncio
    async def test_plain_member_cannot_configure_autotopup(self) -> None:
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "member"))
        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.put(
                "/ee/billing/wallet/autotopup",
                json={"org_id": org_id, "auto_topup_enabled": True},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_configure_own_autotopup_and_secret_is_masked(self) -> None:
        from app.ee.billing.wallet_store import get_wallet_store

        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "admin"))
        await get_wallet_store().upsert_topup_config(
            org_id, paystack_authorization_code="AUTH_should_never_leave_server"
        )

        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.put(
                "/ee/billing/wallet/autotopup",
                json={
                    "org_id": org_id,
                    "auto_topup_enabled": True,
                    "threshold_usd_cents": 500,
                    "topup_amount_usd_cents": 2000,
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["auto_topup_enabled"] is True
        assert "paystack_authorization_code" not in body


# ---------------------------------------------------------------------------
# 5. Wallet GET response never leaks the raw Paystack authorization code
# ---------------------------------------------------------------------------


class TestWalletResponseMasksSecrets:
    @pytest.mark.asyncio
    async def test_get_wallet_never_returns_authorization_code(self) -> None:
        from app.ee.billing.wallet_store import get_wallet_store

        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "member"))
        await get_wallet_store().upsert_topup_config(
            org_id, paystack_authorization_code="AUTH_super_secret_card_token"
        )

        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.get(f"/ee/billing/wallet?org_id={org_id}")
        assert resp.status_code == 200
        body_text = resp.text
        assert "AUTH_super_secret_card_token" not in body_text
        assert "paystack_authorization_code" not in resp.json()["autotopup_config"]


# ---------------------------------------------------------------------------
# 6. Invoice PDF — membership AND invoice-ownership double check
# ---------------------------------------------------------------------------


class TestInvoicePdfCrossOrgAccess:
    async def _seed_invoice(self, org_id: str):
        from decimal import Decimal

        from app.ee.billing.invoice import (
            BusinessInfo,
            InvoiceLineItem,
            build_invoice,
        )
        from app.ee.billing.invoice_store import get_invoice_store
        from datetime import datetime, timedelta, timezone

        business = BusinessInfo(
            name="Nubi", legal_name="Nubi Pty Ltd", reg_number="123",
            vat_number="", vat_rate=Decimal("0.15"), address="",
            email="billing@nubi.io", website="https://nubi.io",
            currency="ZAR", invoice_number_prefix="NUBI",
        )
        now = datetime.now(timezone.utc)
        inv = build_invoice(
            org_id=org_id,
            tier="pro",
            period_start=now - timedelta(days=30),
            period_end=now,
            customer_email="victim@example.com",
            line_items=[InvoiceLineItem(description="Pro plan", amount_zar=Decimal("1499.00"), kind="subscription")],
            business=business,
            invoice_number="NUBI-2026-000001",
        )
        store = get_invoice_store()
        await store.save_invoice(inv)
        return inv

    @pytest.mark.asyncio
    async def test_non_member_cannot_fetch_victim_invoice_pdf(self) -> None:
        victim_org = str(uuid.uuid4())
        inv = await self._seed_invoice(victim_org)

        attacker_id = str(uuid.uuid4())
        attacker_org = str(uuid.uuid4())
        _seed_repo((attacker_org, attacker_id, "owner"))

        app = _app()
        async with _client(app, attacker_id) as client:
            # Attacker claims org_id=victim_org but is not a member of it.
            resp = await client.get(f"/ee/billing/invoices/{inv.id}/pdf?org_id={victim_org}")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_member_of_wrong_org_gets_404_even_if_invoice_id_guessed(self) -> None:
        """The attacker IS an admin of their own org and passes their own
        org_id (so authz passes) but supplies another org's invoice id --
        must be 404, not the victim's invoice."""
        victim_org = str(uuid.uuid4())
        inv = await self._seed_invoice(victim_org)

        attacker_id = str(uuid.uuid4())
        attacker_org = str(uuid.uuid4())
        _seed_repo((attacker_org, attacker_id, "owner"))

        app = _app()
        async with _client(app, attacker_id) as client:
            resp = await client.get(f"/ee/billing/invoices/{inv.id}/pdf?org_id={attacker_org}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_member_of_owning_org_can_fetch_pdf(self) -> None:
        org_id = str(uuid.uuid4())
        inv = await self._seed_invoice(org_id)

        user_id = str(uuid.uuid4())
        _seed_repo((org_id, user_id, "member"))

        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.get(f"/ee/billing/invoices/{inv.id}/pdf?org_id={org_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    @pytest.mark.asyncio
    async def test_events_list_never_returns_other_orgs_events(self) -> None:
        """Belt-and-braces: even if authz were somehow bypassed, the store
        layer itself must never blend another org's billing events in."""
        from app.ee.billing.store import get_billing_store

        org_a = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        store = get_billing_store()
        await store.record_billing_event(org_a, "charge.success", {"secret": "org-a-only"})
        await store.record_billing_event(org_b, "charge.success", {"secret": "org-b-only"})

        user_id = str(uuid.uuid4())
        _seed_repo((org_b, user_id, "member"))

        app = _app()
        async with _client(app, user_id) as client:
            resp = await client.get(f"/ee/billing/events?org_id={org_b}")
        assert resp.status_code == 200
        body = resp.json()
        assert all(
            e["payload"].get("secret") != "org-a-only" for e in body["events"]
        )
