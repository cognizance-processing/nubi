"""Tests for the SSRF guard (SECURITY FIX B4).

Coverage
--------
- guard_url BLOCKS the cloud-metadata IP (169.254.169.254).
- guard_url BLOCKS loopback hostnames/IPs (http://localhost).
- guard_url BLOCKS RFC1918 private literals (10.0.0.5, 192.168.1.1).
- guard_url BLOCKS a hostname that *resolves* to a private IP (DNS-rebinding
  defence — monkeypatch socket.getaddrinfo).
- guard_url BLOCKS non-http(s) schemes (file://, ftp://, gopher://).
- guard_url ALLOWS a normal public host (monkeypatch getaddrinfo -> public IP).
- NUBI_SSRF_ALLOW_PRIVATE=1 PERMITS localhost but STILL BLOCKS 169.254.169.254.
- DNS-rebinding with a mix of public + private addresses is blocked (every
  resolved address is inspected).
- The http_json connector's execute() path invokes the guard before fetching
  (mock httpx so no real request is made).

No real network calls are made anywhere — socket.getaddrinfo is monkeypatched
for hostnames; IP literals resolve locally without a round-trip.
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.ssrf import guard_url
from app.errors import AppError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _getaddrinfo_returning(*ips: str):
    """Return a fake socket.getaddrinfo that resolves any host to *ips*.

    Each entry mimics the 5-tuple shape getaddrinfo returns:
    ``(family, type, proto, canonname, sockaddr)`` where ``sockaddr[0]`` is the
    numeric address.
    """

    def _fake(host, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        infos = []
        for ip in ips:
            if ":" in ip:
                family = socket.AF_INET6
                sockaddr = (ip, 0, 0, 0)
            else:
                family = socket.AF_INET
                sockaddr = (ip, 0)
            infos.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return infos

    return _fake


def _getaddrinfo_raising(*_args, **_kwargs):  # noqa: ANN002, ANN003
    """A fake getaddrinfo that always fails to resolve (NXDOMAIN-like)."""
    raise socket.gaierror("Name or service not known")


# ---------------------------------------------------------------------------
# BLOCK: literals that resolve without DNS
# ---------------------------------------------------------------------------


class TestGuardBlocksLiterals:
    def test_blocks_metadata_ip(self) -> None:
        """The cloud-metadata IP is the highest-value SSRF target -> blocked."""
        with pytest.raises(AppError) as exc_info:
            guard_url("http://169.254.169.254/latest/meta-data/")
        assert exc_info.value.code == "ssrf_blocked"
        assert exc_info.value.status == 400

    def test_blocks_metadata_ipv6(self) -> None:
        """The AWS IPv6 metadata address is blocked too."""
        with pytest.raises(AppError) as exc_info:
            guard_url("http://[fd00:ec2::254]/latest/meta-data/")
        assert exc_info.value.code == "ssrf_blocked"

    def test_blocks_localhost(self) -> None:
        """http://localhost resolves to loopback -> blocked."""
        with pytest.raises(AppError) as exc_info:
            guard_url("http://localhost/admin")
        assert exc_info.value.code == "ssrf_blocked"
        assert exc_info.value.status == 400

    def test_blocks_loopback_literal(self) -> None:
        with pytest.raises(AppError) as exc_info:
            guard_url("http://127.0.0.1:8080/")
        assert exc_info.value.code == "ssrf_blocked"

    def test_blocks_rfc1918_10_8(self) -> None:
        with pytest.raises(AppError) as exc_info:
            guard_url("http://10.0.0.5/")
        assert exc_info.value.code == "ssrf_blocked"

    def test_blocks_rfc1918_192_168(self) -> None:
        with pytest.raises(AppError) as exc_info:
            guard_url("http://192.168.1.1/")
        assert exc_info.value.code == "ssrf_blocked"

    def test_blocks_rfc1918_172_16(self) -> None:
        with pytest.raises(AppError) as exc_info:
            guard_url("http://172.16.5.5/")
        assert exc_info.value.code == "ssrf_blocked"

    def test_blocks_unspecified_0_0_0_0(self) -> None:
        with pytest.raises(AppError) as exc_info:
            guard_url("http://0.0.0.0/")
        assert exc_info.value.code == "ssrf_blocked"


