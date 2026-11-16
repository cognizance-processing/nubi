"""Deployment defaults are safe, and login attempts are limited per account and per real client."""

from __future__ import annotations

import os
import pathlib
import stat

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

REPO = pathlib.Path(__file__).resolve().parents[3]


def _request(headers=None, peer="10.0.0.9"):
    from starlette.requests import Request

    return Request(
        {
            "type": "http", "method": "POST", "path": "/api/v1/auth/login", "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": (peer, 1234) if peer else None,
        }
    )


def test_client_address_header_is_only_trusted_from_the_proxy(monkeypatch):
    from app.middleware.ratelimit import _client_ip

    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("NUBI_TRUSTED_PROXY_CIDRS", raising=False)
    # Not behind a known proxy: the header is ignored however it is spelled.
    assert _client_ip(_request({"Fly-Client-IP": "203.0.113.7"}, peer="8.8.8.8")) == "8.8.8.8"

    monkeypatch.setenv("NUBI_TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    assert _client_ip(_request({"Fly-Client-IP": "203.0.113.7"}, peer="10.0.0.9")) == "203.0.113.7"
    # A direct caller that is not the proxy cannot choose its own bucket.
    assert _client_ip(_request({"Fly-Client-IP": "203.0.113.7"}, peer="8.8.8.8")) == "8.8.8.8"
    # Garbage in the header falls back to the peer.
    assert _client_ip(_request({"Fly-Client-IP": "not-an-ip"}, peer="10.0.0.9")) == "10.0.0.9"

    monkeypatch.delenv("NUBI_TRUSTED_PROXY_CIDRS")
    monkeypatch.setenv("FLY_APP_NAME", "nubi")
    assert _client_ip(_request({"Fly-Client-IP": "203.0.113.7"}, peer="172.19.0.2")) == "203.0.113.7"
    assert _client_ip(_request({"Fly-Client-IP": "203.0.113.7"}, peer="8.8.8.8")) == "8.8.8.8"


def test_login_attempts_are_limited_per_account(monkeypatch):
    from app.middleware import ratelimit

    monkeypatch.setattr(ratelimit._cfg, "_loaded", True, raising=False)
    monkeypatch.setattr(ratelimit._cfg, "enabled", True, raising=False)
    monkeypatch.setattr(ratelimit._cfg, "burst_factor", 1.0, raising=False)
    monkeypatch.setenv("NUBI_RATELIMIT_LOGIN_PER_ACCOUNT_RPM", "3")
    ratelimit._buckets.clear()

    results = [ratelimit.consume_login_attempt("Victim@Example.com")[0] for _ in range(5)]
    assert results[:3] == [True, True, True] and results[3:] == [False, False]
    # Same account whatever the casing; another account is unaffected.
    assert ratelimit.consume_login_attempt("victim@example.com")[0] is False
    assert ratelimit.consume_login_attempt("someone-else@example.com")[0] is True
    ratelimit._buckets.clear()


def test_login_route_uses_the_per_account_limit():
    import inspect

    from app.routes import auth

    src = inspect.getsource(auth.login)
    assert "consume_login_attempt" in src
    assert src.index("consume_login_attempt") < src.index("verify_password")


BASE = {
    "DATABASE_URL": "postgresql://u:p@localhost/db",
    "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "", "GOOGLE_REDIRECT_URI": "",
    "FRONTEND_URL": "http://localhost:3000",
}


@pytest.mark.parametrize(
    "secret",
    [
        "replace-me-with-openssl-rand-hex-32-output",
        "changeme-changeme-changeme-changeme-1234",
        "your-secret-key-here-your-secret-key-here",
        "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef",
        "a" * 40,
    ],
)
def test_production_refuses_placeholder_signing_secrets(secret):
    from app.config import Settings, assert_production_secrets

    with pytest.raises(RuntimeError):
        assert_production_secrets(Settings(_env_file=None, ENV="production", JWT_SECRET=secret, **BASE))
    # Fine outside production.
    assert_production_secrets(Settings(_env_file=None, ENV="development", JWT_SECRET=secret, **BASE))


def test_a_real_secret_is_accepted_in_production():
    import secrets

    from app.config import Settings, assert_production_secrets

    assert_production_secrets(Settings(_env_file=None, ENV="production", JWT_SECRET=secrets.token_hex(32), **BASE))


def test_application_startup_runs_the_secret_check():
    import inspect

    import main

    assert "assert_production_secrets" in inspect.getsource(main.lifespan)


def test_there_is_no_default_admin_password():
    from app.config import Settings

    assert Settings.model_fields["SUPERUSER_PASSWORD"].default in ("", None)


def test_seed_refuses_in_production_and_without_a_password():
    src = (REPO / "backend" / "seed.py").read_text()
    assert "production" in src and "SUPERUSER_PASSWORD" in src
    assert "/ {TEST_PASSWORD}" not in src  # the password is never printed
    assert "SUPERUSER_PASSWORD}" not in (REPO / "backend" / "reset_db.py").read_text()


def test_compose_files_ship_no_usable_secret_and_keep_storage_local():
    env = (REPO / ".env.compose").read_text()
    line = next(l for l in env.splitlines() if l.startswith("JWT_SECRET="))
    assert line.strip() == "JWT_SECRET="
    compose = (REPO / "docker-compose.yml").read_text()
    assert '"9000:9000"' not in compose and '"9001:9001"' not in compose
    assert "127.0.0.1:9000:9000" in compose and "127.0.0.1:9001:9001" in compose


def test_api_schema_is_not_served_in_production(monkeypatch):
    import main
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ENV", "production")
    app = main.create_app()
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None
    monkeypatch.setattr(settings, "ENV", "development")
    assert main.create_app().openapi_url == "/openapi.json"


def test_cli_token_file_is_private(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("nubi_cli_config", REPO / "cli" / "nubi_cli" / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    monkeypatch.setattr(mod, "_CREDENTIALS_PATH", tmp_path / ".nubi" / "credentials")
    mod.save_token("nubi_key_secret")
    path = tmp_path / ".nubi" / "credentials"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    # An existing world-readable file is tightened on the next write.
    os.chmod(path, 0o644)
    mod.save_token("nubi_key_other")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
