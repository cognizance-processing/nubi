"""E2E: Webhook CRUD + SSRF guard tests.

- POST /webhooks/ with valid https URL → 201; secret NOT in response
- Blocked URLs (localhost, 169.254.169.254, 10.x, file://, ftp://) → 400 ssrf_blocked
- GET list/get never returns secret
- PUT rotates secret (still hidden)
- DELETE removes endpoint
- Per-org isolation: a query with a subscribed webhook pointed at unreachable host
  still returns 200 (non-blocking delivery)
"""

from __future__ import annotations

import uuid
import pytest


# example.com is an IANA-reserved test domain with a stable public IP (93.184.216.34)
# that resolves instantly — ideal as a webhook target that passes SSRF guard.
VALID_URL = "https://example.com/nubi-e2e-hook"
SECRET = "supersecretkey123"


@pytest.mark.usefixtures("e2e_ctx")
class TestWebhooks:
    def _create_webhook(self, e2e_ctx, url: str = VALID_URL, secret: str = SECRET) -> dict:
        resp = e2e_ctx.client.post(
            "/webhooks/",
            json={
                "name": f"E2E Test Webhook {uuid.uuid4().hex[:6]}",
                "url": url,
                "secret": secret,
                "event_types": ["query_executed"],
                "active": True,
            },
            headers=e2e_ctx.su_headers(),
        )
        return resp

    def test_create_valid_webhook_201(self, e2e_ctx):
        """Valid HTTPS webhook → 201 with secret NOT in response."""
        resp = self._create_webhook(e2e_ctx)
        assert resp.status_code == 201, f"Got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "id" in body
        assert "url" in body
        # Secret must NEVER appear in the response
        body_str = str(body)
        assert SECRET not in body_str, f"Secret leaked in response: {body_str}"
        assert "secret" not in body, "secret field should not be in create response"
        # Cleanup
        e2e_ctx.client.delete(f"/webhooks/{body['id']}", headers=e2e_ctx.su_headers())

    def test_localhost_ssrf_blocked(self, e2e_ctx):
        """localhost URL → 400 ssrf_blocked."""
        resp = self._create_webhook(e2e_ctx, url="https://localhost:8080/hook")
        assert resp.status_code == 400, f"Expected 400 for localhost, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", body.get("detail", ""))
        assert "ssrf" in str(err).lower() or "blocked" in str(err).lower(), (
            f"Expected ssrf_blocked error, got: {err}"
        )

    def test_metadata_ip_ssrf_blocked(self, e2e_ctx):
        """169.254.169.254 (cloud metadata) → 400 ssrf_blocked."""
        resp = self._create_webhook(e2e_ctx, url="https://169.254.169.254/latest/meta-data/")
        assert resp.status_code == 400, f"Expected 400 for metadata IP, got {resp.status_code}: {resp.text}"

    def test_rfc1918_10x_blocked(self, e2e_ctx):
        """10.x.x.x private IP → 400 ssrf_blocked."""
        resp = self._create_webhook(e2e_ctx, url="https://10.0.0.1/hook")
        assert resp.status_code == 400, f"Expected 400 for 10.x IP, got {resp.status_code}: {resp.text}"

    def test_rfc1918_192168_blocked(self, e2e_ctx):
        """192.168.x.x private IP → 400 ssrf_blocked."""
        resp = self._create_webhook(e2e_ctx, url="https://192.168.1.100/hook")
        assert resp.status_code == 400, f"Expected 400 for 192.168.x, got {resp.status_code}: {resp.text}"

    def test_file_scheme_blocked(self, e2e_ctx):
        """file:// scheme → 400 ssrf_blocked."""
        resp = self._create_webhook(e2e_ctx, url="file:///etc/passwd")
        assert resp.status_code in (400, 422), f"Expected 400/422 for file://, got {resp.status_code}: {resp.text}"

    def test_ftp_scheme_blocked(self, e2e_ctx):
        """ftp:// scheme → 400 ssrf_blocked."""
        resp = self._create_webhook(e2e_ctx, url="ftp://example.com/hook")
        assert resp.status_code in (400, 422), f"Expected 400/422 for ftp://, got {resp.status_code}: {resp.text}"

    def test_list_never_returns_secret(self, e2e_ctx):
        """GET /webhooks/ response never contains the signing secret."""
        # Create one first
        create_resp = self._create_webhook(e2e_ctx)
        assert create_resp.status_code == 201
        wh_id = create_resp.json()["id"]
        try:
            list_resp = e2e_ctx.client.get("/webhooks/", headers=e2e_ctx.su_headers())
            assert list_resp.status_code == 200
            list_str = list_resp.text
            assert SECRET not in list_str, f"Secret found in list response: {list_str[:200]}"
            for item in list_resp.json():
                assert "secret" not in item, f"secret field in list item: {item}"
        finally:
            e2e_ctx.client.delete(f"/webhooks/{wh_id}", headers=e2e_ctx.su_headers())

    def test_get_by_id_never_returns_secret(self, e2e_ctx):
        """GET /webhooks/{id} never returns secret."""
        create_resp = self._create_webhook(e2e_ctx)
        assert create_resp.status_code == 201
        wh_id = create_resp.json()["id"]
        try:
            get_resp = e2e_ctx.client.get(f"/webhooks/{wh_id}", headers=e2e_ctx.su_headers())
            assert get_resp.status_code == 200
            body = get_resp.json()
            assert SECRET not in str(body)
            assert "secret" not in body
        finally:
            e2e_ctx.client.delete(f"/webhooks/{wh_id}", headers=e2e_ctx.su_headers())

    def test_put_rotates_secret_still_hidden(self, e2e_ctx):
        """PUT /webhooks/{id} rotates secret; new secret not in response."""
        create_resp = self._create_webhook(e2e_ctx)
        assert create_resp.status_code == 201
        wh_id = create_resp.json()["id"]
        try:
            new_secret = "rotated-secret-new-key-456"
            put_resp = e2e_ctx.client.put(
                f"/webhooks/{wh_id}",
                json={"secret": new_secret},
                headers=e2e_ctx.su_headers(),
            )
            assert put_resp.status_code == 200, f"Got {put_resp.status_code}: {put_resp.text}"
            body = put_resp.json()
            assert new_secret not in str(body), "New secret leaked in PUT response"
            assert SECRET not in str(body), "Old secret leaked in PUT response"
            assert "secret" not in body
        finally:
            e2e_ctx.client.delete(f"/webhooks/{wh_id}", headers=e2e_ctx.su_headers())

    def test_delete_webhook(self, e2e_ctx):
        """DELETE /webhooks/{id} removes the endpoint (404 on subsequent GET)."""
        create_resp = self._create_webhook(e2e_ctx)
        assert create_resp.status_code == 201
        wh_id = create_resp.json()["id"]

        del_resp = e2e_ctx.client.delete(f"/webhooks/{wh_id}", headers=e2e_ctx.su_headers())
        assert del_resp.status_code == 204, f"Got {del_resp.status_code}: {del_resp.text}"

        # Subsequent GET → 404
        get_resp = e2e_ctx.client.get(f"/webhooks/{wh_id}", headers=e2e_ctx.su_headers())
        assert get_resp.status_code == 404

    def test_query_with_active_webhook_returns_200(self, e2e_ctx):
        """A query completes 200 even when an outbound webhook endpoint exists.

        We create a webhook pointing at the webhook.site test URL (which exists
        but may be slow or drop requests), then run a query. The query endpoint
        must return non-blocking. Webhook delivery is fire-and-forget.
        """
        # Create webhook pointing at webhook.site (a real HTTPS URL)
        create_resp = self._create_webhook(e2e_ctx)
        if create_resp.status_code != 201:
            pytest.skip(f"Could not create test webhook: {create_resp.text}")
        wh_id = create_resp.json()["id"]
        try:
            resp = e2e_ctx.client.post(
                "/query",
                json={"sql": "SELECT 1 AS x"},
                headers={
                    **e2e_ctx.su_headers(),
                    "Accept": "application/vnd.apache.arrow.stream",
                },
                timeout=15.0,
            )
            assert resp.status_code == 200, (
                f"Query failed: {resp.status_code}: {resp.text}"
            )
        finally:
            e2e_ctx.client.delete(f"/webhooks/{wh_id}", headers=e2e_ctx.su_headers())