# ---------------------------------------------------------------------------
# BLOCK: schemes
# ---------------------------------------------------------------------------


class TestGuardBlocksSchemes:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com/_x",
            "ws://example.com/socket",
            "example.com/no-scheme",  # urlsplit -> empty scheme
        ],
    )
    def test_blocks_non_http_scheme(self, url: str) -> None:
        with pytest.raises(AppError) as exc_info:
            guard_url(url)
        assert exc_info.value.code == "ssrf_blocked"
        assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# BLOCK: hostname that resolves to a private IP (DNS rebinding)
# ---------------------------------------------------------------------------


class TestGuardBlocksResolvedPrivate:
    def test_hostname_resolving_to_private_ip_is_blocked(self) -> None:
        """A public-looking hostname that resolves to a private IP -> blocked."""
        fake = _getaddrinfo_returning("10.1.2.3")
        with patch.object(socket, "getaddrinfo", fake):
            with pytest.raises(AppError) as exc_info:
                guard_url("http://evil.example.com/")
        assert exc_info.value.code == "ssrf_blocked"

    def test_hostname_resolving_to_metadata_is_blocked(self) -> None:
        """DNS rebinding to the metadata IP is blocked."""
        fake = _getaddrinfo_returning("169.254.169.254")
        with patch.object(socket, "getaddrinfo", fake):
            with pytest.raises(AppError) as exc_info:
                guard_url("http://rebind.example.com/")
        assert exc_info.value.code == "ssrf_blocked"

    def test_mixed_public_and_private_records_blocked(self) -> None:
        """Every resolved address is checked: a public A-record cannot mask a
        private one (the core DNS-rebinding defence)."""
        fake = _getaddrinfo_returning("93.184.216.34", "127.0.0.1")
        with patch.object(socket, "getaddrinfo", fake):
            with pytest.raises(AppError) as exc_info:
                guard_url("http://sneaky.example.com/")
        assert exc_info.value.code == "ssrf_blocked"


# ---------------------------------------------------------------------------
# ALLOW: public hosts
# ---------------------------------------------------------------------------


class TestGuardAllowsPublic:
    def test_public_host_allowed(self) -> None:
        """A hostname resolving to a public IP is allowed (no raise)."""
        fake = _getaddrinfo_returning("93.184.216.34")  # example.com
        with patch.object(socket, "getaddrinfo", fake):
            guard_url("https://api.example.com/v1/records")  # must not raise

    def test_public_ipv6_allowed(self) -> None:
        fake = _getaddrinfo_returning("2606:2800:220:1:248:1893:25c8:1946")
        with patch.object(socket, "getaddrinfo", fake):
            guard_url("https://api.example.com/")  # must not raise

    def test_unresolvable_host_is_not_blocked(self) -> None:
        """An unresolvable host is not an SSRF target; guard fails open so the
        downstream fetch can surface the real DNS error."""
        with patch.object(socket, "getaddrinfo", _getaddrinfo_raising):
            guard_url("http://does-not-resolve.invalid/")  # must not raise


# ---------------------------------------------------------------------------
# Escape hatch: NUBI_SSRF_ALLOW_PRIVATE
# ---------------------------------------------------------------------------


class TestEscapeHatch:
    def test_allow_private_permits_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With NUBI_SSRF_ALLOW_PRIVATE=1, localhost is permitted."""
        monkeypatch.setenv("NUBI_SSRF_ALLOW_PRIVATE", "1")
        guard_url("http://localhost:9000/minio")  # must not raise
        guard_url("http://10.0.0.5/internal")  # private also permitted

    def test_allow_private_still_blocks_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even with the escape hatch on, the metadata IP stays blocked."""
        monkeypatch.setenv("NUBI_SSRF_ALLOW_PRIVATE", "1")
        with pytest.raises(AppError) as exc_info:
            guard_url("http://169.254.169.254/latest/meta-data/")
        assert exc_info.value.code == "ssrf_blocked"

    def test_allow_private_still_blocks_link_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Link-local addresses stay blocked even with the escape hatch on."""
        monkeypatch.setenv("NUBI_SSRF_ALLOW_PRIVATE", "1")
        with pytest.raises(AppError) as exc_info:
            guard_url("http://169.254.10.10/")
        assert exc_info.value.code == "ssrf_blocked"

    def test_disabled_by_default_blocks_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without the env var, localhost is blocked (default-secure)."""
        monkeypatch.delenv("NUBI_SSRF_ALLOW_PRIVATE", raising=False)
        with pytest.raises(AppError) as exc_info:
            guard_url("http://localhost/")
        assert exc_info.value.code == "ssrf_blocked"

    @pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off"])
    def test_falsy_values_do_not_enable_escape_hatch(
        self, monkeypatch: pytest.MonkeyPatch, falsy: str
    ) -> None:
        monkeypatch.setenv("NUBI_SSRF_ALLOW_PRIVATE", falsy)
        with pytest.raises(AppError):
            guard_url("http://localhost/")


