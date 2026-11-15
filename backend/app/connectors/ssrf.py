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
import re
import socket
from typing import Any
from urllib.parse import parse_qsl, urlsplit

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

    Notes
    -----
    This performs validation only.  Because DNS is re-resolved by the HTTP
    client at request time, ``guard_url`` alone is vulnerable to a TOCTOU /
    DNS-rebinding race (the host can pass the check then rebind to an internal
    address before the socket connects).  For the actual outbound fetch, use
    :func:`resolve_and_pin` instead, which closes that window by resolving the
    host **once**, validating every address, and pinning the connection to a
    checked IP literal.  ``guard_url`` remains useful for cheap create-time /
    pre-flight validation where no request follows immediately.

    Unlike :func:`resolve_and_pin`, ``guard_url`` **fails open** on an
    unresolvable host: a host that cannot be resolved is not an SSRF target (the
    downstream request will simply fail), so the caller can surface the real DNS
    error rather than a spurious block.
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


class PinnedTarget:
    """A validated outbound target whose connection IP has been pinned.

    Carries everything an HTTP client needs to issue a request that cannot be
    DNS-rebound after the SSRF check:

    * ``url`` — the original, unmodified URL (host = original hostname). Used for
      TLS certificate verification / SNI so the cert still validates against the
      hostname the user configured.
    * ``ip`` — a string IP literal that was resolved from the host and passed
      the forbidden-address policy. The HTTP client must connect to *this*
      address rather than re-resolving the hostname.
    * ``host`` — the original hostname (for the ``Host`` request header / SNI).
    * ``port`` — the effective TCP port (explicit, or scheme default).
    """

    __slots__ = ("url", "ip", "host", "port")

    def __init__(self, url: str, ip: str, host: str, port: int) -> None:
        self.url = url
        self.ip = ip
        self.host = host
        self.port = port


