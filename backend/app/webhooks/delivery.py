"""Signed async delivery for outbound webhooks.

A delivery is a POST of a canonical JSON body to a host-registered HTTPS URL,
authenticated with an HMAC-SHA256 signature the host can verify.

Signing
-------
The signature covers ``"{timestamp}.{body}"`` (the Stripe-style signed payload)
so a host can reject replays by checking the timestamp skew. Headers:

    X-Nubi-Signature  hex HMAC-SHA256 of ``"{ts}.{body}"`` keyed by the secret
    X-Nubi-Timestamp  the unix timestamp (seconds) used in the signed payload
    X-Nubi-Event      the event type (e.g. ``watch_breach``)
    Content-Type      application/json

The body is serialised ONCE with ``json.dumps(..., separators=(",", ":"),
sort_keys=True)`` and the SAME bytes are both signed and sent, so the host's
recomputation matches byte-for-byte.

Delivery discipline
--------------------
- Bounded retry with exponential backoff on transport errors and 5xx / 429.
- 2xx is success; other 4xx are permanent (no retry — the host rejected it).
- Fire-and-forget: :func:`dispatch_event` schedules deliveries on the running
  loop and returns immediately; failures are logged, NEVER raised, so a webhook
  can never break the request that triggered the event.

DNS-rebinding defence
---------------------
:func:`deliver_one` calls ``resolve_and_pin`` (from ``app.connectors.ssrf``)
to resolve the target hostname exactly **once**, validate every address against
the SSRF blocklist, and return the first safe IP literal.  The HTTP client is
then given a custom transport (:class:`_PinnedTransport`) that connects
directly to that IP — bypassing any subsequent DNS resolution — while
preserving the original hostname in:

* the ``Host`` request header (so virtual-hosting works), and
* the TLS ``sni_hostname`` extension consumed by httpcore's ``_connect`` path
  (so the TLS cert is validated against the hostname, not the raw IP literal).

A zero-TTL attacker domain cannot rebind between the check and the connect
because the socket is opened to the *pinned* IP, not re-resolved.  If the
pinned IP later turns out to be private (e.g. the resolution in this function
yields a private address despite the guard), the connection is refused
fail-closed via a secondary ``_is_forbidden`` check inside the transport.

The defence-in-depth guard_url call is retained at the top of deliver_one
because guard_url is cheap (no external request) and catches blatant literals
(``http://127.0.0.1/...``) before we even call resolve_and_pin.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import time
from typing import Any

logger = logging.getLogger("app.webhooks.delivery")

# Tunables (kept small so a slow/dead host never ties up resources).
_TIMEOUT_S = 10.0
_MAX_ATTEMPTS = 4
_BASE_BACKOFF_S = 0.5


def canonical_body(payload: dict[str, Any]) -> bytes:
    """Serialise *payload* to the canonical bytes that are signed AND sent.

    Stable key order + compact separators so the host can recompute the exact
    same bytes for signature verification.
    """
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """Return the hex HMAC-SHA256 over ``"{timestamp}.{body}"`` keyed by *secret*."""
    signed_payload = f"{timestamp}.".encode("utf-8") + body
    return hmac.new(
        secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()


def verify(secret: str, body: bytes, timestamp: int, signature: str) -> bool:
    """Constant-time verify a signature produced by :func:`sign`.

    Provided for hosts/tests; the delivery path itself only signs.
    """
    expected = sign(secret, body, timestamp)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Pinned transport — the DNS-rebinding fix
# ---------------------------------------------------------------------------


def _build_pinned_client(pinned_ip: str, hostname: str, scheme: str, port: int, timeout_s: float):
    """Return an ``httpx.AsyncClient`` that connects to *pinned_ip* while
    presenting *hostname* as the ``Host`` header and TLS SNI name.

    How the pin works
    -----------------
    httpcore's ``AsyncHTTPConnection._connect`` reads ``request.extensions``
    for the key ``"sni_hostname"`` and passes it as the ``server_hostname``
    argument to ``stream.start_tls()`` — overriding the SNI derived from
    ``origin.host``.  We build our origin with the raw IP as the host (so the
    TCP socket connects to that IP), inject ``sni_hostname = hostname`` into
    every request's extensions (so TLS cert validation still uses the hostname),
    and ensure the ``Host`` header carries the original hostname.

    The transport is a thin ``httpx.AsyncBaseTransport`` subclass that rewrites
    the outgoing request before forwarding it to an inner
    ``httpx.AsyncHTTPTransport`` whose httpcore pool targets the IP origin.

    Fail-closed secondary check
    ---------------------------
    Before building the origin we verify that *pinned_ip* is not a private/
    reserved address.  This is a redundant guard (resolve_and_pin already
    blocks private IPs) but defends against logic errors in the caller.
    """
    import httpx  # noqa: PLC0415

    # --- Fail-closed secondary check ---
    # resolve_and_pin already validated the IP; this guard defends against
    # future callers that skip resolve_and_pin or pass a stale IP.
    try:
        addr = ipaddress.ip_address(pinned_ip)
    except ValueError:
        raise ValueError(f"pinned_ip {pinned_ip!r} is not a valid IP address")

    from app.connectors.ssrf import _is_forbidden, _allow_private  # noqa: PLC0415

    if _is_forbidden(addr, allow_private=_allow_private()):
        raise ValueError(
            f"Refusing to connect to pinned IP {pinned_ip!r}: address is "
            "private/reserved (DNS rebinding fail-closed)."
        )

    # --- Build an httpcore pool whose origin is the pinned IP ---
    # The pool connects to <scheme>://<pinned_ip>:<port> at the TCP level,
    # completely bypassing further DNS resolution.
    scheme_b = scheme.encode()
    ip_b = pinned_ip.encode()

    class _PinnedTransport(httpx.AsyncBaseTransport):
        """Rewrite each outbound request to connect to the pinned IP while
        preserving the original Host header and TLS SNI for the hostname."""

        def __init__(self) -> None:
            import httpcore  # noqa: PLC0415

            self._pool = httpcore.AsyncConnectionPool(
                ssl_context=httpx.create_ssl_context() if scheme == "https" else None,
            )

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            import httpcore  # noqa: PLC0415

            # Build the httpcore URL pointing at the pinned IP, but keep the
            # original path/query from the request.
            pinned_url = httpcore.URL(
                scheme=scheme_b,
                host=ip_b,
                port=port,
                target=request.url.raw_path,
            )

            # Merge caller extensions with sni_hostname so TLS handshakes use
            # the hostname (not the IP) for certificate validation.
            extensions: dict[str, Any] = dict(request.extensions)
            extensions["sni_hostname"] = hostname

            # Build request headers: replace/inject the Host header so the
            # server-side virtual hosting routes correctly.
            raw_headers = [
                (k, v)
                for k, v in request.headers.raw
                if k.lower() != b"host"
            ]
            host_header_value = hostname.encode()
            if port not in (80, 443):
                host_header_value += f":{port}".encode()
            raw_headers.append((b"host", host_header_value))

            core_request = httpcore.Request(
                method=request.method,
                url=pinned_url,
                headers=raw_headers,
                content=request.stream,
                extensions=extensions,
            )

            with httpx._transports.default.map_httpcore_exceptions():  # noqa: SLF001
                resp = await self._pool.handle_async_request(core_request)

            return httpx.Response(
                status_code=resp.status,
                headers=resp.headers,
                stream=httpx._transports.default.AsyncResponseStream(resp.stream),  # noqa: SLF001
                extensions=resp.extensions,
            )

        async def aclose(self) -> None:
            await self._pool.aclose()

    return httpx.AsyncClient(
        transport=_PinnedTransport(),
        timeout=timeout_s,
        # Disable httpx's own redirect following so we don't chase a redirect
        # to a private IP after the SSRF check.
        follow_redirects=False,
    )


async def deliver_one(
    url: str,
    secret: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = _MAX_ATTEMPTS,
    base_backoff_s: float = _BASE_BACKOFF_S,
    timeout_s: float = _TIMEOUT_S,
) -> bool:
    """Deliver one signed event to *url*, retrying with backoff. Never raises.

    Returns ``True`` on a 2xx response, ``False`` if all attempts are exhausted
    or the host returned a permanent (non-429) 4xx.

    DNS-rebinding protection: the hostname is resolved once via
    ``resolve_and_pin``, the resulting public IP is validated, and all HTTP
    connections for this delivery target that pinned IP directly.  The original
    hostname is preserved in the ``Host`` header and TLS SNI so cert validation
    still works correctly.  A secondary fail-closed check inside the transport
    ensures that even if the caller somehow passes a private IP it is rejected
    at connect time.
    """
    # SECURITY (SSRF / DNS-rebinding):
    #
    # Step 1 — defence-in-depth guard_url call for cheap pre-flight validation
    #           (catches blatant literals like http://127.0.0.1/..., file://,
    #            etc. before we spend any resolver effort).
    #
    # Step 2 — resolve_and_pin: resolve ONCE, reject if any address is blocked,
    #           pin the first safe IP.  The transport uses that IP directly so
    #           no second DNS resolution occurs between the check and the connect.
    try:
        from app.connectors.ssrf import guard_url as _guard_url, resolve_and_pin  # noqa: PLC0415

        _guard_url(url)                      # Step 1: cheap pre-flight check
        pinned = resolve_and_pin(url)        # Step 2: single-resolution + pin
    except Exception as _ssrf_exc:  # noqa: BLE001
        logger.warning(
            "webhook %s -> %s blocked by SSRF guard: %s; not delivering",
            event_type,
            url,
            _ssrf_exc,
        )
        return False

    body = canonical_body(payload)
    timestamp = int(time.time())
    signature = sign(secret, body, timestamp)
    headers = {
        "Content-Type": "application/json",
        "X-Nubi-Signature": signature,
        "X-Nubi-Timestamp": str(timestamp),
        "X-Nubi-Event": event_type,
    }

    # Build a client that connects to the pinned IP while preserving Host/SNI.
    try:
        client = _build_pinned_client(
            pinned_ip=pinned.ip,
            hostname=pinned.host,
            scheme=pinned.url.split("://", 1)[0].lower(),
            port=pinned.port,
            timeout_s=timeout_s,
        )
    except Exception as _build_exc:  # noqa: BLE001 — fail closed
        logger.warning(
            "webhook %s -> %s: could not build pinned client: %s; not delivering",
            event_type,
            url,
            _build_exc,
        )
        return False

    for attempt in range(1, max_attempts + 1):
        try:
            async with client:
                resp = await client.post(url, content=body, headers=headers)
            status = resp.status_code
            if 200 <= status < 300:
                return True
            # 4xx (except 429) is permanent — the host rejected the delivery.
            if 400 <= status < 500 and status != 429:
                logger.warning(
                    "webhook %s -> %s rejected (status=%s); not retrying",
                    event_type,
                    url,
                    status,
                )
                return False
            # 5xx / 429 → retryable.
            logger.info(
                "webhook %s -> %s attempt %d/%d got status=%s; will retry",
                event_type,
                url,
                attempt,
                max_attempts,
                status,
            )
        except Exception as exc:  # noqa: BLE001 — transport errors are retryable.
            logger.info(
                "webhook %s -> %s attempt %d/%d transport error: %s",
                event_type,
                url,
                attempt,
                max_attempts,
                exc,
            )

        if attempt < max_attempts:
            # Exponential backoff: base * 2^(attempt-1).
            await asyncio.sleep(base_backoff_s * (2 ** (attempt - 1)))

    logger.warning(
        "webhook %s -> %s exhausted %d attempts; giving up",
        event_type,
        url,
        max_attempts,
    )
    return False


async def deliver_to_org(
    org_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> int:
    """Fan out *event_type* to every active subscribed endpoint in *org_id*.

    STRICTLY org-scoped: endpoints are looked up by ``org_id`` so org A's
    endpoints never receive org B's events. Returns the number of endpoints the
    event was delivered to successfully. Never raises — a store or delivery
    failure is logged and treated as a non-delivery.
    """
    from app.webhooks.models import get_webhook_store  # noqa: PLC0415

    if not org_id:
        return 0

    try:
        endpoints = await get_webhook_store().list_active_for_event(
            str(org_id), event_type
        )
    except Exception as exc:  # noqa: BLE001 — never break the caller on a store error.
        logger.warning(
            "webhook lookup failed for org=%s event=%s: %s",
            org_id,
            event_type,
            exc,
        )
        return 0

    if not endpoints:
        return 0

    results = await asyncio.gather(
        *(
            deliver_one(ep["url"], ep["secret"], event_type, payload)
            for ep in endpoints
        ),
        return_exceptions=True,
    )
    return sum(1 for r in results if r is True)


def dispatch_event(
    org_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Fire-and-forget entrypoint: schedule org-scoped delivery, never block/raise.

    If a running event loop is available the fan-out runs as a background task;
    otherwise (no loop — e.g. a sync context) it runs synchronously to
    completion. Either way, exceptions are swallowed: a webhook must never break
    the request/flow that triggered it.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        task = loop.create_task(_safe_deliver(org_id, event_type, payload))
        # Drop a reference so the task isn't GC'd before it runs; we never await it.
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return

    # No running loop: deliver synchronously (best-effort, still never raises).
    try:
        asyncio.run(_safe_deliver(org_id, event_type, payload))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "webhook dispatch (sync) failed for org=%s event=%s: %s",
            org_id,
            event_type,
            exc,
        )


# Strong refs to in-flight fire-and-forget tasks (avoids premature GC).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


async def _safe_deliver(
    org_id: str, event_type: str, payload: dict[str, Any]
) -> None:
    """Wrapper that guarantees the background task never propagates an error."""
    try:
        await deliver_to_org(org_id, event_type, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "webhook delivery task failed for org=%s event=%s: %s",
            org_id,
            event_type,
            exc,
        )
