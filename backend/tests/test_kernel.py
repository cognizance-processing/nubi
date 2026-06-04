"""Tests for LocalSubprocessRunner, RemoteRunner, and POST /compute/run (M4-A).

Coverage
--------
Unit tests (runner):
  1. Round-trip: input table {n:[1,2,3]}, code ``result = inputs['input']`` → same table.
  2. Transform: sum via pyarrow.compute → correct aggregated table.
  3. Timeout: ``import time; time.sleep(5)`` with timeout_s=1 → AppError kernel_timeout.
  4. Missing ``result`` binding → AppError kernel_error.
  5. RemoteRunner.run() → AppError kernel_unavailable (503).

Endpoint tests (POST /compute/run):
  6. Embed token → 403 forbidden.
  7. First-party access token + code that passes through input_query_id='demo_all' → 200
     Arrow IPC with X-Nubi-Tier header set to 'local_kernel'.
  8. No token → 401.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio

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
# RSA keypair for embed token tests (module-level, generated once)
# ---------------------------------------------------------------------------

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from jwt.algorithms import RSAAlgorithm
import jwt as pyjwt

_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_PUBLIC_KEY_PEM: str = _PUBLIC_KEY.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

_JWKS_KEY: dict = json.loads(RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS_KEY["kid"] = "compute-test-key"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

_HOST_ISS = "https://compute-test-host.example"
_HOST_AUD = "nubi"
_EMBED_ORIGIN = "https://compute-test-host.example"
_KID = "compute-test-key"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_arrow(content: bytes) -> pa.Table:
    reader = pa_ipc.open_stream(BytesIO(content))
    return reader.read_all()


def _mint_embed_token(
    *,
    scope: list[str] | None = None,
    exp_delta: int = 300,
) -> str:
    if scope is None:
        scope = ["read:query"]
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": _HOST_ISS,
        "aud": _HOST_AUD,
        "sub": "embed-user-1",
        "org": "acme-org",
        "roles": ["viewer"],
        "policies": {},
        "scope": scope,
        "embed_origin": _EMBED_ORIGIN,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    return pyjwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": _KID})


def _mint_access_token(user_id: str = "test-user-id") -> str:
    """Mint a first-party HS256 access token using the app's jwt module."""
    from app.auth.jwt import mint_access_token

    return mint_access_token(user_id)


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
    """Register test embed issuer for endpoint tests, clean up after."""
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
    """FastAPI app with DB I/O patched (reuse conftest pattern)."""
    from unittest.mock import AsyncMock, patch

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
# 1. Round-trip: identity pass-through
# ===========================================================================


def test_local_runner_round_trip_identity():
    """Input table flows through unchanged when code assigns result = inputs['input']."""
    from app.compute.runner import LocalSubprocessRunner

    input_table = pa.table({"n": pa.array([1, 2, 3], type=pa.int64())})
    runner = LocalSubprocessRunner()
    result = runner.run(
        code="result = inputs['input']",
        inputs={"input": input_table},
        timeout_s=30,
    )
    assert result.tier == "local_kernel"
    assert result.elapsed_ms >= 0
    assert result.table is not None
    assert result.table.schema == input_table.schema
    assert result.table.to_pydict() == input_table.to_pydict()


# ===========================================================================
# 2. Transform: compute sum via pyarrow.compute
# ===========================================================================


def test_local_runner_sum_transform():
    """User code computes a sum; result table has the aggregated value."""
    from app.compute.runner import LocalSubprocessRunner

    input_table = pa.table({"n": pa.array([1, 2, 3], type=pa.int64())})
    code = (
        "import pyarrow.compute as pc\n"
        "result = pa.table({'s': [pc.sum(inputs['input']['n']).as_py()]})"
    )
    runner = LocalSubprocessRunner()
    result = runner.run(code=code, inputs={"input": input_table}, timeout_s=30)

    assert result.table is not None
    assert result.table.column_names == ["s"]
    assert result.table.column("s")[0].as_py() == 6


# ===========================================================================
# 3. Timeout
# ===========================================================================


def test_local_runner_timeout():
    """Sleeping code exceeds timeout → AppError kernel_timeout (504)."""
    from app.compute.runner import LocalSubprocessRunner
    from app.errors import AppError

    runner = LocalSubprocessRunner()
    with pytest.raises(AppError) as exc_info:
        runner.run(
            code="import time; time.sleep(10)",
            inputs={},
            timeout_s=1,
        )
    err = exc_info.value
    assert err.code == "kernel_timeout"
    assert err.status == 504