def resolve_and_pin(url: str) -> PinnedTarget:
    """Resolve *url* once, validate every address, and return a pinned target.

    This is the TOCTOU-safe entry point for outbound connector fetches. It
    resolves the hostname a **single** time, rejects the request if *any*
    resolved address is a forbidden SSRF target (defeating DNS rebinding where a
    public A-record masks a private one), and returns a :class:`PinnedTarget`
    whose ``ip`` the caller must connect to directly. Because the caller pins
    the connection to this checked IP — rather than letting the HTTP client
    re-resolve the hostname at socket-connect time — an attacker domain with a
    zero-TTL record cannot rebind to ``169.254.169.254`` / RFC1918 / loopback
    between the check and the request.

    Parameters
    ----------
    url:
        The user-supplied URL about to be fetched on the server.

    Returns
    -------
    PinnedTarget
        The validated, pinned connection target.

    Raises
    ------
    app.errors.AppError
        ``code="ssrf_blocked"`` (400) if the scheme is not ``http``/``https``,
        the URL has no host, the host does not resolve to any address, or any
        resolved address is a forbidden target (loopback, link-local,
        unique-local, RFC1918 private, ``0.0.0.0``, or a cloud-metadata IP).
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

    # Resolve the host EXACTLY ONCE. Every subsequent decision (validation and
    # the pinned connection) is made against this same resolution, so there is
    # no second lookup that an attacker could rebind.
    addrs = _resolve_addresses(host)

    safe_ip: str | None = None
    for ip in addrs:
        if _is_forbidden(ip, allow_private=allow_private):
            raise AppError(
                "ssrf_blocked",
                f"Refusing to fetch {host!r}: it resolves to a blocked address "
                f"({ip}). Internal, loopback, link-local, and cloud-metadata "
                "targets are not permitted.",
                status=400,
            )
        if safe_ip is None:
            # Pin the first validated address. Prefer the original-form literal
            # (the mapped IPv4 form is only used for classification, never for
            # the actual connection).
            safe_ip = str(ip)

    if safe_ip is None:
        # No address resolved. Unlike guard_url's historical fail-open, a fetch
        # we are about to pin MUST have a concrete, checked IP to connect to —
        # there is nothing safe to pin, so refuse.
        raise AppError(
            "ssrf_blocked",
            f"Refusing to fetch {host!r}: the host does not resolve to any "
            "address, so no safe connection target could be pinned.",
            status=400,
        )

    port = parts.port if parts.port is not None else (443 if scheme == "https" else 80)

    return PinnedTarget(url=url, ip=safe_ip, host=host, port=port)


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


# ---------------------------------------------------------------------------
# Connector targets (database hosts, DSNs, tenant-supplied endpoints)
# ---------------------------------------------------------------------------

#: Config keys that may name a host directly.
_HOST_KEYS = ("host", "hostname", "server", "hostaddr")
#: Config keys that carry a URL / DSN whose host must be checked too.
_URL_KEYS = ("dsn", "url", "base_url", "connection_string", "jdbc_url", "uri")
#: Connection-string parameters a database driver takes its target from.
_TARGET_PARAMS = ("host", "hostaddr")
#: Schemes whose driver falls back to a local socket when no host is given.
_SOCKET_FALLBACK_SCHEMES = ("postgres", "postgresql", "redshift", "cockroachdb", "mysql")

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62})(?:\.[A-Za-z0-9_-]{1,63})*\.?$"
)
_KEYWORD_DSN_RE = re.compile(r"^\s*[A-Za-z_]+\s*=")
_KEYWORD_PAIR_RE = re.compile(r"([A-Za-z_]+)\s*=\s*('(?:[^'\\]|\\.)*'|\S*)")


def _not_allowed() -> AppError:
    # Deliberately generic: no resolved address, no hint about what answered.
    return AppError(
        "connector_target_not_allowed",
        "This connection target is not allowed.",
        status=400,
    )


def _checked_addresses(host: str) -> list[ipaddress._BaseAddress]:
    """Resolve *host* once and return its addresses; refuse if any is forbidden."""
    host = host.strip()
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # Anything that is not a plain host name is refused rather than left
        # for the driver to interpret in its own way.
        if not _HOSTNAME_RE.match(host):
            raise _not_allowed() from None
    allow_private = _allow_private()
    addrs = _resolve_addresses(host)
    for ip in addrs:
        if _is_forbidden(ip, allow_private=allow_private):
            raise _not_allowed()
    return addrs


def _check_one_host(host: str) -> None:
    _checked_addresses(host)


def _check_host(host: Any) -> None:
    """Check a host value; a comma-separated list is checked entry by entry."""
    if host is None or host == "":
        return
    if not isinstance(host, str):
        raise _not_allowed()
    for part in host.split(","):
        _check_one_host(part)


def _netloc_hosts(netloc: str) -> list[str]:
    hosts: list[str] = []
    for entry in netloc.rsplit("@", 1)[-1].split(","):
        entry = entry.strip()
        if entry.startswith("["):
            entry = entry.split("]", 1)[0] + "]"
        elif ":" in entry:
            entry = entry.rsplit(":", 1)[0]
        hosts.append(entry)
    return hosts


def _hosts_of(value: Any) -> list[str]:
    """Every host a URL or connection string names: the authority (each entry
    of a multi-host list), plus ``host`` / ``hostaddr`` parameters, in either
    URL or ``key=value`` form.  Raises when the string names no host but its
    driver would then use a local socket."""
    if not isinstance(value, str) or not value.strip():
        return []
    text = value.strip()
    if text.lower().startswith("jdbc:"):
        text = text[5:]
    if "://" not in text and _KEYWORD_DSN_RE.match(text):
        found = [
            raw[1:-1] if raw.startswith("'") else raw
            for key, raw in _KEYWORD_PAIR_RE.findall(text)
            if key.lower() in _TARGET_PARAMS
        ]
        if not any(found):
            raise _not_allowed()
        return found
    bare = "://" not in text
    if bare:
        text = "x://" + text
    try:
        parts = urlsplit(text)
        params = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        raise _not_allowed() from None
    hosts = [h for h in _netloc_hosts(parts.netloc) if h]
    hosts += [v for k, v in params if k.lower() in _TARGET_PARAMS]
    if not hosts and not bare and parts.scheme.lower().split("+", 1)[0] in _SOCKET_FALLBACK_SCHEMES:
        raise _not_allowed()
    return hosts


def _host_of(value: Any) -> str | None:
    hosts = _hosts_of(value)
    return ",".join(hosts) if hosts else None


def guard_connector_target(config: dict[str, Any]) -> None:
    """Refuse a connector config whose host or DSN points at the platform's
    own network (loopback, private ranges, link-local, cloud metadata).

    Applied to every DIRECT connection before a connector is built — the
    platform dials these targets itself, so a tenant-supplied host is an
    outbound request made on the tenant's behalf.  Bridge-mode connections are
    dialled from the customer's network and are not subject to this check.

    ``NUBI_SSRF_ALLOW_PRIVATE`` (operators only) reopens private and loopback
    ranges for self-hosted deployments; link-local and metadata never open.
    """
    if not isinstance(config, dict):
        return
    for key in _HOST_KEYS:
        _check_host(config.get(key))
    for key in _URL_KEYS:
        _check_host(_host_of(config.get(key)))


# ---------------------------------------------------------------------------
# Pinning: the driver dials the address that was checked
# ---------------------------------------------------------------------------
#
# Checking a host name and then handing the NAME to a database driver leaves a
# second lookup at connect time, which need not give the same answer.  For the
# engines below the checked address itself is put into the connection, the way
# resolve_and_pin does for HTTP:
#
# * Postgres family — libpq's ``hostaddr`` carries the address; ``host`` stays
#   the name, so TLS verification and authentication are unchanged.
# * MySQL family — the address replaces the host (these connections carry no
#   host-name verification to preserve).

_POSTGRES_TYPES = frozenset({"postgres", "postgresql", "redshift", "cockroachdb", "cloudsql"})
_MYSQL_TYPES = frozenset({"mysql", "mariadb"})


def _pinned_address(host: str) -> str:
    """The one checked address to dial for *host*; refused when there is none."""
    addrs = _checked_addresses(host)
    if not addrs:
        # A name that does not resolve now could resolve to anything later.
        raise _not_allowed()
    return str(addrs[0])


def _pinned_list(hosts: str) -> str:
    return ",".join(_pinned_address(part) for part in hosts.split(","))


def ip_literals(value: Any) -> str:
    """Return *value* if it is an IP literal or a comma-separated list of them."""
    if not isinstance(value, str) or not value.strip():
        raise _not_allowed()
    for part in value.split(","):
        try:
            ipaddress.ip_address(part.strip())
        except ValueError:
            raise _not_allowed() from None
    return ",".join(part.strip() for part in value.split(","))


def _check_dialled_addresses(value: str) -> None:
    """An address list a connection string already names is what the driver
    will dial, so it must be IP literals and every one must pass the policy."""
    _check_host(ip_literals(value))


def _pin_postgres_dsn(dsn: str) -> str:
    """Add ``hostaddr`` for every host a Postgres connection string names."""
    text = dsn.strip()
    if "://" not in text:
        if not _KEYWORD_DSN_RE.match(text):
            raise _not_allowed()
        pairs = {
            key.lower(): (raw[1:-1] if raw.startswith("'") else raw)
            for key, raw in _KEYWORD_PAIR_RE.findall(text)
        }
        if pairs.get("hostaddr"):
            _check_dialled_addresses(pairs["hostaddr"])
            return text
        if not pairs.get("host"):
            raise _not_allowed()
        return f"{text} hostaddr={_pinned_list(pairs['host'])}"
    try:
        parts = urlsplit(text)
        params = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        raise _not_allowed() from None
    named = {k.lower(): v for k, v in params}
    if named.get("hostaddr"):
        _check_dialled_addresses(named["hostaddr"])
        return text
    authority = [h for h in _netloc_hosts(parts.netloc) if h]
    if authority and named.get("host"):
        raise _not_allowed()  # two sources for the target: which one is dialled is the driver's call
    hosts = ",".join(authority) or named.get("host") or ""
    if not hosts:
        raise _not_allowed()
    sep = "&" if parts.query else "?"
    base, frag = (text.split("#", 1) + [""])[:2]
    return f"{base}{sep}hostaddr={_pinned_list(hosts)}" + (f"#{frag}" if frag else "")


def _pin_mysql_dsn(dsn: str) -> str:
    """Replace the host of a MySQL connection string with its checked address."""
    try:
        parts = urlsplit(dsn.strip())
        host = parts.hostname
    except ValueError:
        raise _not_allowed() from None
    if not host or "," in parts.netloc.rsplit("@", 1)[-1]:
        raise _not_allowed()
    ip = _pinned_address(host)
    userinfo, at, hostport = parts.netloc.rpartition("@")
    port = hostport.rsplit(":", 1)[1] if (":" in hostport and not hostport.endswith("]")) else ""
    literal = f"[{ip}]" if ":" in ip else ip
    netloc = f"{userinfo}{at}{literal}" + (f":{port}" if port else "")
    return parts._replace(netloc=netloc).geturl()


def pin_connector_target(config: dict[str, Any]) -> None:
    """``guard_connector_target`` for a connection that is about to be opened.

    Applies the same policy, and for Postgres- and MySQL-family configs also
    rewrites *config* in place so the driver dials the checked address:
    ``hostaddr`` is set (Postgres) or ``host`` is replaced (MySQL), and a
    ``dsn`` is rewritten the same way.  Call it on the working copy of a
    config, never on one that will be stored.
    """
    if not isinstance(config, dict):
        return
    ctype = str(config.get("connector_type") or config.get("type") or "").lower()
    if ctype not in _POSTGRES_TYPES and ctype not in _MYSQL_TYPES:
        guard_connector_target(config)
        return

    # Everything that is not the dialled target still goes through the guard.
    for key in _HOST_KEYS:
        if key not in ("host", "hostaddr"):
            _check_host(config.get(key))
    for key in _URL_KEYS:
        if key != "dsn":
            _check_host(_host_of(config.get(key)))

    dsn = config.get("dsn")
    host = config.get("host")
    if host is not None and host != "" and not isinstance(host, str):
        raise _not_allowed()
    if ctype in _POSTGRES_TYPES:
        if dsn:
            config["dsn"] = _pin_postgres_dsn(str(dsn))
            _check_host(host)  # unused by the driver, but never left unchecked
            _check_host(config.get("hostaddr"))
        elif host:
            config["hostaddr"] = _pinned_list(host)
        else:
            config.pop("hostaddr", None)
    else:
        if dsn:
            config["dsn"] = _pin_mysql_dsn(str(dsn))
            _check_host(host)
        elif host:
            if "," in host:
                raise _not_allowed()
            config["host"] = _pinned_address(host)
        _check_host(config.get("hostaddr"))


def connection_host(host: Any, *, default: str = "localhost") -> str:
    """Return *host* for use inside an assembled connection string.

    Only plain host names and IP literals (or a comma-separated list of them)
    are accepted, so the value cannot carry anything else into the string.
    When the config names no host the driver would dial *default*; that
    implied target goes through the same policy as an explicit one.
    """
    if host is None or host == "":
        _check_host(default)
        return default
    if not isinstance(host, str):
        raise _not_allowed()
    for part in host.split(","):
        part = part.strip()
        literal = part[1:-1] if part.startswith("[") and part.endswith("]") else part
        try:
            ipaddress.ip_address(literal)
        except ValueError:
            if not _HOSTNAME_RE.match(part):
                raise _not_allowed() from None
    return host.strip()


def guard_connector_endpoint(endpoint: str) -> None:
    """The same policy for a tenant-supplied endpoint (e.g. object storage)."""
    _check_host(_host_of(endpoint))
