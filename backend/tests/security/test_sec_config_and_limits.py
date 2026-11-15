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


# ---------------------------------------------------------------------------
# Each rule on its own: which peer is the proxy
# ---------------------------------------------------------------------------


def _trusted(monkeypatch, peer, *, cidrs=None, fly=False):
    from app.middleware.ratelimit import _is_trusted_proxy

    monkeypatch.delenv("NUBI_TRUSTED_PROXY_CIDRS", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    if cidrs is not None:
        monkeypatch.setenv("NUBI_TRUSTED_PROXY_CIDRS", cidrs)
    if fly:
        monkeypatch.setenv("FLY_APP_NAME", "app")
    return _is_trusted_proxy(peer)


def test_an_explicit_proxy_list_is_the_whole_list(monkeypatch):
    # Named proxies only: a private peer outside the list is not the proxy,
    # even on the platform where private peers would otherwise count.
    assert _trusted(monkeypatch, "10.0.0.9", cidrs="10.0.0.0/8", fly=True) is True
    assert _trusted(monkeypatch, "172.19.0.2", cidrs="10.0.0.0/8", fly=True) is False
    assert _trusted(monkeypatch, "127.0.0.1", cidrs="10.0.0.0/8", fly=True) is False


def test_without_a_known_proxy_no_peer_is_trusted(monkeypatch):
    for peer in ("10.0.0.9", "172.19.0.2", "192.168.1.5", "127.0.0.1", "8.8.8.8", "not-an-address"):
        assert _trusted(monkeypatch, peer) is False, peer


def test_on_the_platform_private_and_loopback_peers_are_the_proxy(monkeypatch):
    assert _trusted(monkeypatch, "172.19.0.2", fly=True) is True
    assert _trusted(monkeypatch, "127.0.0.1", fly=True) is True
    assert _trusted(monkeypatch, "8.8.8.8", fly=True) is False


def test_a_malformed_proxy_list_entry_trusts_nobody(monkeypatch):
    assert _trusted(monkeypatch, "8.8.8.8", cidrs="not-a-cidr") is False
    assert _trusted(monkeypatch, "8.8.8.8", cidrs="not-a-cidr, 10.0.0.0/8") is False
    assert _trusted(monkeypatch, "10.1.2.3", cidrs="not-a-cidr, 10.0.0.0/8") is True


# ---------------------------------------------------------------------------
# Each rule on its own: the per-account sign-in allowance
# ---------------------------------------------------------------------------


@pytest.fixture
def account_limit(monkeypatch):
    from app.middleware import ratelimit

    monkeypatch.setattr(ratelimit._cfg, "_loaded", True, raising=False)
    monkeypatch.setattr(ratelimit._cfg, "enabled", True, raising=False)
    monkeypatch.setattr(ratelimit._cfg, "burst_factor", 1.0, raising=False)
    monkeypatch.setattr(ratelimit, "get_redis", lambda: None)
    monkeypatch.delenv("NUBI_RATELIMIT_LOGIN_PER_ACCOUNT_RPM", raising=False)
    ratelimit._buckets.clear()
    yield ratelimit
    ratelimit._buckets.clear()


def _attempts(ratelimit, email, n):
    return [ratelimit.consume_login_attempt(email)[0] for _ in range(n)]


def test_account_allowance_defaults_to_ten_a_minute(account_limit):
    results = _attempts(account_limit, "a@example.com", 12)
    assert results == [True] * 10 + [False, False]


@pytest.mark.parametrize("setting", ["not-a-number", "", "ten"])
def test_a_bad_allowance_setting_falls_back_to_the_default(account_limit, monkeypatch, setting):
    monkeypatch.setenv("NUBI_RATELIMIT_LOGIN_PER_ACCOUNT_RPM", setting)
    assert _attempts(account_limit, "a@example.com", 12) == [True] * 10 + [False, False]


def test_whitespace_and_casing_do_not_make_a_new_account(account_limit, monkeypatch):
    monkeypatch.setenv("NUBI_RATELIMIT_LOGIN_PER_ACCOUNT_RPM", "2")
    assert _attempts(account_limit, "victim@example.com", 2) == [True, True]
    for spelling in ("  victim@example.com", "victim@example.com \n", "VICTIM@example.com", " Victim@Example.Com "):
        assert account_limit.consume_login_attempt(spelling)[0] is False, spelling


def test_account_allowance_is_off_when_rate_limiting_is_off(account_limit, monkeypatch):
    monkeypatch.setenv("NUBI_RATELIMIT_LOGIN_PER_ACCOUNT_RPM", "1")
    monkeypatch.setattr(account_limit._cfg, "enabled", False, raising=False)
    assert _attempts(account_limit, "a@example.com", 5) == [True] * 5
    assert account_limit._buckets == {}  # nothing was counted


def test_account_allowance_uses_the_shared_store_when_there_is_one(account_limit, monkeypatch):
    seen: list = []

    def _shared(client, key, rpm, now):
        seen.append((client, key, rpm))
        return False, 42

    monkeypatch.setattr(account_limit, "get_redis", lambda: "shared-store")
    monkeypatch.setattr(account_limit, "_redis_consume", _shared)
    monkeypatch.setenv("NUBI_RATELIMIT_LOGIN_PER_ACCOUNT_RPM", "7")
    assert account_limit.consume_login_attempt("a@example.com") == (False, 42)
    client, key, rpm = seen[0]
    assert client == "shared-store" and rpm == 7
    assert key[1] == "login-account" and key[0].startswith("acct:") and "example" not in key[0]
    assert account_limit._buckets == {}  # the decision came from the shared store


def test_a_shared_store_outage_still_limits_the_account(account_limit, monkeypatch):
    def _down(*_a, **_k):
        raise ConnectionError("store unavailable")

    monkeypatch.setattr(account_limit, "get_redis", lambda: "shared-store")
    monkeypatch.setattr(account_limit, "_redis_consume", _down)
    monkeypatch.setenv("NUBI_RATELIMIT_LOGIN_PER_ACCOUNT_RPM", "2")
    assert _attempts(account_limit, "a@example.com", 4) == [True, True, False, False]


@pytest.mark.asyncio
async def test_login_refuses_an_account_over_its_allowance_before_any_lookup(monkeypatch):
    from unittest.mock import AsyncMock

    from app.errors import AppError
    from app.middleware import ratelimit
    from app.routes import auth

    lookups = AsyncMock(side_effect=AssertionError("the account was looked up"))
    asked: list[str] = []

    def _over(email):
        asked.append(email)
        return False, 30

    monkeypatch.setattr(ratelimit, "consume_login_attempt", _over)
    monkeypatch.setattr(auth, "fetchrow", lookups)
    monkeypatch.setattr(auth, "verify_password", lambda *a, **k: (_ for _ in ()).throw(AssertionError("hashed")))

    body = auth.LoginIn(email="Victim@Example.com", password="guess")
    with pytest.raises(AppError) as exc:
        await auth.login(body, _request(), None)
    assert exc.value.status == 429 and exc.value.code == "rate_limited"
    assert "30" in exc.value.message
    assert asked == ["victim@example.com"]
    lookups.assert_not_awaited()
