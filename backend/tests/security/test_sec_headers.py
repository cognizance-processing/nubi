"""Every response carries baseline browser-security headers."""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.testclient import TestClient

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

INLINE = "(function(){document.documentElement.classList.add('dark')})();"
INDEX = f'<!doctype html><html><head><script type="application/ld+json">{{}}</script><script>{INLINE}</script></head><body></body></html>'


def _app(tmp_path, env="production"):
    from app.middleware.security_headers import register_security_headers

    index = tmp_path / "index.html"
    index.write_text(INDEX)
    app = FastAPI()
    register_security_headers(app, index_html=str(index), env=env)

    @app.get("/api/v1/thing")
    async def thing():
        return {"ok": True}

    @app.get("/embed/demo.html", response_class=HTMLResponse)
    async def embed_demo():
        return "<html></html>"

    @app.get("/assets/engine.worker.js")
    async def worker_script():
        return Response("self.onmessage = () => {}", media_type="text/javascript")

    @app.get("/embed/nubi-embed.js")
    async def embed_script():
        return Response("void 0", media_type="text/javascript")

    @app.get("/", response_class=HTMLResponse)
    async def spa():
        return INDEX

    return TestClient(app)


def test_api_responses_are_locked_down(tmp_path):
    resp = _app(tmp_path).get("/api/v1/thing")
    h = resp.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "max-age=" in h["strict-transport-security"]
    csp = h["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp


def test_script_files_can_run_as_workers_that_load_webassembly(tmp_path):
    """A worker is governed by the policy on its own script response."""
    client = _app(tmp_path)
    csp = client.get("/assets/engine.worker.js").headers["content-security-policy"]
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
    assert "'wasm-unsafe-eval'" in directives["script-src"]
    assert "'self'" in directives["connect-src"]
    assert "'unsafe-inline'" not in directives["script-src"]
    assert directives["frame-ancestors"] == "'none'"
    # Embed scripts keep their own (host-loadable) policy.
    embed = client.get("/embed/nubi-embed.js").headers["content-security-policy"]
    assert embed == "default-src 'none'; base-uri 'none'"


def test_app_document_gets_a_script_policy_that_allows_only_its_own_code(tmp_path):
    resp = _app(tmp_path).get("/")
    csp = resp.headers["content-security-policy"]
    digest = base64.b64encode(hashlib.sha256(INLINE.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in csp  # the theme bootstrap, by hash
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    # No inline scripts and no other origins: only the app's own files run.
    assert "'unsafe-inline'" not in script_src
    assert "http" not in script_src and "*" not in script_src
    assert "'self'" in script_src
    for directive in ("object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'", "form-action 'self'"):
        assert directive in csp
    assert "img-src" in csp and "connect-src" in csp


def test_embed_assets_may_be_framed_by_hosts(tmp_path):
    csp = _app(tmp_path).get("/embed/demo.html").headers["content-security-policy"]
    assert "frame-ancestors 'none'" not in csp


def test_hsts_is_only_sent_in_production(tmp_path):
    assert "strict-transport-security" not in _app(tmp_path, env="development").get("/api/v1/thing").headers


def test_the_application_registers_the_middleware():
    import main

    names = {m.cls.__name__ for m in main.app.user_middleware}
    assert "SecurityHeadersMiddleware" in names


@pytest.mark.parametrize("mode", ["off", "report-only"])
def test_document_policy_mode_is_configurable(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("NUBI_CSP_MODE", mode)
    h = _app(tmp_path).get("/").headers
    if mode == "off":
        assert "content-security-policy" not in h and "content-security-policy-report-only" not in h
    else:
        assert "content-security-policy-report-only" in h
    assert h["x-content-type-options"] == "nosniff"
