"""Baseline browser-security response headers.

Applied to every response:

* ``X-Content-Type-Options: nosniff`` and ``Referrer-Policy``.
* ``Strict-Transport-Security`` in production.
* A ``Content-Security-Policy``:
    - API and other non-document responses get ``default-src 'none'`` — they
      are data, never a page.
    - HTML documents (the app shell) get a policy that only runs the app's own
      scripts: same-origin files plus the inline theme bootstrap, admitted by
      hash.  No ``unsafe-inline`` or ``unsafe-eval`` for scripts.
    - Nothing may frame the app (``frame-ancestors 'none'``), except assets
      under ``/embed/`` which host pages load by design.

``NUBI_CSP_MODE`` — ``enforce`` (default), ``report-only`` or ``off`` — controls
the *document* policy only, so a deployment can stage it.  The API policy and
the other headers are always sent.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_INLINE_SCRIPT_RE = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.DOTALL | re.IGNORECASE)
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


def _inline_script_hashes(index_html: str | None) -> list[str]:
    """``'sha256-…'`` sources for the executable inline scripts in the app shell."""
    if not index_html or not os.path.isfile(index_html):
        return []
    try:
        with open(index_html, encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        return []
    hashes: list[str] = []
    for match in _INLINE_SCRIPT_RE.finditer(html):
        attrs = match.group("attrs").lower()
        if "src=" in attrs:
            continue
        kind = re.search(r'type\s*=\s*["\']?([^"\'\s>]+)', attrs)
        if kind and kind.group(1) not in ("text/javascript", "module", "application/javascript"):
            continue  # data blocks (e.g. JSON-LD) are not executed
        digest = hashlib.sha256(match.group("body").encode("utf-8")).digest()
        hashes.append(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")
    return hashes


def _document_csp(script_hashes: list[str], *, frameable: bool, env: str) -> str:
    dev = env != "production"
    script_src = ["'self'", "'wasm-unsafe-eval'", *script_hashes]
    connect_src = ["'self'", "https:", "wss:", "blob:", "data:"]
    if dev:
        connect_src += ["http://localhost:*", "http://127.0.0.1:*", "ws://localhost:*", "ws://127.0.0.1:*"]
    directives = [
        "default-src 'self'",
        "script-src " + " ".join(script_src),
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src " + " ".join(connect_src),
        "worker-src 'self' blob:",
        "child-src 'self' blob:",
        "frame-src 'self' blob:",
        "media-src 'self' data: blob:",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    if not frameable:
        directives.append("frame-ancestors 'none'")
    return "; ".join(directives)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, index_html: str | None = None, env: str = "production") -> None:
        super().__init__(app)
        self._env = env
        self._hashes = _inline_script_hashes(index_html)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if self._env == "production":
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        if "content-security-policy" in headers or "content-security-policy-report-only" in headers:
            return response  # a route set its own policy

        path = request.url.path
        frameable = path.startswith("/embed/")
        is_document = headers.get("content-type", "").lower().startswith("text/html")
        if not is_document:
            # /embed/* scripts and styles are loaded by host pages; a
            # default-src 'none' policy on a script response is harmless but
            # frame-ancestors is left off for anything under /embed/.
            headers["Content-Security-Policy"] = (
                "default-src 'none'; base-uri 'none'" if frameable else _API_CSP
            )
            return response

        if path in ("/docs", "/redoc") or path.startswith("/docs/oauth2"):
            return response  # API explorer pages (non-production only) load their own assets

        mode = os.environ.get("NUBI_CSP_MODE", "enforce").strip().lower()
        if mode == "off":
            return response
        policy = _document_csp(self._hashes, frameable=frameable, env=self._env)
        name = "Content-Security-Policy-Report-Only" if mode == "report-only" else "Content-Security-Policy"
        headers[name] = policy
        return response


def register_security_headers(
    app: FastAPI, *, index_html: str | None = None, env: str = "production"
) -> None:
    """Add :class:`SecurityHeadersMiddleware` to *app*."""
    app.add_middleware(SecurityHeadersMiddleware, index_html=index_html, env=env)
