"""E2B remote kernel runner — M4-REMOTE (Wave M4R-A).

Executes user Python code inside an E2B Firecracker microVM sandbox, which
provides production-grade isolation:

* **No host network/IMDS access**: each sandbox runs in a Firecracker microVM
  with its own isolated network namespace.  The AWS Instance Metadata Service
  (169.254.169.254) and any RFC-1918 address on the Nubi host are unreachable
  from inside the VM.
* **No host filesystem access**: the VM has its own ephemeral root filesystem;
  the Nubi server's files, secrets, and credentials are invisible.
* **No host process visibility**: the VM kernel is separate; user code cannot
  see or signal Nubi server processes.

This is the **production code-execution path**.  Local subprocess execution
(``LocalSubprocessRunner``) is a development convenience only.

Enabling
--------
Set in the environment::

    KERNEL_REMOTE_PROVIDER=e2b
    E2B_API_KEY=e2b-...

Install the optional dependency::

    pip install e2b-code-interpreter

E2B SDK methods used (verified from e2b_code_interpreter v2.7.0 source)
-----------------------------------------------------------------------
``from e2b_code_interpreter import Sandbox``
``Sandbox.create(api_key=..., timeout=...)``
``sbx.files.write(path: str, data: bytes | str | IO) -> None``
``sbx.files.read(path: str, format: str) -> bytes``  (format='bytes' for binary)
``sbx.run_code(code: str, timeout: float | None) -> Execution``
``execution.logs.stdout: list[str]``
``execution.logs.stderr: list[str]``
``execution.error: ExecutionError | None``  (.name, .value, .traceback)
``sbx.kill() -> None``

Verification source: https://github.com/e2b-dev/code-interpreter (models.py,
code_interpreter_sync.py) and https://pypi.org/project/e2b-code-interpreter/.
"""

from __future__ import annotations

import textwrap
import time
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.ipc as pa_ipc

from app.connectors.arrow_io import table_to_ipc_bytes
from app.errors import AppError

if TYPE_CHECKING:  # resolves the string return annotation without a circular import
    from app.compute.runner import KernelResult

if TYPE_CHECKING:
    pass  # keep TYPE_CHECKING block for future type stubs

# ---------------------------------------------------------------------------
# Output size caps
# ---------------------------------------------------------------------------

_OUTPUT_SIZE_CAP_BYTES: int = 64 * 1024 * 1024   # 64 MiB for Arrow result
_STDOUT_CAP_BYTES: int = 1 * 1024 * 1024          # 1 MiB for captured stdout

# ---------------------------------------------------------------------------
# Harness template — mirrors LocalSubprocessRunner contract exactly.
# The harness is uploaded to the sandbox and executed via run_code().
#
# Contract:
#   - Input tables are pre-written to /tmp/in_<name>.arrow as Arrow IPC stream.
#   - The harness loads them into `inputs` dict (pyarrow Tables).
#   - `pa` (pyarrow) and `inputs` are available in the user code namespace.
#   - User code MUST assign `result` (pyarrow.Table or pandas.DataFrame).
#   - The harness writes the result to /tmp/out.arrow as Arrow IPC stream.
# ---------------------------------------------------------------------------

_HARNESS_TEMPLATE = textwrap.dedent(
    """\
    import sys
    import glob
    import pyarrow as pa
    import pyarrow.ipc as _pa_ipc

    # ── Load inputs ──────────────────────────────────────────────────────
    _input_paths = {input_paths!r}
    inputs = {{}}
    for _name, _path in _input_paths.items():
        with open(_path, 'rb') as _f:
            _reader = _pa_ipc.open_stream(_f)
            inputs[_name] = _reader.read_all()

    # ── Execute user code ─────────────────────────────────────────────
    _ns = {{'inputs': inputs, 'pa': pa}}
    exec(compile(_code, '<cell>', 'exec'), _ns)

    # ── Extract result ─────────────────────────────────────────────────
    _result = _ns.get('result')
    if _result is None:
        print("kernel_error: user code did not bind 'result'", file=sys.stderr)
        sys.exit(1)

    # Convert pandas DataFrame if possible.
    if not isinstance(_result, pa.Table):
        try:
            import pandas as _pd
            if isinstance(_result, _pd.DataFrame):
                _result = pa.Table.from_pandas(_result)
            else:
                print(
                    "kernel_error: 'result' must be a pyarrow.Table or pandas.DataFrame, "
                    f"got {{type(_result).__name__}}",
                    file=sys.stderr,
                )
                sys.exit(1)
        except ImportError:
            print(
                "kernel_error: 'result' is not a pyarrow.Table and pandas is not installed",
                file=sys.stderr,
            )
            sys.exit(1)

    # ── Write output Arrow IPC file ───────────────────────────────────
    _out_path = '/tmp/out.arrow'
    _sink = pa.BufferOutputStream()
    with _pa_ipc.new_stream(_sink, _result.schema) as _writer:
        for _batch in _result.to_batches():
            _writer.write_batch(_batch)
    _raw = _sink.getvalue().to_pybytes()
    with open(_out_path, 'wb') as _f:
        _f.write(_raw)
    """
)


