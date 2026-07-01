"""Adversarial coverage for the http_call flow task (app/flows/handlers/http_call.py).

``tests/test_flow_http_call_assert.py`` already covers the functional shape
(2xx/5xx/timeout, missing url/method, allowlist happy-path, body/headers). This
file targets the specific attacker angles this security wave calls out and
does NOT duplicate the functional suite:

1. DNS-rebinding — a hostname with MULTIPLE resolved addresses, one public and
   one private/metadata, is blocked (``resolve_and_pin`` must reject if ANY
   resolved address is forbidden, not just the first).
2. The org allowlist (``http_call_allowed_hosts``) cannot be used to widen
   access to a host that resolves to a forbidden address — the SSRF guard is
   evaluated BEFORE the allowlist and always wins.
3. file:// and other non-http(s) schemes are blocked even when present in the
   allowlist.
4. The auth secret value never appears in the run's returned dict (result) —
   covers header/basic auth kinds (bearer is already covered functionally).
5. A non-2xx response fails the run (RuntimeError) even when the target host
   IS allow-listed (allowlisting doesn't change the "must be 2xx" contract).
6. http_call cannot be pointed at Nubi's own loopback/private admin surface —
   same SSRF guard, exercised with a realistic "internal API" URL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.errors import AppError
from app.flows.handlers.http_call import handle as http_handle


def _ctx(secrets: dict[str, str] | None = None, flow: dict[str, Any] | None = None):
    from app.flows.executor import TaskContext

    ctx = TaskContext(
        flow_params={},
        inputs={},
        now=datetime(2025, 1, 1, tzinfo=timezone.utc),
        org_id="org1",
    )
    ctx.secrets = secrets or {}
    ctx.flow = flow
    return ctx


_CLAIMS: dict[str, Any] = {"org_id": "org1"}


class TestDNSRebindMultiAddress:
    """1: resolve_and_pin must block if ANY resolved address is forbidden."""

    def test_second_address_is_metadata_ip_blocks(self):
        """A host with [public_ip, metadata_ip] must be blocked (not just checked
        against the first resolved address)."""
        import ipaddress

        with patch(
            "app.connectors.ssrf._resolve_addresses",
            return_value=[
                ipaddress.ip_address("93.184.216.34"),  # public — looks safe
                ipaddress.ip_address("169.254.169.254"),  # metadata — must block
            ],
        ):
            config = {"url": "http://rebind.example.com/hook"}
            with pytest.raises(AppError) as exc_info:
                http_handle(config, _ctx(), _CLAIMS)
        assert exc_info.value.code == "ssrf_blocked"

    def test_second_address_is_private_blocks(self):
        import ipaddress

        with patch(
            "app.connectors.ssrf._resolve_addresses",
            return_value=[
                ipaddress.ip_address("93.184.216.34"),
                ipaddress.ip_address("10.0.0.5"),
            ],
        ):
            config = {"url": "http://rebind2.example.com/hook"}
            with pytest.raises(AppError) as exc_info:
                http_handle(config, _ctx(), _CLAIMS)
        assert exc_info.value.code == "ssrf_blocked"


class TestAllowlistCannotBypassSSRF:
    """2-3: the org allowlist is evaluated AFTER the SSRF guard, never before."""

    def test_allowlisted_host_that_resolves_to_metadata_ip_still_blocked(self):
        """Even if the org explicitly allowlists the hostname, a host that
        resolves to the cloud-metadata IP must still be blocked by the SSRF
        guard — the allowlist can only narrow, never widen, past SSRF."""
        import ipaddress

        flow = {"runtime_config": {"http_call_allowed_hosts": ["evil-metadata.example.com"]}}
        config = {"url": "http://evil-metadata.example.com/hook"}
        with patch(
            "app.connectors.ssrf._resolve_addresses",
            return_value=[ipaddress.ip_address("169.254.169.254")],
        ):
            with pytest.raises(AppError) as exc_info:
                http_handle(config, _ctx(flow=flow), _CLAIMS)
        assert exc_info.value.code == "ssrf_blocked"

    def test_allowlisted_localhost_still_blocked(self):
        flow = {"runtime_config": {"http_call_allowed_hosts": ["localhost"]}}
        config = {"url": "http://localhost:9999/internal-admin"}
        with pytest.raises(AppError) as exc_info:
            http_handle(config, _ctx(flow=flow), _CLAIMS)
        assert exc_info.value.code == "ssrf_blocked"

    def test_file_scheme_blocked_even_if_host_allowlisted(self):
        flow = {"runtime_config": {"http_call_allowed_hosts": [""]}}
        config = {"url": "file:///etc/passwd"}
        with pytest.raises(AppError) as exc_info:
            http_handle(config, _ctx(flow=flow), _CLAIMS)
        assert exc_info.value.code == "ssrf_blocked"


class TestNubiInternalSurfaceNotReachable:
    """6: http_call cannot reach Nubi's own loopback/private admin surface."""

    @pytest.mark.parametrize(
        "internal_url",
        [
            "http://127.0.0.1:8000/api/v1/internal/admin",
            "http://localhost:8000/openapi.json",
            "http://[::1]:8000/api/v1/jobs",
            "http://10.0.0.10:8000/api/v1/query",  # typical internal LB address
        ],
    )
    def test_internal_nubi_style_url_blocked(self, internal_url):
        config = {"url": internal_url}
        with pytest.raises(AppError) as exc_info:
            http_handle(config, _ctx(), _CLAIMS)
        assert exc_info.value.code == "ssrf_blocked"


class TestAuthSecretNeverLeaksHeaderAndBasic:
    """4: header/basic auth kinds — secret value absent from the run result."""

    def _run_with_auth(self, auth_cfg: dict[str, Any], secret_val: str) -> dict[str, Any]:
        config = {
            "url": "https://example.com/hook",
            "auth": auth_cfg,
        }
        ctx = _ctx(secrets={"TOK": secret_val})

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="1.2.3.4", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            result = http_handle(config, ctx, _CLAIMS)
        return result

    def test_header_auth_secret_not_in_result(self):
        secret_val = "header-secret-value-xyz"
        result = self._run_with_auth(
            {"kind": "header", "secret_name": "TOK", "header_name": "X-Api-Key"},
            secret_val,
        )
        import json as _json

        assert secret_val not in _json.dumps(result)

    def test_basic_auth_secret_not_in_result(self):
        secret_val = "basic-secret-value-xyz"
        result = self._run_with_auth(
            {"kind": "basic", "secret_name": "TOK", "username": "svc"},
            secret_val,
        )
        import json as _json

        assert secret_val not in _json.dumps(result)
        # The base64-encoded form must not leak either.
        import base64

        encoded = base64.b64encode(f"svc:{secret_val}".encode()).decode()
        assert encoded not in _json.dumps(result)


class TestNon2xxFailsRunEvenWhenAllowlisted:
    """5: allowlisting a host does not relax the "must be 2xx" contract."""

    def test_allowlisted_host_5xx_still_raises(self):
        flow = {"runtime_config": {"http_call_allowed_hosts": ["example.com"]}}
        config = {"url": "https://example.com/hook"}
        ctx = _ctx(flow=flow)

        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.read.return_value = b"Service Unavailable"
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="1.2.3.4", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            with pytest.raises(RuntimeError, match="503"):
                http_handle(config, ctx, _CLAIMS)
