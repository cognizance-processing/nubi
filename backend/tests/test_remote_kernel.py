"""Tests for M4-REMOTE: E2B remote kernel runner, ModalRunner, _choose_runner,
and the /compute/run endpoint with a mocked E2B sandbox.

Coverage
--------
Unit tests (E2BRunner):
  1. Round-trip: E2BRunner.run writes inputs, runs harness, returns correct Arrow
     table with tier='remote_kernel'.
  2. stdout is captured from execution.logs.stdout and returned.
  3. Timeout → AppError("kernel_timeout", 504).
  4. SDK not installed (import fails) → AppError("kernel_unavailable", 503).
  5. Empty api_key → AppError("kernel_unavailable", 503).
  6. Execution.error is set → AppError("kernel_error", 400).
  7. Output file missing → AppError("kernel_error", 400).
  8. Output file too large → AppError("kernel_output_too_large", 413).

Unit tests (_choose_runner):
  9. KERNEL_REMOTE_PROVIDER=e2b + E2B_API_KEY set, ENV=production → E2BRunner.
  10. KERNEL_REMOTE_PROVIDER=e2b + E2B_API_KEY set, ENV=development → E2BRunner.
  11. No remote, ENV=development + KERNEL_LOCAL_ENABLED → LocalSubprocessRunner.
  12. No remote, ENV=production → AppError("kernel_disabled", 503).

Endpoint tests (POST /compute/run):
  13. Remote configured (mocked E2B) + first-party token → 200 + X-Nubi-Tier: remote_kernel.
  14. Embed token → 403 (unchanged security gate).
  15. No token → 401 (unchanged).

ModalRunner:
  16. No credentials → AppError("kernel_unavailable", 503).
  17. Modal not installed → AppError("kernel_unavailable", 503).

Mock strategy
-------------
``E2BRunner._get_sandbox_class()`` is the patchable indirection.  Tests
monkeypatch ``app.compute.remote_e2b._get_sandbox_class`` to return a
``FakeSandboxClass`` that records file writes, serves a pre-built Arrow IPC
blob for reads, and returns a ``FakeExecution`` with known stdout.
This means the real ``e2b`` package is NEVER imported — the suite runs
without it installed.
"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Environment bootstrap (before any app import)
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
_JWKS_KEY: dict = json.loads(_RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS_KEY["kid"] = "remote-test-key"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

_HOST_ISS = "https://remote-test-host.example"
_HOST_AUD = "nubi"
_EMBED_ORIGIN = "https://remote-test-host.example"
_KID = "remote-test-key"

# ---------------------------------------------------------------------------
# Arrow IPC helpers
# ---------------------------------------------------------------------------


def _table_to_ipc_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        for batch in table.to_batches():
            writer.write_batch(batch)
    return sink.getvalue().to_pybytes()


def _parse_arrow(content: bytes) -> pa.Table:
    return pa_ipc.open_stream(BytesIO(content)).read_all()


# ---------------------------------------------------------------------------
# Token minting helpers
# ---------------------------------------------------------------------------


def _mint_embed_token(scope: list[str] | None = None) -> str:
    if scope is None:
        scope = ["read:query"]
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": _HOST_ISS,
        "aud": _HOST_AUD,
        "sub": "embed-user-remote",
        "org": "acme",
        "roles": ["viewer"],
        "policies": {},
        "scope": scope,
        "embed_origin": _EMBED_ORIGIN,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=300)).timestamp()),
    }
    return _pyjwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": _KID})


def _mint_access_token(user_id: str = "remote-test-user") -> str:
    from app.auth.jwt import mint_access_token
    return mint_access_token(user_id)


# ---------------------------------------------------------------------------
# Fake E2B sandbox machinery
# ---------------------------------------------------------------------------


class FakeLogs:
    """Fake Execution.logs — mimics e2b_code_interpreter models.Logs."""

    def __init__(self, stdout: list[str] | None = None, stderr: list[str] | None = None):
        self.stdout = stdout or []
        self.stderr = stderr or []


class FakeExecution:
    """Fake Execution object returned by sbx.run_code()."""

    def __init__(
        self,
        stdout: list[str] | None = None,
        stderr: list[str] | None = None,
        error=None,
    ):
        self.logs = FakeLogs(stdout=stdout, stderr=stderr)
        self.error = error  # None → success; set to FakeExecutionError for failure
        self.text = "\n".join(stdout or [])


class FakeExecutionError:
    """Fake ExecutionError — mimics e2b_code_interpreter models.ExecutionError."""

    def __init__(self, name: str = "NameError", value: str = "undefined", traceback: str = ""):
        self.name = name
        self.value = value
        self.traceback = traceback


class FakeSandbox:
    """Fake E2B Sandbox instance.

    Records files written, serves a /tmp/out.arrow from a pre-set bytes blob,
    and returns a FakeExecution from run_code().
    """

    def __init__(
        self,
        *,
        out_arrow_bytes: bytes | None = None,
        execution: FakeExecution | None = None,
        run_code_raises: Exception | None = None,
        read_raises: Exception | None = None,
    ):
        self._files_written: dict[str, bytes] = {}
        self._out_arrow_bytes = out_arrow_bytes  # bytes to serve for /tmp/out.arrow
        self._execution = execution or FakeExecution(stdout=["hello"])
        self._run_code_raises = run_code_raises
        self._read_raises = read_raises
        self.killed = False
        self.files = self  # sbx.files == sbx (provides write/read methods)

    def write(self, path: str, data) -> None:
        """Record a file write."""
        if isinstance(data, bytes):
            self._files_written[path] = data
        else:
            self._files_written[path] = data.encode() if isinstance(data, str) else bytes(data)

    def read(self, path: str, format: str = "text"):
        """Return bytes for /tmp/out.arrow; raise if configured to."""
        if self._read_raises is not None:
            raise self._read_raises
        if path == "/tmp/out.arrow":
            if self._out_arrow_bytes is None:
                raise FileNotFoundError(f"{path} not found in fake sandbox")
            return self._out_arrow_bytes
        return b""

    def run_code(self, code: str, timeout=None) -> FakeExecution:
        """Return the pre-set execution or raise."""
        if self._run_code_raises is not None:
            raise self._run_code_raises
        return self._execution

    def kill(self) -> None:
        """Record that the sandbox was killed."""
        self.killed = True


def _make_fake_sandbox_class(
    *,
    out_table: pa.Table | None = None,
    stdout: list[str] | None = None,
    run_code_raises: Exception | None = None,
    execution_error: FakeExecutionError | None = None,
    out_arrow_bytes: bytes | None = None,
    read_raises: Exception | None = None,
) -> tuple[type, "FakeSandbox"]:
    """Build a FakeSandboxClass and a reference to the instance it will create.

    Returns
    -------
    (FakeSandboxClass, sandbox_ref_holder)
        FakeSandboxClass.create() sets sandbox_ref_holder.instance so tests can
        inspect the created sandbox after the call.
    """
    # Build the Arrow bytes for the output file if a table was provided.
    if out_table is not None and out_arrow_bytes is None:
        out_arrow_bytes = _table_to_ipc_bytes(out_table)

    execution = FakeExecution(
        stdout=stdout or [],
        error=execution_error,
    )

    # We need to capture the created instance for inspection.
    class _Holder:
        instance: FakeSandbox | None = None

    holder = _Holder()

    class FakeSandboxClass:
        @classmethod
        def create(cls, api_key=None, timeout=None, **kwargs):
            inst = FakeSandbox(
                out_arrow_bytes=out_arrow_bytes,
                execution=execution,
                run_code_raises=run_code_raises,
                read_raises=read_raises,
            )
            holder.instance = inst
            return inst

    return FakeSandboxClass, holder


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
    from unittest.mock import AsyncMock

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
# 1. E2BRunner — round-trip: writes inputs, parses output, returns correct table
# ===========================================================================


def test_e2b_runner_round_trip():
    """E2BRunner writes inputs as Arrow IPC and returns the expected table."""
    from app.compute.remote_e2b import E2BRunner

    input_table = pa.table({"n": pa.array([10, 20, 30], type=pa.int64())})
    expected_output = pa.table({"s": pa.array([60], type=pa.int64())})

    FakeSandboxClass, holder = _make_fake_sandbox_class(
        out_table=expected_output,
        stdout=["computing sum"],
    )

    with patch("app.compute.remote_e2b._get_sandbox_class", return_value=FakeSandboxClass):
        runner = E2BRunner(api_key="e2b-test-key", timeout_s=30)
        result = runner.run(
            code="import pyarrow.compute as pc; result = pa.table({'s': [pc.sum(inputs['input']['n']).as_py()]})",
            inputs={"input": input_table},
            timeout_s=30,
        )

    # Verify the result
    assert result.tier == "remote_kernel"
    assert result.table is not None
    assert result.table.to_pydict() == expected_output.to_pydict()
    assert result.elapsed_ms >= 0

    # Verify input was written to the sandbox filesystem
    sbx = holder.instance
    assert sbx is not None
    assert "/tmp/in_input.arrow" in sbx._files_written

    # Verify the written bytes are valid Arrow IPC for the input table
    written_bytes = sbx._files_written["/tmp/in_input.arrow"]
    roundtripped = pa_ipc.open_stream(BytesIO(written_bytes)).read_all()
    assert roundtripped.to_pydict() == input_table.to_pydict()

    # Verify sandbox was killed in finally
    assert sbx.killed is True


# ===========================================================================
# 2. E2BRunner — stdout captured
# ===========================================================================


def test_e2b_runner_stdout_captured():
    """Stdout lines from execution.logs.stdout are joined and returned."""
    from app.compute.remote_e2b import E2BRunner

    output_table = pa.table({"x": pa.array([1], type=pa.int64())})
    FakeSandboxClass, holder = _make_fake_sandbox_class(
        out_table=output_table,
        stdout=["line one", "line two", "line three"],
    )

    with patch("app.compute.remote_e2b._get_sandbox_class", return_value=FakeSandboxClass):
        runner = E2BRunner(api_key="e2b-test-key")
        result = runner.run(code="result = pa.table({'x': [1]})", inputs={}, timeout_s=30)

    assert "line one" in result.stdout
    assert "line two" in result.stdout
    assert "line three" in result.stdout


# ===========================================================================
# 3. E2BRunner — timeout → AppError kernel_timeout 504
# ===========================================================================


def test_e2b_runner_timeout_raises_504():
    """When run_code raises a timeout-like exception → AppError kernel_timeout 504."""
    from app.compute.remote_e2b import E2BRunner
    from app.errors import AppError

    class FakeTimeoutError(Exception):
        """Simulates e2b.exceptions.TimeoutException."""
        pass

    FakeSandboxClass, holder = _make_fake_sandbox_class(
        run_code_raises=FakeTimeoutError("Sandbox timeout exceeded"),
    )

    with patch("app.compute.remote_e2b._get_sandbox_class", return_value=FakeSandboxClass):
        runner = E2BRunner(api_key="e2b-test-key")
        with pytest.raises(AppError) as exc_info:
            runner.run(code="result = pa.table({'x': [1]})", inputs={}, timeout_s=5)

    err = exc_info.value
    assert err.code == "kernel_timeout"
    assert err.status == 504

    # Sandbox must still be killed in finally
    sbx = holder.instance
    assert sbx is not None
    assert sbx.killed is True


# ===========================================================================
# 4. E2BRunner — SDK not installed → AppError kernel_unavailable 503
# ===========================================================================


def test_e2b_runner_sdk_not_installed_503():
    """If e2b-code-interpreter is not installed → 503 kernel_unavailable."""
    from app.compute.remote_e2b import E2BRunner
    from app.errors import AppError

    def _raise_import_error():
        raise ImportError("No module named 'e2b_code_interpreter'")

    with patch("app.compute.remote_e2b._get_sandbox_class", side_effect=_raise_import_error):
        runner = E2BRunner(api_key="e2b-test-key")
        with pytest.raises(AppError) as exc_info:
            runner.run(code="result = pa.table({'x': [1]})", inputs={}, timeout_s=30)

    err = exc_info.value
    assert err.code == "kernel_unavailable"
    assert err.status == 503


# ===========================================================================
# 5. E2BRunner — empty api_key → AppError kernel_unavailable 503
# ===========================================================================


def test_e2b_runner_no_api_key_503():
    """Empty api_key → AppError kernel_unavailable 503 (before any SDK call)."""
    from app.compute.remote_e2b import E2BRunner
    from app.errors import AppError

    runner = E2BRunner(api_key="")
    with pytest.raises(AppError) as exc_info:
        runner.run(code="result = pa.table({'x': [1]})", inputs={}, timeout_s=30)

    err = exc_info.value
    assert err.code == "kernel_unavailable"
    assert err.status == 503


# ===========================================================================
# 6. E2BRunner — execution.error set → AppError kernel_error 400
# ===========================================================================


def test_e2b_runner_execution_error_400():
    """When execution.error is not None → AppError kernel_error 400."""
    from app.compute.remote_e2b import E2BRunner
    from app.errors import AppError

    FakeSandboxClass, holder = _make_fake_sandbox_class(
        out_table=pa.table({"x": pa.array([1])}),
        execution_error=FakeExecutionError(
            name="NameError", value="name 'result' is not defined"
        ),
    )

    with patch("app.compute.remote_e2b._get_sandbox_class", return_value=FakeSandboxClass):
        runner = E2BRunner(api_key="e2b-test-key")
        with pytest.raises(AppError) as exc_info:
            runner.run(code="x = 42  # forgot result", inputs={}, timeout_s=30)

    err = exc_info.value
    assert err.code == "kernel_error"
    assert err.status == 400
    assert "NameError" in err.message


# ===========================================================================
# 7. E2BRunner — output file missing → AppError kernel_error 400
# ===========================================================================


def test_e2b_runner_output_file_missing_400():
    """When /tmp/out.arrow cannot be read → AppError kernel_error 400."""
    from app.compute.remote_e2b import E2BRunner
    from app.errors import AppError

    FakeSandboxClass, holder = _make_fake_sandbox_class(
        read_raises=FileNotFoundError("/tmp/out.arrow not found"),
    )

    with patch("app.compute.remote_e2b._get_sandbox_class", return_value=FakeSandboxClass):
        runner = E2BRunner(api_key="e2b-test-key")
        with pytest.raises(AppError) as exc_info:
            runner.run(code="result = pa.table({'x': [1]})", inputs={}, timeout_s=30)

    err = exc_info.value
    assert err.code == "kernel_error"
    assert err.status == 400


# ===========================================================================
# 8. E2BRunner — output too large → AppError kernel_output_too_large 413
# ===========================================================================


def test_e2b_runner_output_too_large_413():
    """Output Arrow IPC bytes > 64 MiB → AppError kernel_output_too_large 413."""
    from app.compute.remote_e2b import E2BRunner, _OUTPUT_SIZE_CAP_BYTES
    from app.errors import AppError

    # Create a "file" that exceeds the cap.
    oversized_bytes = b"X" * (_OUTPUT_SIZE_CAP_BYTES + 1)

    FakeSandboxClass, holder = _make_fake_sandbox_class(
        out_arrow_bytes=oversized_bytes,
    )

    with patch("app.compute.remote_e2b._get_sandbox_class", return_value=FakeSandboxClass):
        runner = E2BRunner(api_key="e2b-test-key")
        with pytest.raises(AppError) as exc_info:
            runner.run(code="result = pa.table({'x': [1]})", inputs={}, timeout_s=30)

    err = exc_info.value
    assert err.code == "kernel_output_too_large"
    assert err.status == 413


# ===========================================================================
# 9. _choose_runner: KERNEL_REMOTE_PROVIDER=e2b + E2B_API_KEY, ENV=production
#    → E2BRunner (production now works!)
# ===========================================================================


def test_choose_runner_e2b_production():
    """With E2B configured, ENV=production → E2BRunner (no longer 503)."""
    from app.compute.remote_e2b import E2BRunner
    from app.routes.compute import _choose_runner

    env_patch = {
        "ENV": "production",
        "KERNEL_REMOTE_PROVIDER": "e2b",
        "E2B_API_KEY": "e2b-prod-key",
        "KERNEL_LOCAL_ENABLED": "false",
    }
    with patch.dict(os.environ, env_patch):
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            runner = _choose_runner()
        finally:
            get_settings.cache_clear()

    assert isinstance(runner, E2BRunner)
    assert runner._api_key == "e2b-prod-key"


# ===========================================================================
# 10. _choose_runner: KERNEL_REMOTE_PROVIDER=e2b + E2B_API_KEY, ENV=development
#     → E2BRunner (remote takes priority over local in all envs)
# ===========================================================================


def test_choose_runner_e2b_development():
    """With E2B configured, ENV=development → E2BRunner (remote preferred)."""
    from app.compute.remote_e2b import E2BRunner
    from app.routes.compute import _choose_runner

    env_patch = {
        "ENV": "development",
        "KERNEL_REMOTE_PROVIDER": "e2b",
        "E2B_API_KEY": "e2b-dev-key",
        "KERNEL_LOCAL_ENABLED": "true",
    }
    with patch.dict(os.environ, env_patch):
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            runner = _choose_runner()
        finally:
            get_settings.cache_clear()

    assert isinstance(runner, E2BRunner)


# ===========================================================================
# 11. _choose_runner: no remote, ENV=development + KERNEL_LOCAL_ENABLED
#     → LocalSubprocessRunner
# ===========================================================================


def test_choose_runner_local_dev():
    """No remote configured, dev env + local enabled → LocalSubprocessRunner."""
    from app.compute.runner import LocalSubprocessRunner
    from app.routes.compute import _choose_runner

    env_patch = {
        "ENV": "development",
        "KERNEL_LOCAL_ENABLED": "true",
        "KERNEL_REMOTE_PROVIDER": "",
        "E2B_API_KEY": "",
        "MODAL_TOKEN_ID": "",
        "MODAL_TOKEN_SECRET": "",
    }
    with patch.dict(os.environ, env_patch):
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            runner = _choose_runner()
        finally:
            get_settings.cache_clear()

    assert isinstance(runner, LocalSubprocessRunner)


# ===========================================================================
# 12. _choose_runner: no remote, ENV=production → AppError kernel_disabled 503
# ===========================================================================


def test_choose_runner_production_no_remote_503():
    """ENV=production + no remote configured → AppError kernel_disabled 503."""
    from app.errors import AppError
    from app.routes.compute import _choose_runner

    env_patch = {
        "ENV": "production",
        "KERNEL_LOCAL_ENABLED": "false",
        "KERNEL_REMOTE_PROVIDER": "",
        "E2B_API_KEY": "",
        "MODAL_TOKEN_ID": "",
        "MODAL_TOKEN_SECRET": "",
    }
    with patch.dict(os.environ, env_patch):
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            with pytest.raises(AppError) as exc_info:
                _choose_runner()
        finally:
            get_settings.cache_clear()

    err = exc_info.value
    assert err.code == "kernel_disabled"
    assert err.status == 503


# ===========================================================================
# 13. Endpoint: remote configured (mocked E2B) + first-party token → 200
#     + X-Nubi-Tier: remote_kernel
# ===========================================================================


@pytest.mark.asyncio
async def test_endpoint_remote_configured_returns_remote_tier(client):
    """POST /compute/run with E2B configured → 200 + X-Nubi-Tier: remote_kernel."""
    token = _mint_access_token()
    output_table = pa.table({"x": pa.array([7, 8, 9], type=pa.int64())})

    FakeSandboxClass, holder = _make_fake_sandbox_class(
        out_table=output_table,
        stdout=["from remote"],
    )

    env_patch = {
        "KERNEL_REMOTE_PROVIDER": "e2b",
        "E2B_API_KEY": "e2b-endpoint-test",
        "ENV": "test",
    }
    with patch.dict(os.environ, env_patch):
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            with patch("app.compute.remote_e2b._get_sandbox_class", return_value=FakeSandboxClass):
                resp = await client.post(
                    "/api/v1/compute/run",
                    json={"code": "result = pa.table({'x': [7, 8, 9]})", "timeout_s": 30},
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            get_settings.cache_clear()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.headers.get("x-nubi-tier") == "remote_kernel"
    assert resp.headers.get("content-type", "").startswith("application/vnd.apache.arrow.stream")

    # Parse the Arrow response
    result_table = _parse_arrow(resp.content)
    assert result_table.to_pydict() == output_table.to_pydict()


# ===========================================================================
# 14. Endpoint: embed token → 403 (unchanged security gate)
# ===========================================================================


@pytest.mark.asyncio
async def test_endpoint_embed_token_rejected_with_remote(client):
    """Embed token → 403 even when E2B is configured (unchanged security gate)."""
    token = _mint_embed_token(scope=["read:*", "edit:*"])

    env_patch = {
        "KERNEL_REMOTE_PROVIDER": "e2b",
        "E2B_API_KEY": "e2b-test-key",
        "ENV": "test",
    }
    with patch.dict(os.environ, env_patch):
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/compute/run",
                json={"code": "result = pa.table({'x': [1]})"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Origin": _EMBED_ORIGIN,
                },
            )
        finally:
            get_settings.cache_clear()

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# ===========================================================================
# 15. Endpoint: no token → 401
# ===========================================================================


@pytest.mark.asyncio
async def test_endpoint_no_token_401(client):
    """No Authorization header → 401."""
    resp = await client.post(
        "/api/v1/compute/run",
        json={"code": "result = pa.table({'x': [1]})"},
    )
    assert resp.status_code == 401


# ===========================================================================
# 16. ModalRunner — no credentials → AppError kernel_unavailable 503
# ===========================================================================


def test_modal_runner_no_credentials_503():
    """ModalRunner with empty credentials → AppError kernel_unavailable 503."""
    from app.compute.remote_modal import ModalRunner
    from app.errors import AppError

    runner = ModalRunner(token_id="", token_secret="")
    with pytest.raises(AppError) as exc_info:
        runner.run(code="result = pa.table({'x': [1]})", inputs={}, timeout_s=30)

    err = exc_info.value
    assert err.code == "kernel_unavailable"
    assert err.status == 503


# ===========================================================================
# 17. ModalRunner — modal SDK not installed → AppError kernel_unavailable 503
# ===========================================================================


def test_modal_runner_sdk_not_installed_503():
    """If modal package is not installed → AppError kernel_unavailable 503."""
    from app.compute.remote_modal import ModalRunner
    from app.errors import AppError

    def _raise_import_error():
        raise ImportError("No module named 'modal'")

    with patch("app.compute.remote_modal._get_modal_module", side_effect=_raise_import_error):
        runner = ModalRunner(token_id="tok-id", token_secret="tok-sec")
        with pytest.raises(AppError) as exc_info:
            runner.run(code="result = pa.table({'x': [1]})", inputs={}, timeout_s=30)

    err = exc_info.value
    assert err.code == "kernel_unavailable"
    assert err.status == 503


# ===========================================================================
# 18. E2BRunner — multiple inputs written correctly
# ===========================================================================


def test_e2b_runner_multiple_inputs_written():
    """Multiple input tables are each written to the correct /tmp/in_*.arrow path."""
    from app.compute.remote_e2b import E2BRunner

    table_a = pa.table({"a": pa.array([1, 2], type=pa.int64())})
    table_b = pa.table({"b": pa.array([3, 4], type=pa.int64())})
    output_table = pa.table({"ok": pa.array([1], type=pa.int64())})

    FakeSandboxClass, holder = _make_fake_sandbox_class(out_table=output_table)

    with patch("app.compute.remote_e2b._get_sandbox_class", return_value=FakeSandboxClass):
        runner = E2BRunner(api_key="e2b-test-key")
        result = runner.run(
            code="result = pa.table({'ok': [1]})",
            inputs={"alpha": table_a, "beta": table_b},
            timeout_s=30,
        )

    sbx = holder.instance
    assert "/tmp/in_alpha.arrow" in sbx._files_written
    assert "/tmp/in_beta.arrow" in sbx._files_written

    # Verify each written file is valid Arrow IPC for the correct table
    for name, expected in [("alpha", table_a), ("beta", table_b)]:
        path = f"/tmp/in_{name}.arrow"
        roundtripped = pa_ipc.open_stream(BytesIO(sbx._files_written[path])).read_all()
        assert roundtripped.to_pydict() == expected.to_pydict()


# ===========================================================================
# 19. E2BRunner — sandbox always killed even on error
# ===========================================================================


def test_e2b_runner_sandbox_killed_on_error():
    """Even when run_code raises a non-timeout error, sandbox.kill() is called."""
    from app.compute.remote_e2b import E2BRunner
    from app.errors import AppError

    class RandomSandboxError(Exception):
        pass

    FakeSandboxClass, holder = _make_fake_sandbox_class(
        run_code_raises=RandomSandboxError("unexpected sandbox error"),
    )

    with patch("app.compute.remote_e2b._get_sandbox_class", return_value=FakeSandboxClass):
        runner = E2BRunner(api_key="e2b-test-key")
        with pytest.raises(AppError):
            runner.run(code="result = pa.table({'x': [1]})", inputs={}, timeout_s=30)

    sbx = holder.instance
    assert sbx is not None
    assert sbx.killed is True


# ===========================================================================
# 20. ComputePlacementRouter — remote_configured=True routes native wheel cells
#     to 'remote_kernel' (confirms the router handles M4-REMOTE correctly)
# ===========================================================================


def test_router_native_wheel_remote_configured():
    """native_wheel + remote_configured=True → remote_kernel tier."""
    from app.compute.router import ComputePlacementRouter

    router = ComputePlacementRouter(remote_configured=True)
    assert router.place({"kind": "python", "needs_native_wheel": True}) == "remote_kernel"


def test_router_oversized_remote_configured():
    """est_rows > browser_cap + remote_configured=True → remote_kernel tier."""
    from app.compute.router import ComputePlacementRouter, _DEFAULT_BROWSER_ROW_CAP

    router = ComputePlacementRouter(remote_configured=True)
    assert (
        router.place({"kind": "python", "est_rows": _DEFAULT_BROWSER_ROW_CAP + 1})
        == "remote_kernel"
    )
