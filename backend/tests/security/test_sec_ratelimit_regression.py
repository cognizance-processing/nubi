"""Regression guard for the FINDING-6B rate-limit fix (app/middleware/ratelimit.py).

``tests/test_ratelimit.py`` already has thorough end-to-end coverage of the 6B
fix (forged org claims, forged embed exemption, same-org sharing, cross-org
isolation). This file adds a SECOND, independent layer of regression-guard
directly against the internal identity-extraction functions — the module the
task calls out for re-confirmation — so a future refactor that keeps the HTTP
tests green but breaks the underlying verification logic (e.g. reading an
unverified header/claim) is still caught here.
"""

from __future__ import annotations

import base64
import json
import uuid

from app.middleware.ratelimit import (
    _extract_identity,
    _extract_verified_identity,
    _is_embed_exempt_path,
)


def _make_request(headers: dict[str, str] | None = None, client_host: str | None = "1.2.3.4"):
    from starlette.requests import Request

    headers = headers or {}
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/query",
        "headers": raw_headers,
        "query_string": b"",
        "client": (client_host, 12345) if client_host else None,
    }
    return Request(scope)


class TestForgedOrgClaimNeverMintsFreshBucket:
    def test_unsigned_alg_none_org_claim_falls_back_to_ip(self):
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(
            json.dumps({"sub": "attacker", "org": "victim-org", "typ": "access"}).encode()
        ).rstrip(b"=").decode()
        forged = f"{header}.{body}."

        req = _make_request(headers={"Authorization": f"Bearer {forged}"})
        key, is_embed = _extract_verified_identity(req)
        assert key is None, "SECURITY: alg:none forged org claim minted a fresh bucket key"
        assert is_embed is False

    def test_tampered_signature_org_claim_falls_back_to_ip(self):
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(
            json.dumps({"sub": "attacker", "org": "victim-org", "typ": "access"}).encode()
        ).rstrip(b"=").decode()
        forged = f"{header}.{body}.garbage-signature"

        req = _make_request(headers={"Authorization": f"Bearer {forged}"})
        key, is_embed = _extract_verified_identity(req)
        assert key is None
        assert is_embed is False

        identity = _extract_identity(req)
        assert identity == "ip:1.2.3.4", (
            f"SECURITY: forged org claim influenced the final identity key: {identity!r}"
        )

    def test_valid_token_org_key_is_derived_from_verified_claim(self):
        from app.auth.jwt import mint_access_token

        user_id = str(uuid.uuid4())
        token = mint_access_token(user_id, extra_claims={"org": "real-org-123"})
        req = _make_request(headers={"Authorization": f"Bearer {token}"})
        key, is_embed = _extract_verified_identity(req)
        assert key == "org:real-org-123"
        assert is_embed is False


class TestForgedEmbedNeverGetsExemption:
    def test_forged_embed_kind_via_unsigned_token_not_exempt(self):
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(
            json.dumps({"sub": "attacker", "kind": "embed", "org": "victim-org", "typ": "embed"}).encode()
        ).rstrip(b"=").decode()
        forged = f"{header}.{body}."

        req = _make_request(headers={"Authorization": f"Bearer {forged}"})
        key, is_embed = _extract_verified_identity(req)
        assert is_embed is False, "SECURITY: forged embed kind claimed the exemption"

    def test_exempt_path_matcher_is_scoped_to_the_documented_surface(self):
        assert _is_embed_exempt_path("/api/v1/query") is True
        assert _is_embed_exempt_path("/api/v1/query/registry") is True
        assert _is_embed_exempt_path("/api/v1/metrics/retail_nsv/query") is True
        assert _is_embed_exempt_path("/api/v1/metrics/retail_nsv/sql") is True
        # NOT exempt: writes / auth / arbitrary other metrics sub-paths.
        assert _is_embed_exempt_path("/api/v1/metrics") is False
        assert _is_embed_exempt_path("/api/v1/metrics/retail_nsv") is False
        assert _is_embed_exempt_path("/api/v1/metrics/retail_nsv/revert/1") is False
        # /explain was removed (model-explainability feature retired) — never exempt.
        assert _is_embed_exempt_path("/api/v1/metrics/retail_nsv/explain") is False
        assert _is_embed_exempt_path("/api/v1/connectors") is False
        assert _is_embed_exempt_path("/api/v1/auth/login") is False


class TestNoPeerFallsBackToSharedBucketNeverSkipped:
    def test_no_client_and_no_xff_uses_unknown_bucket(self):
        req = _make_request(headers={}, client_host=None)
        identity = _extract_identity(req)
        assert identity == "unknown"

    def test_leftmost_xff_never_used_only_rightmost(self):
        req = _make_request(
            headers={"X-Forwarded-For": "attacker-spoofed-1, attacker-spoofed-2, 9.9.9.9"},
            client_host=None,
        )
        identity = _extract_identity(req)
        assert identity == "ip:9.9.9.9"
        assert "attacker-spoofed" not in identity
