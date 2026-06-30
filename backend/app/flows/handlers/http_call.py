"""Task handler for the ``http_call`` flow task kind.

Lets a flow POST (or GET/PUT/PATCH/DELETE) to a host HTTP endpoint so the
host can run nightly domain jobs on Nubi's scheduler.

Security
--------
* **SSRF-guarded** — every URL is validated via
  :func:`app.connectors.ssrf.resolve_and_pin` (DNS-rebinding-safe) before the
  request is issued.  Loopback, RFC1918, link-local, and cloud-metadata targets
  are all blocked; set ``NUBI_SSRF_ALLOW_PRIVATE=1`` for self-hosted dev.

* **Org-level allowlist** — when the flow's ``runtime_config`` carries an
  ``http_call_allowed_hosts`` list, the URL's hostname must be in that list.
  Evaluated AFTER the SSRF check so the allowlist can never override the
  always-blocked set.

* **Auth via encrypted secrets only** — the ``auth.secret_name`` config key
  names an org secret resolved at run time; the value (e.g. a bearer token) is
  injected into the request headers and NEVER written to logs, run records, or
  task results (secret redaction in the executor covers any accidental leaks).

* **Response truncation** — the body snippet stored in the run record is capped
  at 2 KiB so a verbose endpoint cannot cause O(N) jsonb growth.

Config shape
------------
::

    {
        "url":     "https://hooks.myhost.com/nubi/nightly",   # required
        "method":  "POST",                                     # default POST
        "headers": {"X-Source": "nubi-flow"},                 # optional
        "body":    {"run_date": "{{ params.run_date }}"},      # optional JSON body
        "auth": {
            "kind":        "bearer",          # bearer | header | basic
            "secret_name": "MY_WEBHOOK_KEY",  # org secret name (NOT the value)
            # bearer → Authorization: Bearer <secret>
            # header → injects as the header named by auth.header_name
            # basic  → HTTP Basic (user:password — secret is the password)
            "header_name": "X-Custom-Token",  # only for kind=header
            "username":    "nubi",             # only for kind=basic (default nubi)
        }
    }

Returns
-------
dict
    ``{status_code, ok, response_snippet, url, method}``

The task SUCCEEDS on 2xx and RAISES (failing the run) on non-2xx or timeout.
``response_snippet`` is always secret-free (truncated raw text, never the
resolved auth credential).
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext

logger = logging.getLogger("nubi.flows.http_call")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
_DEFAULT_METHOD = "POST"
_DEFAULT_TIMEOUT_S = 30
_RESPONSE_SNIPPET_BYTES = int(os.environ.get("NUBI_HTTP_CALL_SNIPPET_BYTES", "2048"))


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Issue an HTTP request to the configured endpoint.

    Parameters
    ----------
    config:
        Task config dict (already template-resolved by the executor).
    ctx:
        Task execution context.  ``ctx.secrets`` carries resolved org secrets
        for auth injection.  ``ctx.flow`` may carry org-level settings.
    claims:
        Caller auth claims (carried for interface parity; not used directly).

    Returns
    -------
    dict
        ``{status_code, ok, response_snippet, url, method}``

    Raises
    ------
    AppError("ssrf_blocked", 400)
        When the URL resolves to a forbidden (private/loopback/metadata) address.
    AppError("http_call_not_allowed", 403)
        When the hostname is not in the org's ``http_call_allowed_hosts`` list.
    AppError("invalid_task_config", 400)
        When required config fields are missing or invalid.
    RuntimeError
        When the HTTP response status is non-2xx or the request times out.
    """
    from app.connectors.ssrf import resolve_and_pin  # noqa: PLC0415
    from app.errors import AppError  # noqa: PLC0415

    # ── 1. Validate required fields ───────────────────────────────────────────
    url: str | None = config.get("url")
    if not url or not isinstance(url, str) or not url.strip():
        raise AppError(
            "invalid_task_config",
            "http_call task requires 'url' in config.",
            400,
        )
    url = url.strip()

    method: str = str(config.get("method") or _DEFAULT_METHOD).upper().strip()
    if method not in _VALID_METHODS:
        raise AppError(
            "invalid_task_config",
            f"http_call: unsupported method {method!r}. "
            f"Allowed: {sorted(_VALID_METHODS)}.",
            400,
        )

    # ── 2. SSRF guard (resolve_and_pin: DNS-rebinding-safe) ───────────────────
    # resolve_and_pin raises AppError("ssrf_blocked") if ANY resolved address
    # is loopback / private / link-local / metadata.
    pinned = resolve_and_pin(url)

    # ── 3. Org-level host allowlist (optional) ────────────────────────────────
    _check_host_allowlist(pinned.host, ctx)

    # ── 4. Build request headers ──────────────────────────────────────────────
    headers: dict[str, str] = {}
    cfg_headers: Any = config.get("headers")
    if isinstance(cfg_headers, dict):
        for k, v in cfg_headers.items():
            headers[str(k)] = str(v)

    # ── 5. Resolve auth (secret-based; value never logged) ───────────────────
    auth_cfg: Any = config.get("auth")
    if isinstance(auth_cfg, dict):
        _inject_auth(auth_cfg, headers, ctx)

    # ── 6. Build JSON body ────────────────────────────────────────────────────
    body_cfg: Any = config.get("body")
    json_body: str | None = None
    if body_cfg is not None:
        if isinstance(body_cfg, (dict, list)):
            json_body = json.dumps(body_cfg)
        elif isinstance(body_cfg, str):
            json_body = body_cfg
        if json_body:
            headers.setdefault("Content-Type", "application/json")

    # ── 7. Resolve timeout ────────────────────────────────────────────────────
    timeout_s: int = int(config.get("timeout_s") or _DEFAULT_TIMEOUT_S)
    if timeout_s <= 0:
        timeout_s = _DEFAULT_TIMEOUT_S

    # ── 8. Issue the request (pinned to the checked IP, SNI = original host) ──
    status_code, response_snippet = _do_request(
        method=method,
        url=url,
        pinned_ip=pinned.ip,
        pinned_host=pinned.host,
        pinned_port=pinned.port,
        headers=headers,
        body=json_body,
        timeout_s=timeout_s,
    )

    ok = 200 <= status_code < 300
    logger.info(
        "http_call %s %s → %d (%s)",
        method,
        url,
        status_code,
        "ok" if ok else "FAIL",
    )

    if not ok:
        raise RuntimeError(
            f"http_call: {method} {url} returned HTTP {status_code}. "
            f"Response: {response_snippet[:500]}"
        )

    return {
        "status_code": status_code,
        "ok": ok,
        "response_snippet": response_snippet,
        "url": url,
        "method": method,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_host_allowlist(host: str, ctx: "TaskContext") -> None:
    """Raise AppError if the org has an allowlist and *host* is not in it.

    The allowlist is read from ``ctx.flow.runtime_config.http_call_allowed_hosts``.
    If no list is configured, all (SSRF-clean) hosts are permitted.

    Parameters
    ----------
    host:
        Hostname extracted from the URL (already validated by resolve_and_pin).
    ctx:
        Task execution context.  ``ctx.flow`` may carry org-level runtime config.
    """
    from app.errors import AppError  # noqa: PLC0415

    flow = getattr(ctx, "flow", None)
    if not isinstance(flow, dict):
        return

    rc = flow.get("runtime_config") or {}
    raw = rc.get("http_call_allowed_hosts")
    if not isinstance(raw, list) or not raw:
        return  # no allowlist configured → permit all SSRF-clean hosts

    allowed_hosts: list[str] = [str(h).strip().lower() for h in raw if h]
    host_lower = host.strip().lower()

    if host_lower not in allowed_hosts:
        raise AppError(
            "http_call_not_allowed",
            f"http_call: host {host!r} is not in the org's "
            "http_call_allowed_hosts list. Add it to allow outbound calls.",
            403,
        )


def _inject_auth(
    auth_cfg: dict[str, Any],
    headers: dict[str, str],
    ctx: "TaskContext",
) -> None:
    """Resolve the org secret named in *auth_cfg* and inject it into *headers*.

    The resolved secret VALUE is never logged or stored in the task result —
    the executor's ``_redact_outcome`` provides a second line of defence.

    Supported kinds
    ---------------
    ``bearer``
        ``Authorization: Bearer <secret>``
    ``header``
        Arbitrary header: ``auth_cfg['header_name']`` header = ``<secret>``
    ``basic``
        ``Authorization: Basic <base64(user:secret)>`` where ``user``
        defaults to ``"nubi"`` when not specified in ``auth_cfg['username']``.
    """
    kind: str = str(auth_cfg.get("kind") or "bearer").lower().strip()
    secret_name: str = str(auth_cfg.get("secret_name") or "").strip()
    if not secret_name:
        # No secret configured — skip auth injection silently.
        return

    secret_value: str = (ctx.secrets or {}).get(secret_name, "")
    if not secret_value:
        from app.errors import AppError  # noqa: PLC0415
        raise AppError(
            "invalid_task_config",
            f"http_call auth: secret {secret_name!r} is not set or empty. "
            "Ensure the org secret exists and is non-empty.",
            400,
        )

    if kind == "bearer":
        headers["Authorization"] = f"Bearer {secret_value}"
    elif kind == "header":
        header_name: str = str(auth_cfg.get("header_name") or "X-Auth-Token").strip()
        headers[header_name] = secret_value
    elif kind == "basic":
        import base64  # noqa: PLC0415
        user: str = str(auth_cfg.get("username") or "nubi")
        creds = base64.b64encode(f"{user}:{secret_value}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    else:
        from app.errors import AppError  # noqa: PLC0415
        raise AppError(
            "invalid_task_config",
            f"http_call auth: unsupported kind {kind!r}. "
            "Expected 'bearer', 'header', or 'basic'.",
            400,
        )


def _do_request(
    *,
    method: str,
    url: str,
    pinned_ip: str,
    pinned_host: str,
    pinned_port: int,
    headers: dict[str, str],
    body: str | None,
    timeout_s: int,
) -> tuple[int, str]:
    """Perform the actual HTTP request pinned to the pre-checked IP.

    Connecting to the pinned IP rather than re-resolving the hostname closes
    the TOCTOU DNS-rebinding window between the SSRF check and the socket
    connect.  TLS certificate verification still uses the ORIGINAL hostname
    (via the ``Host`` header / SNI) so TLS integrity is preserved.

    Returns ``(status_code, response_snippet)`` where *response_snippet* is
    the first ``_RESPONSE_SNIPPET_BYTES`` bytes of the response body decoded
    as UTF-8 (lossy), safe for storage in the run record.
    """
    import http.client  # noqa: PLC0415
    import ssl  # noqa: PLC0415
    from urllib.parse import urlsplit  # noqa: PLC0415

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    # Always set the Host header so HTTP/1.1 virtual-hosting works.
    headers = dict(headers)  # copy so we don't mutate caller's dict
    headers.setdefault("Host", pinned_host)
    headers.setdefault("User-Agent", "nubi-flow/1.0")

    body_bytes: bytes | None = body.encode("utf-8") if body else None
    if body_bytes is not None:
        headers.setdefault("Content-Length", str(len(body_bytes)))

    if scheme == "https":
        ssl_context = ssl.create_default_context()
        # HTTPSConnection with an IP literal: override server_hostname for SNI
        # and cert verification so they use the original hostname, not the IP.
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            pinned_ip,
            port=pinned_port,
            timeout=timeout_s,
            context=ssl_context,
        )
        # Patch the internal _server_hostname so ssl wraps with the correct SNI.
        # This is the standard workaround for connecting-to-IP with hostname SNI.
        conn._server_hostname = pinned_host  # type: ignore[attr-defined]
    else:
        conn = http.client.HTTPConnection(
            pinned_ip,
            port=pinned_port,
            timeout=timeout_s,
        )

    try:
        conn.request(method, path, body=body_bytes, headers=headers)
        resp = conn.getresponse()
        status_code: int = resp.status
        raw_body = resp.read(_RESPONSE_SNIPPET_BYTES + 1)
        response_snippet = raw_body[:_RESPONSE_SNIPPET_BYTES].decode("utf-8", errors="replace")
        return status_code, response_snippet
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
