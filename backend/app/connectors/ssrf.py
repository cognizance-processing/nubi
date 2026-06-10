"""SSRF (Server-Side Request Forgery) guard for outbound connector fetches.

Several connectors fetch a user-supplied URL on the server (e.g. the
``http_json`` connector issues an ``httpx.get`` against ``config['url']``).
Without a host filter, an authenticated user could point such a connector at:

* the cloud metadata endpoint (``http://169.254.169.254/latest/meta-data/`` or
  ``http://[fd00:ec2::254]/``) to exfiltrate instance-role credentials;
* loopback / RFC1918 / link-local / unique-local addresses to reach internal
  services that are not meant to be exposed.

The query result is then streamed back to the caller, so this is a real read
primitive against the server's internal network.

``guard_url`` is the single reusable choke point.  Call it immediately before
any outbound request that targets a user-controlled URL.  It:

1. rejects non-``http(s)`` schemes;
2. resolves the hostname via ``socket.getaddrinfo`` and inspects **every**
   resolved address (defeating DNS-rebinding attacks that hide a private A
   record behind a public one);
3. raises ``AppError("ssrf_blocked", ..., 400)`` if any resolved address is a
   forbidden target.

Escape hatch
------------
Set the environment variable ``NUBI_SSRF_ALLOW_PRIVATE`` to a truthy value
(``1``/``true``/``yes``/``on``) for self-hosted / dev deployments that
legitimately talk to localhost or RFC1918 services (e.g. a sidecar MinIO).
This relaxes the private/loopback checks **but still blocks link-local and the
cloud metadata endpoints** — those are never a legitimate connector target and
are the highest-value SSRF prize.

Standard library only (``ipaddress``, ``socket``, ``urllib.parse``).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from app.errors import AppError

# Cloud metadata service addresses that must NEVER be reachable, even when the
# private-network escape hatch is enabled.
_METADATA_IPS: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure / OpenStack IMDS (IPv4)
        "fd00:ec2::254",    # AWS IMDS (IPv6)
    }
)

_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _allow_private() -> bool:
    """Return True if the private-network escape hatch is enabled via env."""
    return os.environ.get("NUBI_SSRF_ALLOW_PRIVATE", "").strip().lower() in _TRUTHY


def _is_metadata_address(ip: ipaddress._BaseAddress) -> bool:
    """Return True if *ip* is a known cloud-metadata address."""
    return str(ip) in _METADATA_IPS


def _is_link_local_or_metadata(ip: ipaddress._BaseAddress) -> bool:
    """Targets blocked unconditionally, even with the escape hatch enabled.

    Covers link-local ranges (IPv4 ``169.254.0.0/16``, IPv6 ``fe80::/10``) and
    the cloud metadata endpoints, which sit inside the IPv4 link-local block.
    """
    return ip.is_link_local or _is_metadata_address(ip)


def _is_private_target(ip: ipaddress._BaseAddress) -> bool:
    """Targets blocked unless the private-network escape hatch is enabled.

    Loopback (``127.0.0.0/8``, ``::1``), RFC1918 / unique-local private ranges
    (``10/8``, ``172.16/12``, ``192.168/16``, ``fc00::/7``), and the
    unspecified address (``0.0.0.0`` / ``::``).
    """
    return ip.is_loopback or ip.is_private or ip.is_unspecified


def _is_forbidden(ip: ipaddress._BaseAddress, *, allow_private: bool) -> bool:
    """Return True if *ip* is a forbidden SSRF target under the active policy."""
    # Map IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1) back to IPv4 so the
    # underlying address is classified correctly.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    # Always-blocked: link-local + metadata (the escape hatch never reopens these).
    if _is_link_local_or_metadata(ip):
        return True

    if allow_private:
        # Escape hatch: private/loopback are permitted; only the always-blocked
        # set above applies.
        return False

    return _is_private_target(ip)


def _resolve_addresses(host: str) -> list[ipaddress._BaseAddress]:
    """Resolve *host* to the set of IP addresses it maps to.

    Returns an empty list if the host cannot be resolved.  An unresolvable host
    is not an SSRF target (the downstream request will simply fail), so the
    caller treats "no addresses" as "nothing to block".

    Bare IP literals are returned directly without any network round-trip.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []

    addrs: list[ipaddress._BaseAddress] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        raw = sockaddr[0]
        # Strip an IPv6 scope/zone id if present (e.g. "fe80::1%en0").
        raw = raw.split("%", 1)[0]
        if raw in seen:
            continue
        seen.add(raw)
        try:
            addrs.append(ipaddress.ip_address(raw))
        except ValueError:
            # Should not happen for getaddrinfo output, but never let a
            # malformed address slip through unchecked.
            continue
    return addrs


def guard_url(url: str) -> None:
    """Validate *url* is safe to fetch server-side, or raise ``AppError``.

    Parameters
    ----------
    url:
        The user-supplied URL about to be fetched on the server.

    Raises
    ------
    app.errors.AppError
        ``code="ssrf_blocked"`` (400) if the scheme is not ``http``/``https``,
        the URL has no host, or any resolved address is a forbidden target
        (loopback, link-local, unique-local, RFC1918 private, ``0.0.0.0``, or a
        cloud-metadata IP).
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()

    if scheme not in ("http", "https"):
        raise AppError(
            "ssrf_blocked",
            f"URL scheme {scheme or '(none)'!r} is not allowed; only http(s) URLs may be fetched.",
            status=400,
        )

    host = parts.hostname
    if not host:
        raise AppError(
            "ssrf_blocked",
            "URL has no host component and cannot be fetched.",
            status=400,
        )

    allow_private = _allow_private()

    for ip in _resolve_addresses(host):
        if _is_forbidden(ip, allow_private=allow_private):
            raise AppError(
                "ssrf_blocked",
                f"Refusing to fetch {host!r}: it resolves to a blocked address "
                f"({ip}). Internal, loopback, link-local, and cloud-metadata "
                "targets are not permitted.",
                status=400,
            )


def guard_s3_endpoint(endpoint: str) -> None:
    """Validate an S3/object-storage endpoint host, or raise ``AppError``.

    Looser than :func:`guard_url`: a self-hosted MinIO / S3-compatible store
    legitimately lives on a private/loopback address, so private ranges are
    NOT blocked here. Only the always-forbidden set is rejected — cloud-metadata
    IPs (169.254.169.254 / fd00:ec2::254) and link-local — which are never a
    legitimate object-storage endpoint but ARE a classic httpfs SSRF target
    (point a connector at the IMDS to read cloud credentials).

    Accepts a scheme-stripped ``host`` or ``host:port`` (what DuckDB's
    ``ENDPOINT`` option takes); a bare empty string is a no-op.
    """
    if not endpoint:
        return
    # Strip any leading scheme and a trailing /path, then split off the port.
    host = endpoint.split("://", 1)[-1].split("/", 1)[0]
    # IPv6 literal in brackets: [::1]:9000 → ::1
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host

    for ip in _resolve_addresses(host):
        if _is_link_local_or_metadata(ip):
            raise AppError(
                "ssrf_blocked",
                f"Refusing to use object-storage endpoint {host!r}: it resolves "
                f"to a blocked address ({ip}). Link-local and cloud-metadata "
                "targets are never valid endpoints.",
                status=400,
            )
