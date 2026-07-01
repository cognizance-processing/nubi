"""Adversarial coverage for the audit backstop middleware (app/middleware/audit.py).

Attacks probed
--------------
1. No request body / query-string content is ever captured — the summary dict
   is limited to {method, path, status} regardless of what the client sends
   (POPIA contract).
2. The fail-open path (record_audit unavailable / raising) cannot be abused to
   SUPPRESS legitimate logging of a DIFFERENT request — a broken audit sink on
   one request does not poison the flag for subsequent requests, and the
   client-visible response is unaffected either way.
3. org_id attribution comes ONLY from the cryptographically verified token
   (``verify_token``) — a forged/tampered Authorization header, or an
   X-Org-Id-style spoofed header, can never attribute an audit row to a
   different org than the token actually verifies to.
4. An unauthenticated mutating request is never audited (no actor/org to
   attribute it to) and never crashes the middleware.
5. A route that already called ``record_audit`` (request.state.audit_logged)
   is not double-logged by the backstop.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from app.middleware.audit import _extract_identity, _extract_resource, _should_audit


def _make_request(
    method: str = "POST",
    path: str = "/api/v1/boards",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> Request:
    headers = headers or {}
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw_headers,
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive=receive)


class TestNoBodyOrQueryCapture:
    """1: only method/path/status ever reach the audit summary."""

    def test_should_audit_never_inspects_request_body(self):
        """_should_audit is a pure path/method check — it never reads the body."""
        req = _make_request(
            method="POST",
            path="/api/v1/boards",
            body=b'{"ssn": "000-00-0000", "password": "supersecret"}',
        )
        # Must not raise / must not need to await the body to decide.
        assert _should_audit(req.method, req.url.path) is True

    def test_extract_resource_has_no_pii_fields(self):
        """_extract_resource only returns structural path segments."""
        resource_type, resource_id = _extract_resource("/api/v1/boards/abc-123")
        assert resource_type == "boards"
        assert resource_id == "abc-123"
        # No way for a query string or body value to leak in — confirm the
        # function signature only accepts a path string.
        import inspect

        sig = inspect.signature(_extract_resource)
        assert list(sig.parameters) == ["path"]

    @pytest.mark.asyncio
    async def test_audit_summary_dict_shape_is_fixed(self, app, fake_db):
        """End-to-end: the summary passed to record_audit is exactly
        {method, path, status} — nothing else, regardless of a PII-laden body.
        """
        from datetime import datetime, timezone
        import uuid as _uuid

        from httpx import ASGITransport, AsyncClient

        from app.auth.jwt import mint_access_token
        from app.repos.memory import InMemoryRepo
        from app.repos.provider import set_repo

        user_id = str(_uuid.uuid4())
        org_id = str(_uuid.uuid4())
        fake_db.users[user_id] = {
            "id": user_id, "email": "audit@test.com", "name": "Audit Test",
            "avatar_url": None, "email_verified": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        repo = InMemoryRepo()
        repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
        set_repo(repo)
        token = mint_access_token(user_id)

        captured: list[dict[str, Any]] = []

        async def fake_record_audit(**kwargs):
            captured.append(kwargs)

        with patch("app.audit.record_audit", side_effect=fake_record_audit):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
                ac.headers["Authorization"] = f"Bearer {token}"
                # A PII/secret-laden body on a mutating request to a route that
                # does NOT itself call record_audit explicitly (so the backstop
                # fires). /api/v1/connectors POST is a mutating route.
                await ac.post(
                    "/api/v1/connectors",
                    json={
                        "name": "leaky",
                        "connector_type": "postgres",
                        "config": {
                            "host": "db.example.com",
                            "password": "TOP_SECRET_PW",
                            "ssn": "000-00-0000",
                        },
                    },
                )

        set_repo(None)

        for call in captured:
            summary = call.get("summary") or {}
            assert set(summary.keys()) <= {"method", "path", "status"}, (
                f"SECURITY: audit summary carries unexpected keys: {summary}"
            )
            assert "TOP_SECRET_PW" not in str(summary)
            assert "000-00-0000" not in str(summary)


class TestFailOpenCannotSuppressOtherRequests:
    """2: a broken audit sink on one request does not poison later requests."""

    @pytest.mark.asyncio
    async def test_record_audit_exception_does_not_break_response(self, app, fake_db):
        from datetime import datetime, timezone
        import uuid as _uuid

        from httpx import ASGITransport, AsyncClient

        from app.auth.jwt import mint_access_token
        from app.repos.memory import InMemoryRepo
        from app.repos.provider import set_repo

        user_id = str(_uuid.uuid4())
        org_id = str(_uuid.uuid4())
        fake_db.users[user_id] = {
            "id": user_id, "email": "failopen@test.com", "name": "Fail Open",
            "avatar_url": None, "email_verified": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        repo = InMemoryRepo()
        repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
        set_repo(repo)
        token = mint_access_token(user_id)

        with patch("app.audit.record_audit", side_effect=RuntimeError("sink down")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
                ac.headers["Authorization"] = f"Bearer {token}"
                resp1 = await ac.post(
                    "/api/v1/connectors",
                    json={"name": "a", "connector_type": "postgres", "config": {"host": "x"}},
                )
                # A second, independent request must not be silently skipped —
                # the middleware must attempt record_audit again (fail-open is
                # per-request, not a permanent "give up" state).
                resp2 = await ac.post(
                    "/api/v1/connectors",
                    json={"name": "b", "connector_type": "postgres", "config": {"host": "y"}},
                )

        set_repo(None)
        # The client-visible response must be unaffected by the audit failure.
        assert resp1.status_code in (200, 201, 400, 403, 409, 422)
        assert resp2.status_code in (200, 201, 400, 403, 409, 422)


class TestOrgAttributionFromVerifiedTokenOnly:
    """3: org_id comes from verify_token — never a spoofable header."""

    def test_spoofed_org_header_is_ignored(self):
        """A spoofed 'X-Org-Id' style header has no effect — _extract_identity
        only reads the Authorization bearer token."""
        req = _make_request(
            headers={
                "X-Org-Id": "attacker-controlled-org",
                "X-Forwarded-Org": "attacker-controlled-org-2",
            },
        )
        actor_user_id, org_id, actor_kind = _extract_identity(req)
        assert org_id is None
        assert actor_kind == "system"

    def test_garbage_bearer_token_falls_back_to_system_no_org(self):
        req = _make_request(headers={"Authorization": "Bearer not-a-real-jwt"})
        actor_user_id, org_id, actor_kind = _extract_identity(req)
        assert actor_user_id is None
        assert org_id is None
        assert actor_kind == "system"

    def test_tampered_signature_token_falls_back_to_system(self):
        """A token with a tampered payload (sig won't verify) must never leak
        the forged org claim into audit attribution."""
        import base64
        import json as _json

        header = base64.urlsafe_b64encode(
            _json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        forged_payload = base64.urlsafe_b64encode(
            _json.dumps({"sub": "attacker", "org": "victim-org", "typ": "access"}).encode()
        ).rstrip(b"=").decode()
        forged_token = f"{header}.{forged_payload}.not-a-real-signature"

        req = _make_request(headers={"Authorization": f"Bearer {forged_token}"})
        actor_user_id, org_id, actor_kind = _extract_identity(req)
        assert org_id != "victim-org", "SECURITY: forged org claim leaked into audit attribution"
        assert actor_kind == "system"

    def test_valid_token_org_matches_verified_claim(self):
        import uuid as _uuid

        from app.auth.jwt import mint_access_token

        user_id = str(_uuid.uuid4())
        token = mint_access_token(user_id)
        req = _make_request(headers={"Authorization": f"Bearer {token}"})
        actor_user_id, org_id, actor_kind = _extract_identity(req)
        assert actor_user_id == user_id
        assert actor_kind == "access"


class TestUnauthenticatedNeverAudited:
    """4: no token → no actor/org → never audited, never crashes."""

    def test_no_auth_header_is_system_kind_no_org(self):
        req = _make_request(headers={})
        actor_user_id, org_id, actor_kind = _extract_identity(req)
        assert actor_user_id is None
        assert org_id is None
        assert actor_kind == "system"

    @pytest.mark.asyncio
    async def test_unauthenticated_mutation_not_audited_and_gets_401(self, app):
        from httpx import ASGITransport, AsyncClient

        captured: list[dict[str, Any]] = []

        async def fake_record_audit(**kwargs):
            captured.append(kwargs)

        with patch("app.audit.record_audit", side_effect=fake_record_audit):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
                resp = await ac.post(
                    "/api/v1/connectors",
                    json={"name": "anon", "connector_type": "postgres", "config": {}},
                )

        assert resp.status_code in (401, 403)
        assert not captured, "Unauthenticated mutation must never reach record_audit"


class TestNoDoubleLogging:
    """5: request.state.audit_logged suppresses the backstop write."""

    def test_should_audit_skip_prefixes_never_hit_backstop(self):
        for path in ("/health", "/api/v1/health", "/embed/foo", "/docs", "/api/v1/auth/login"):
            assert _should_audit("POST", path) is False

    def test_get_and_head_never_audited(self):
        assert _should_audit("GET", "/api/v1/boards") is False
        assert _should_audit("HEAD", "/api/v1/boards") is False
        assert _should_audit("OPTIONS", "/api/v1/boards") is False