# ---------------------------------------------------------------------------
# Patchable indirection — tested via monkeypatching _get_sandbox_class()
# ---------------------------------------------------------------------------


def _get_sandbox_class():
    """Return the E2B Sandbox class (lazy import).

    Separated into its own function so tests can monkeypatch it to inject a
    fake Sandbox class without needing to import the real e2b package.

    Raises
    ------
    ImportError
        If the ``e2b-code-interpreter`` package is not installed.
    """
    from e2b_code_interpreter import Sandbox  # noqa: PLC0415  (lazy import)

    return Sandbox


# ---------------------------------------------------------------------------
# E2BRunner
# ---------------------------------------------------------------------------


class E2BRunner:
    """Execute user code inside an E2B Firecracker microVM sandbox.

    SECURITY: E2B runs each sandbox in an isolated Firecracker microVM.
    The user code has NO access to Nubi's host filesystem, network interfaces,
    cloud IMDS (169.254.169.254), or process table.  This is the production-
    safe code-execution path.  See module docstring for full isolation details.

    Parameters
    ----------
    api_key:
        E2B API key (from ``E2B_API_KEY`` env var).
    timeout_s:
        Default sandbox session timeout in seconds.  Passed to ``Sandbox.create``.
    """

    tier: str = "remote_kernel"

    def __init__(self, api_key: str, timeout_s: int = 30) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s
        # Do NOT connect at construction — sandbox is created lazily in run().

    def run(
        self,
        code: str,
        inputs: dict[str, pa.Table],
        timeout_s: int,
    ) -> "KernelResult":  # type: ignore[name-defined]  # imported below
        """Run *code* in an E2B sandbox and return a :class:`KernelResult`.

        Steps
        -----
        1. Validate api_key and import e2b SDK (503 if missing).
        2. Create a fresh E2B sandbox with the given timeout.
        3. Write each input Arrow table to ``/tmp/in_<name>.arrow`` inside the VM.
        4. Build and run the harness code (same contract as LocalSubprocessRunner).
        5. Check for execution error → AppError 400.
        6. Read ``/tmp/out.arrow`` back from the VM, parse Arrow IPC.
        7. Enforce 64 MiB output-size cap → AppError 413.
        8. Capture stdout (cap at 1 MiB).
        9. Kill the sandbox in ``finally``.
        10. Return :class:`KernelResult` with tier='remote_kernel'.

        Raises
        ------
        AppError("kernel_unavailable", 503)
            If ``e2b-code-interpreter`` is not installed or ``api_key`` is empty.
        AppError("kernel_error", 400)
            If the user code raises an exception or does not assign ``result``.
        AppError("kernel_output_too_large", 413)
            If the output Arrow IPC exceeds 64 MiB.
        AppError("kernel_timeout", 504)
            If the sandbox execution times out.
        """
        from app.compute.runner import KernelResult  # avoid circular import

        # ── 0. Guard: api_key required ─────────────────────────────────────────
        if not self._api_key:
            raise AppError(
                "kernel_unavailable",
                "remote kernel (E2B) not configured/installed: E2B_API_KEY is empty.",
                503,
            )

        # ── 1. Lazy-import E2B SDK ────────────────────────────────────────────
        try:
            SandboxClass = _get_sandbox_class()
        except ImportError as exc:
            raise AppError(
                "kernel_unavailable",
                f"remote kernel (E2B) not configured/installed: {exc}",
                503,
            ) from exc

        start = time.monotonic()
        sbx = None
        try:
            # ── 2. Create sandbox ─────────────────────────────────────────────
            sbx = SandboxClass.create(
                api_key=self._api_key,
                timeout=timeout_s,
            )

            # ── 3. Write input Arrow tables into the sandbox filesystem ────────
            input_paths: dict[str, str] = {}
            for name, table in inputs.items():
                ipc_bytes = table_to_ipc_bytes(table)
                remote_path = f"/tmp/in_{name}.arrow"
                sbx.files.write(remote_path, ipc_bytes)
                input_paths[name] = remote_path

            # ── 4. Build and run harness ──────────────────────────────────────
            harness_body = _HARNESS_TEMPLATE.format(input_paths=input_paths)
            full_code = f"_code = {code!r}\n" + harness_body

            try:
                execution = sbx.run_code(full_code, timeout=float(timeout_s))
            except Exception as exc:
                # Catch E2B timeout exceptions and any other sandbox errors.
                # E2B raises TimeoutException (from e2b.exceptions) on timeout.
                exc_type = type(exc).__name__
                # Detect timeout by class name (works without importing e2b.exceptions).
                if "timeout" in exc_type.lower() or "timeout" in str(exc).lower():
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    raise AppError(
                        "kernel_timeout",
                        f"E2B sandbox execution timed out after {timeout_s}s.",
                        504,
                    ) from exc
                raise AppError(
                    "kernel_error",
                    f"E2B sandbox error ({exc_type}): {exc}",
                    400,
                ) from exc

            # ── 5. Check execution error ──────────────────────────────────────
            if execution.error is not None:
                err = execution.error
                # Sanitise: include error name + value but NOT the full traceback
                # (it may contain user-data details; traceback is still in logs).
                sanitized = f"{err.name}: {err.value}"
                raise AppError(
                    "kernel_error",
                    f"Kernel execution error — {sanitized}",
                    400,
                )

            # ── 6. Read output Arrow IPC back from the sandbox ─────────────────
            try:
                out_bytes: bytes = sbx.files.read("/tmp/out.arrow", format="bytes")
            except Exception as exc:
                raise AppError(
                    "kernel_error",
                    f"Kernel did not produce an output file: {exc}",
                    400,
                ) from exc

            # ── 7. Enforce output size cap ─────────────────────────────────────
            if len(out_bytes) > _OUTPUT_SIZE_CAP_BYTES:
                raise AppError(
                    "kernel_output_too_large",
                    f"Kernel output exceeds the "
                    f"{_OUTPUT_SIZE_CAP_BYTES // (1024 * 1024)} MiB cap "
                    f"({len(out_bytes)} bytes).",
                    413,
                )

            # ── 8. Parse Arrow IPC bytes → pyarrow.Table ──────────────────────
            try:
                import io as _io

                reader = pa_ipc.open_stream(_io.BytesIO(out_bytes))
                result_table = reader.read_all()
            except Exception as exc:
                raise AppError(
                    "kernel_error",
                    f"Failed to parse kernel output as Arrow IPC: {exc}",
                    400,
                ) from exc

            # ── 9. Capture stdout (capped at 1 MiB) ───────────────────────────
            raw_stdout_lines: list[str] = execution.logs.stdout or []
            stdout_text = "\n".join(raw_stdout_lines)
            if len(stdout_text.encode()) > _STDOUT_CAP_BYTES:
                # Truncate by character count (approximate; close enough for cap).
                stdout_text = stdout_text[: _STDOUT_CAP_BYTES] + "\n[... output truncated by nubi kernel cap ...]\n"

            elapsed_ms = int((time.monotonic() - start) * 1000)

            return KernelResult(
                table=result_table,
                stdout=stdout_text,
                tier="remote_kernel",
                elapsed_ms=elapsed_ms,
            )

        finally:
            # ── 10. Always kill the sandbox to free E2B resources ─────────────
            if sbx is not None:
                try:
                    sbx.kill()
                except Exception:
                    pass  # best-effort cleanup; do not mask the original error