# ===========================================================================
# 4. Missing result binding
# ===========================================================================


def test_local_runner_missing_result():
    """Code that does not assign ``result`` → AppError kernel_error (400)."""
    from app.compute.runner import LocalSubprocessRunner
    from app.errors import AppError

    runner = LocalSubprocessRunner()
    with pytest.raises(AppError) as exc_info:
        runner.run(code="x = 42  # forgot to set result", inputs={}, timeout_s=30)
    err = exc_info.value
    assert err.code == "kernel_error"
    assert err.status == 400


# ===========================================================================
# 5. RemoteRunner raises 503
# ===========================================================================


def test_remote_runner_unconfigured_raises_503():
    """RemoteRunner with configured=False → AppError kernel_unavailable (503)."""
    from app.compute.runner import RemoteRunner
    from app.errors import AppError

    runner = RemoteRunner(configured=False)
    with pytest.raises(AppError) as exc_info:
        runner.run(code="result = pa.table({})", inputs={}, timeout_s=30)
    err = exc_info.value
    assert err.code == "kernel_unavailable"
    assert err.status == 503


# ===========================================================================
# 6. Endpoint: embed token → 403
# ===========================================================================


@pytest.mark.asyncio
async def test_compute_run_embed_token_forbidden(client):
    """Embed token → 403 forbidden; code execution requires a first-party session."""
    token = _mint_embed_token(scope=["read:*"])
    resp = await client.post(
        "/api/v1/compute/run",
        json={"code": "result = pa.table({'x': [1]})"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "forbidden"


# ===========================================================================
# 7. Endpoint: first-party access token + input_query_id → 200 Arrow IPC
# ===========================================================================


@pytest.mark.asyncio
async def test_compute_run_first_party_with_input_query(client):
    """First-party access token + input_query_id='demo_all' + passthrough code → 200.

    The response must be valid Arrow IPC and carry X-Nubi-Tier: local_kernel.
    """
    token = _mint_access_token()

    resp = await client.post(
        "/api/v1/compute/run",
        json={
            "code": "result = inputs['input']",
            "input_query_id": "demo_all",
            "timeout_s": 30,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.headers.get("x-nubi-tier") == "local_kernel"
    assert resp.headers.get("content-type", "").startswith(
        "application/vnd.apache.arrow.stream"
    )

    # Parse the Arrow IPC response and verify it has the demo table columns.
    table = _parse_arrow(resp.content)
    assert set(table.column_names) >= {"id", "name", "value", "active"}
    assert table.num_rows == 5  # demo table has 5 rows


@pytest.mark.asyncio
async def test_compute_run_first_party_no_inputs(client):
    """First-party access token, no input_query_id, trivial code → 200."""
    token = _mint_access_token()

    resp = await client.post(
        "/api/v1/compute/run",
        json={
            "code": "result = pa.table({'x': [1, 2, 3]})",
            "timeout_s": 30,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.headers.get("x-nubi-tier") == "local_kernel"

    table = _parse_arrow(resp.content)
    assert table.column_names == ["x"]
    assert table.num_rows == 3


# ===========================================================================
# 8. Endpoint: no token → 401
# ===========================================================================


@pytest.mark.asyncio
async def test_compute_run_no_token_returns_401(client):
    """No Authorization header → 401 unauthorized."""
    resp = await client.post(
        "/api/v1/compute/run",
        json={"code": "result = pa.table({'x': [1]})"},
    )
    assert resp.status_code == 401


# ===========================================================================
# Bonus: metering is recorded after a successful run
# ===========================================================================


@pytest.mark.asyncio
async def test_compute_run_records_metering(client):
    """After a successful run, record_kernel_usage should have appended an entry."""
    from app.compute.metering import get_usage, clear_usage

    clear_usage()
    token = _mint_access_token()

    resp = await client.post(
        "/api/v1/compute/run",
        json={"code": "result = pa.table({'x': [1]})", "timeout_s": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    usage = get_usage()
    assert len(usage) >= 1
    last = usage[-1]
    assert last["tier"] == "local_kernel"
    assert last["elapsed_ms"] >= 0
    assert last["output_bytes"] > 0
    clear_usage()