# ---------------------------------------------------------------------------
# Integration: http_json connector invokes the guard before fetching
# ---------------------------------------------------------------------------


class TestHttpJsonInvokesGuard:
    def test_execute_calls_guard_before_fetch(self) -> None:
        """The connector's execute() path calls guard_url before httpx.get.

        We point the connector at the metadata IP and assert it raises
        ssrf_blocked and that httpx.get is NEVER called (no real request).
        """
        from app.connectors.http_json import HttpJsonConnector
        from app.connectors.plan import PhysicalPlan

        conn = HttpJsonConnector({"url": "http://169.254.169.254/latest/meta-data/"})
        plan = PhysicalPlan(
            dialect="duckdb",
            sql="SELECT 1",
            params=[],
            projection=None,
            predicates=[],
            rls_claims={},
            cache_key="cafebabe" * 8,
        )

        with patch("httpx.get") as mock_get:
            with pytest.raises(AppError) as exc_info:
                conn.execute(plan)

        assert exc_info.value.code == "ssrf_blocked"
        assert mock_get.call_count == 0, "guard must short-circuit before any fetch"

    def test_execute_invokes_guard_url_function(self) -> None:
        """Directly assert guard_url is invoked with the connector's URL."""
        from app.connectors.http_json import HttpJsonConnector
        from app.connectors.plan import PhysicalPlan

        url = "https://api.example.com/records"
        conn = HttpJsonConnector({"url": url})
        plan = PhysicalPlan(
            dialect="duckdb",
            sql="SELECT 1",
            params=[],
            projection=None,
            predicates=[],
            rls_claims={},
            cache_key="cafebabe" * 8,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch("app.connectors.http_json.guard_url") as mock_guard, patch(
            "httpx.get", return_value=mock_response
        ):
            conn.execute(plan)

        mock_guard.assert_called_once_with(url)


# ---------------------------------------------------------------------------
# guard_s3_endpoint (B4-httpfs) — block metadata/link-local, ALLOW private/MinIO
# ---------------------------------------------------------------------------

import pytest as _pytest
from app.connectors.ssrf import guard_s3_endpoint
from app.errors import AppError as _AppError


def test_s3_endpoint_blocks_metadata_ip():
    with _pytest.raises(_AppError) as ei:
        guard_s3_endpoint("169.254.169.254")
    assert ei.value.code == "ssrf_blocked"


def test_s3_endpoint_blocks_metadata_with_port_and_scheme():
    for ep in ("http://169.254.169.254:80", "169.254.169.254:9000"):
        with _pytest.raises(_AppError):
            guard_s3_endpoint(ep)


def test_s3_endpoint_allows_private_minio():
    # MinIO/self-host legitimately runs on loopback/RFC1918 — must NOT block.
    guard_s3_endpoint("127.0.0.1:9000")
    guard_s3_endpoint("10.0.0.5:9000")
    guard_s3_endpoint("192.168.1.10:9000")


def test_s3_endpoint_allows_public_host_and_empty():
    guard_s3_endpoint("")                      # no-op
    guard_s3_endpoint("s3.amazonaws.com")      # public host, allowed


def test_s3_endpoint_blocks_link_local_ipv6():
    with _pytest.raises(_AppError):
        guard_s3_endpoint("[fe80::1]:9000")
