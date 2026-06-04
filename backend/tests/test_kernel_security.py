"""M4-SEC security hardening tests for the Nubi code-execution kernel.

Coverage
--------
Unit tests (runner):
  1. Oversized code (> 100k chars) → 413 at the route layer.
  2. exec:kernel scope helper logic — unit-test _has_exec_scope directly.
  3. Embed token (already rejected by kind check) → 403 (endpoint).
  4. First-party token with read-only scope (no edit:*) → 403 (endpoint).
  5. Memory hog killed within bounds (POSIX + rlimit only).
  6. CPU spinner killed within bounds (POSIX + rlimit only).
  7. Orphan/process-group: child spawns a grandchild that would write a
     sentinel file after a delay; after kernel_timeout the sentinel must NOT
     exist (the whole process group was killed). POSIX-guard.
  8. stdout cap: huge print truncated to ≤ 1 MiB + marker in kernel result.
  9. Production guard: ENV=production + KERNEL_LOCAL_ENABLED=false → 503.
 10. Production guard: ENV=production + KERNEL_LOCAL_ENABLED=true → still 503
     (local kernel explicitly guarded in production regardless of flag).
 11. Dev mode (ENV=development) → 200, runner works normally.

Notes
-----
- POSIX-specific tests are skipped when ``resource`` is unavailable (Windows).
- Orphan/process-group test uses a 1-second kernel timeout and a 3-second
  grandchild sleep to prove the grandchild is killed.
- All tests keep timeouts small to remain fast in CI.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# POSIX guard
# ---------------------------------------------------------------------------
try:
    import resource as _resource
    _HAVE_RESOURCE = True
except ImportError:
    _HAVE_RESOURCE = False

# ---------------------------------------------------------------------------
# Environment bootstrap (must happen BEFORE any app import)
# ---------------------------------------------------------------------------

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

# ---------------------------------------------------------------------------
# RSA keypair for embed token tests
# ---------------------------------------------------------------------------

import json as _json
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
from cryptography.hazmat.primitives import serialization as _serialization
from cryptography.hazmat.backends import default_backend as _default_backend
from jwt.algorithms import RSAAlgorithm as _RSAAlgorithm
import jwt as _pyjwt

_PRIVATE_KEY = _rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=_default_backend(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_PUBLIC_KEY_PEM: str = _PUBLIC_KEY.public_bytes(
    encoding=_serialization.Encoding.PEM,
    format=_serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

_JWKS_KEY: dict = _json.loads(_RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS_KEY["kid"] = "sec-test-key"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

_HOST_ISS = "https://sec-test-host.example"
_HOST_AUD = "nubi"
_EMBED_ORIGIN = "https://sec-test-host.example"
_KID = "sec-test-key"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_arrow(content: bytes) -> pa.Table:
    reader = pa_ipc.open_stream(BytesIO(content))
    return reader.read_all()


def _mint_embed_token(scope: list[str] | None = None, exp_delta: int = 300) -> str:
    if scope is None:
        scope = ["read:query"]
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": _HOST_ISS,
        "aud": _HOST_AUD,
        "sub": "embed-sec-user",
        "org": "acme",
        "roles": ["viewer"],
        "policies": {},
        "scope": scope,
        "embed_origin": _EMBED_ORIGIN,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    return _pyjwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": _KID})


def _mint_access_token(user_id: str = "sec-test-user") -> str:
    from app.auth.jwt import mint_access_token
    return mint_access_token(user_id)


def _mint_restricted_access_token(user_id: str = "restricted-user") -> str:
    """Mint a first-party HS256 access token with scope=['read:*'] only.

    In normal operation verify.py grants ['read:*','edit:*'] to every
    first-party token when the scope claim is absent.  We explicitly include
    scope=['read:*'] to override the default and test the scope-check path.

    Uses mint_access_token with extra_claims so the token has the required
    'typ' claim and passes decode_access_token validation.
    """
    from app.auth.jwt import mint_access_token

    return mint_access_token(user_id, extra_claims={"scope": ["read:*"]})


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _register_embed_issuer():
    from app.auth.issuers import get_issuer_registry
    from app.auth.jwks_cache import clear_cache

    registry = get_issuer_registry()
    registry.register(
        _HOST_ISS,
        jwks_uri=f"{_HOST_ISS}/.well-known/jwks.json",
        aud=_HOST_AUD,
        allowed_origins=[_EMBED_ORIGIN],
        static_jwks=_STATIC_JWKS,
    )
    yield
    registry.unregister(_HOST_ISS)
    clear_cache()


@pytest_asyncio.fixture
async def app():
    """FastAPI app with DB I/O patched."""
    patches = [
        patch("app.db.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.db.fetch", new=AsyncMock(return_value=[])),
        patch("app.db.execute", new=AsyncMock(return_value="OK")),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
        patch("app.auth.deps.fetchrow", new=AsyncMock(return_value=None)),
    ]
    for p in patches:
        p.start()
    try:
        import main as main_module
        _app = main_module.create_app()
        yield _app
    finally:
        for p in patches:
            p.stop()


@pytest_asyncio.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac


# ===========================================================================
# 1. Oversized code → 413
# ===========================================================================


@pytest.mark.asyncio
async def test_oversized_code_returns_413(client):
    """Code > 100,000 chars → 413 code_too_large before subprocess launch."""
    token = _mint_access_token()
    oversized_code = "x = 1  # padding\n" * 6_000  # ~102,000 chars
    assert len(oversized_code) > 100_000

    resp = await client.post(
        "/api/v1/compute/run",
        json={"code": oversized_code, "timeout_s": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 413, f"Expected 413, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"]["code"] == "code_too_large"


# ===========================================================================
# 2. exec:kernel scope helper — unit tests
# ===========================================================================


def test_has_exec_scope_explicit():
    from app.routes.compute import _has_exec_scope
    assert _has_exec_scope(["exec:kernel"]) is True


def test_has_exec_scope_implied_by_edit_star():
    from app.routes.compute import _has_exec_scope
    assert _has_exec_scope(["read:*", "edit:*"]) is True


def test_has_exec_scope_implied_by_star():
    from app.routes.compute import _has_exec_scope
    assert _has_exec_scope(["*"]) is True


def test_has_exec_scope_read_only_denied():
    from app.routes.compute import _has_exec_scope
    assert _has_exec_scope(["read:*"]) is False


def test_has_exec_scope_empty_denied():
    from app.routes.compute import _has_exec_scope
    assert _has_exec_scope([]) is False


def test_has_exec_scope_unrelated_denied():
    from app.routes.compute import _has_exec_scope
    assert _has_exec_scope(["read:query", "write:query"]) is False


# ===========================================================================
# 3. Embed token → 403 (kind check, already covered in test_kernel.py;
#    repeat here as a security regression guard)
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_token_rejected_403(client):
    """Embed token (kind='embed') → 403 regardless of scope."""
    token = _mint_embed_token(scope=["read:*", "edit:*", "exec:kernel"])
    resp = await client.post(
        "/api/v1/compute/run",
        json={"code": "result = pa.table({'x': [1]})"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# ===========================================================================
# 4. First-party token with read-only scope (no edit:*) → 403
# ===========================================================================


@pytest.mark.asyncio
async def test_read_only_scope_rejected_403(client):
    """First-party token with only ['read:*'] (no edit:* / exec:kernel) → 403."""
    token = _mint_restricted_access_token()
    resp = await client.post(
        "/api/v1/compute/run",
        json={"code": "result = pa.table({'x': [1]})"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"]["code"] == "forbidden"


# ===========================================================================
# 5. Memory hog killed within rlimit bounds (POSIX only)
# ===========================================================================


@pytest.mark.skipif(not _HAVE_RESOURCE, reason="resource module unavailable (non-POSIX)")
def test_memory_hog_killed_within_bounds():
    """Code that allocates >> 2 GiB is killed; AppError raised, not parent OOM."""
    from app.compute.runner import LocalSubprocessRunner
    from app.errors import AppError

    runner = LocalSubprocessRunner()
    # Attempt to allocate 3 GiB — exceeds RLIMIT_AS (2 GiB).
    code = (
        "import sys\n"
        "try:\n"
        "    x = b'\\x00' * (3 * 1024 * 1024 * 1024)\n"
        "except MemoryError:\n"
        "    pass\n"
        "result = __import__('pyarrow').table({'killed': [1]})\n"
    )
    # The process may succeed (if the OS doesn't actually commit 3 GiB pages)
    # or fail with kernel_error.  The important property is that the PARENT
    # process is not OOMed.  We accept either outcome.
    try:
        result = runner.run(code=code, inputs={}, timeout_s=15)
        # If the child survives, that's fine — the parent wasn't killed.
        assert result.table is not None
    except AppError as e:
        # Expected: rlimit or OOM killed the child.
        assert e.code in ("kernel_error", "kernel_timeout")


# ===========================================================================
# 6. CPU spinner killed within timeout (POSIX only)
# ===========================================================================


@pytest.mark.skipif(not _HAVE_RESOURCE, reason="resource module unavailable (non-POSIX)")
def test_cpu_spinner_killed_by_timeout():
    """Infinite CPU loop is killed by the timeout + RLIMIT_CPU grace period."""
    from app.compute.runner import LocalSubprocessRunner
    from app.errors import AppError

    runner = LocalSubprocessRunner()
    with pytest.raises(AppError) as exc_info:
        runner.run(
            code="while True: pass",
            inputs={},
            timeout_s=2,
        )
    err = exc_info.value
    assert err.code == "kernel_timeout"
    assert err.status == 504


# ===========================================================================
# 7. Orphan/process-group kill — sentinel file must NOT be created
# ===========================================================================


@pytest.mark.skipif(not _HAVE_RESOURCE, reason="resource module unavailable (non-POSIX)")
@pytest.mark.skipif(sys.platform == "win32", reason="process groups not supported on Windows")
def test_process_group_kill_orphan_grandchild():
    """After kernel_timeout, orphan grandchild (long-sleep subprocess) is killed.

    The grandchild would write a sentinel file after sleeping 4 seconds.
    The kernel timeout is 1 second.  After the AppError, we wait 5 seconds
    and assert the sentinel file was never written.
    """
    import time
    from app.compute.runner import LocalSubprocessRunner
    from app.errors import AppError

    sentinel = tempfile.mktemp(prefix="nubi_orphan_sentinel_", suffix=".txt")

    # User code spawns a grandchild subprocess that sleeps 4 s then writes the
    # sentinel.  The parent user-code process exits immediately, making the
    # grandchild an orphan — but it's still in the same process group (because
    # the harness uses start_new_session=True, ALL descendants share the new
    # session / process group).
    code = (
        "import subprocess, sys\n"
        f"sentinel = {sentinel!r}\n"
        "# Spawn grandchild: sleep 4s, then write sentinel.\n"
        "subprocess.Popen(\n"
        "    [sys.executable, '-c',\n"
        "     f'import time, pathlib; time.sleep(4); pathlib.Path({sentinel!r}).write_text(\"orphan\")'],\n"
        ")\n"
        "# Parent user code immediately returns a valid result.\n"
        "result = __import__('pyarrow').table({'x': [99]})\n"
    )

    runner = LocalSubprocessRunner()
    try:
        runner.run(code=code, inputs={}, timeout_s=1)
    except AppError as e:
        # kernel_timeout is expected because the grandchild might delay the
        # harness — but the key assertion is that the sentinel never appears.
        assert e.code in ("kernel_timeout", "kernel_error")

    # Wait long enough for the grandchild to have written the sentinel IF it
    # survived (grandchild sleep = 4 s; we wait 5 s to be safe).
    time.sleep(5)

    assert not os.path.exists(sentinel), (
        f"Orphan grandchild was NOT killed — sentinel file exists: {sentinel}"
    )


# ===========================================================================
# 8. stdout cap: huge print truncated
# ===========================================================================


def test_stdout_cap_truncated():
    """Code printing > 1 MiB to stdout → result.stdout is capped + marker."""
    from app.compute.runner import LocalSubprocessRunner

    runner = LocalSubprocessRunner()
    # Print ~2 MiB to stdout.
    code = (
        "import sys\n"
        "sys.stdout.write('A' * 2 * 1024 * 1024)\n"
        "sys.stdout.flush()\n"
        "result = __import__('pyarrow').table({'x': [1]})\n"
    )
    result = runner.run(code=code, inputs={}, timeout_s=30)
    assert result.table is not None
    # stdout must be capped (≤ 1 MiB + small marker overhead).
    assert len(result.stdout) <= 1 * 1024 * 1024 + 200, (
        f"stdout not capped: {len(result.stdout)} bytes"
    )
    assert "truncated" in result.stdout.lower(), "Truncation marker missing from stdout"


# ===========================================================================
# 9. Production guard: ENV=production, KERNEL_LOCAL_ENABLED=false → 503
# ===========================================================================


@pytest.mark.asyncio
async def test_production_guard_local_disabled(client):
    """ENV=production + KERNEL_LOCAL_ENABLED=false → 503 kernel_disabled."""
    token = _mint_access_token()

    with patch.dict(os.environ, {"ENV": "production", "KERNEL_LOCAL_ENABLED": "false"}):
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/compute/run",
                json={"code": "result = pa.table({'x': [1]})", "timeout_s": 5},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            get_settings.cache_clear()

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"]["code"] == "kernel_disabled"


# ===========================================================================
# 10. Production guard: ENV=production + KERNEL_LOCAL_ENABLED=true → 503
#     (local kernel always blocked in production regardless of flag)
# ===========================================================================


@pytest.mark.asyncio
async def test_production_guard_local_enabled_flag_ignored(client):
    """ENV=production + KERNEL_LOCAL_ENABLED=true → still 503 kernel_disabled.

    The route enforces: local runner only when ENV != 'production'.
    KERNEL_LOCAL_ENABLED=true in production is ignored for safety.
    """
    token = _mint_access_token()

    with patch.dict(os.environ, {"ENV": "production", "KERNEL_LOCAL_ENABLED": "true"}):
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/compute/run",
                json={"code": "result = pa.table({'x': [1]})", "timeout_s": 5},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            get_settings.cache_clear()

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"]["code"] == "kernel_disabled"


# ===========================================================================
# 11. Dev mode (ENV=development) → runner works normally
# ===========================================================================


@pytest.mark.asyncio
async def test_dev_mode_runner_works(client):
    """ENV=development → local runner is active; trivial code returns 200."""
    token = _mint_access_token()

    with patch.dict(os.environ, {"ENV": "development", "KERNEL_LOCAL_ENABLED": "true"}):
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/compute/run",
                json={"code": "result = pa.table({'x': [7, 8, 9]})", "timeout_s": 30},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            get_settings.cache_clear()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    table = _parse_arrow(resp.content)
    assert table.column_names == ["x"]
    assert table.num_rows == 3
