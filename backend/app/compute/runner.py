"""On-demand kernel runner — M4-A (M4-SEC hardened).

Classes
-------
KernelResult
    Dataclass holding the result of a kernel execution.
KernelRunner
    Abstract base class for kernel runners.
LocalSubprocessRunner
    Executes user code in a fresh Python subprocess with a scrubbed env.
RemoteRunner
    Stub for Modal/E2B-style remote sandbox (raises 503 when unconfigured).

SECURITY NOTICE — RESIDUAL RISK (read before deploying)
---------------------------------------------------------
``LocalSubprocessRunner`` provides **dev-grade isolation only**.  Even with the
M4-SEC hardening applied (process-group kill, rlimits, output caps, env
scrubbing), the following risks remain:

* **Same OS user**: the child process runs as the same OS user as the web
  server; it can read any file the server user can read.
* **Host network access**: the child shares the host network namespace and can
  reach the cloud Instance Metadata Service (169.254.169.254 / fd00:ec2::254)
  and any RFC-1918 address reachable from the host.  An attacker can exfiltrate
  cloud IAM credentials from IMDS unless an egress firewall blocks link-local
  and RFC-1918 ranges.
* **No cgroup isolation**: rlimits are per-process; a fork-bomb child can still
  exhaust kernel thread limits before RLIMIT_NPROC kicks in.
* **Filesystem**: secrets are NOT passed via the subprocess env (DATABASE_URL,
  JWT_SECRET, GOOGLE_*, etc. are excluded — see ``_build_safe_env``), but any
  file the OS user can read is accessible via the filesystem.

**Production MUST use RemoteRunner** (Modal, E2B, gVisor, or equivalent
container-level sandbox) combined with an egress firewall that blocks:
  - 169.254.0.0/16  (link-local / AWS IMDS)
  - fd00:ec2::/32   (IPv6 IMDS)
  - 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16  (RFC-1918)

``LocalSubprocessRunner`` is a **development convenience and a testbed** for the
Arrow IPC protocol.  It is disabled in production unless
``KERNEL_LOCAL_ENABLED=true`` is explicitly set AND ``ENV != 'production'``.
The route layer enforces this guard and raises 503 when violated.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import textwrap
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pyarrow as pa
import pyarrow.ipc as pa_ipc

from app.compute import sandbox as _sandbox
from app.compute.sandbox import run_sandboxed
from app.errors import AppError

# ---------------------------------------------------------------------------
# Shared M4-SEC hardening (process-group kill, rlimits, output caps) lives in
# app/compute/sandbox.py so flows Python cells share the EXACT same code path.
# The aliases below preserve this module's historical names; limits are
# env-overridable via KERNEL_RLIMIT_* / KERNEL_STD{OUT,ERR}_CAP_BYTES — see
# the sandbox module docstring.
# ---------------------------------------------------------------------------
_HAVE_RESOURCE = _sandbox.HAVE_RESOURCE

# Output size cap for captured stdout/stderr: 1 MiB each.
# Buffers beyond this limit are truncated with a marker so a huge print() call
# cannot OOM the parent process.
#
# NOTE: with Popen.communicate() the OS collects the full output before we can
# truncate; for very large outputs the child must produce the data before the
# parent cap fires.  This is acceptable for MVP — a future hardening pass
# should replace communicate() with incremental reads.
_STDOUT_CAP_BYTES: int = _sandbox.STDOUT_CAP_BYTES   # 1 MiB
_STDERR_CAP_BYTES: int = _sandbox.STDERR_CAP_BYTES   # 1 MiB

# ---------------------------------------------------------------------------
# Output size cap: 64 MiB for the Arrow IPC result file.
# ---------------------------------------------------------------------------
_OUTPUT_SIZE_CAP_BYTES: int = 64 * 1024 * 1024  # 64 MiB

# ---------------------------------------------------------------------------
# Tail length for stderr messages surfaced in errors (to avoid huge payloads).
# ---------------------------------------------------------------------------
_STDERR_TAIL_CHARS: int = 2000

# ---------------------------------------------------------------------------
# rlimit defaults (POSIX only) — shared with sandbox.py.
# ---------------------------------------------------------------------------
# CPU time: timeout_s + 2 s grace; kernel raises SIGXCPU when exceeded.
_RLIMIT_CPU_GRACE_S: int = _sandbox.RLIMIT_CPU_GRACE_S
# Address space: 2 GiB — prevents runaway memory allocation.
_RLIMIT_AS_BYTES: int = _sandbox.RLIMIT_AS_BYTES
# Maximum file size writable: 128 MiB — prevents filling the disk.
_RLIMIT_FSIZE_BYTES: int = _sandbox.RLIMIT_FSIZE_BYTES
# Maximum number of child processes/threads: 64 — contains fork bombs.
_RLIMIT_NPROC: int = _sandbox.RLIMIT_NPROC


# ---------------------------------------------------------------------------
# KernelResult
# ---------------------------------------------------------------------------


@dataclass
class KernelResult:
    """Result of a kernel execution.

    Attributes
    ----------
    table:
        The Arrow table returned by the user code (``result`` binding).
        ``None`` if the runner does not produce a table (should not happen for
        ``LocalSubprocessRunner`` — it raises on missing ``result``).
    stdout:
        Any text printed to stdout by the user code (capped at 1 MiB).
    tier:
        The compute tier that ran this code (e.g. ``"local_kernel"``).
    elapsed_ms:
        Wall-clock milliseconds measured by the runner (``time.monotonic``).
    """

    table: Optional[pa.Table]
    stdout: str
    tier: str
    elapsed_ms: int


# ---------------------------------------------------------------------------
# KernelRunner ABC
# ---------------------------------------------------------------------------


class KernelRunner(ABC):
    """Abstract base class for all kernel runners."""

    @abstractmethod
    def run(
        self,
        code: str,
        inputs: dict[str, pa.Table],
        timeout_s: int,
    ) -> KernelResult:
        """Execute *code* with *inputs* and return a :class:`KernelResult`.

        Parameters
        ----------
        code:
            Python source code to execute.  The code must assign a
            ``pyarrow.Table`` (or pandas DataFrame) to the name ``result``.
        inputs:
            Mapping of name → Arrow table.  The code can access these via
            the ``inputs`` dict that is injected into its namespace.
            ``pa`` is also available as an alias for ``pyarrow``.
        timeout_s:
            Hard wall-clock timeout in seconds.  On expiry the runner kills
            the subprocess and raises ``AppError("kernel_timeout", 504)``.

        Returns
        -------
        KernelResult

        Raises
        ------
        AppError
            Various codes depending on failure mode (see subclass docs).
        """


# ---------------------------------------------------------------------------
# Environment scrubbing helpers
# ---------------------------------------------------------------------------

def _build_safe_env() -> dict[str, str]:
    """Build a minimal, scrubbed environment for the subprocess.

    Strategy
    --------
    Start from an EMPTY dict (do NOT copy ``os.environ``).  Add back only the
    small set of env vars that Python needs to run and locate packages:

    * ``PATH``  — needed to find the Python binary and standard tools.
    * ``PYTHONPATH`` — forwarded so the subprocess can import ``pyarrow`` from
      the same virtualenv / site-packages as the parent process.
    * ``HOME``  — some libraries (e.g. numba cache) write to ``~``; forwarding
      it limits file access to the server user's home but prevents crashes.
    * ``TMPDIR`` / ``TEMP`` / ``TMP`` — may be needed by C extensions for temp
      files; forward the platform default only.
    * ``LANG`` / ``LC_ALL`` — locale settings to avoid codec errors on some
      platforms.

    Explicitly EXCLUDED (never forwarded)
    --------------------------------------
    ``DATABASE_URL``, ``JWT_SECRET``, ``GOOGLE_CLIENT_ID``,
    ``GOOGLE_CLIENT_SECRET``, ``GOOGLE_REDIRECT_URI``, any var whose name
    starts with ``AWS_``, ``AZURE_``, ``GCP_``, ``OPENAI_``, ``ANTHROPIC_``.

    This list is intentionally conservative.  If a library the user code
    imports needs an additional env var it will raise an ImportError or
    similar — that is the safe failure mode.
    """
    _FORWARD = {
        "PATH",
        "PYTHONPATH",
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONUTF8",
        "VIRTUAL_ENV",
    }

    # Build PYTHONPATH from the parent's sys.path so pyarrow is importable.
    # This is safe: it only affects module search, not secrets.
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    # Include site-packages directories from the running interpreter.
    site_paths = [p for p in sys.path if p and "site-packages" in p]
    combined = ":".join(filter(None, [existing_pythonpath] + site_paths))

    env: dict[str, str] = {}
    for key in _FORWARD:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    # Override PYTHONPATH with the combined value so pyarrow is always findable.
    if combined:
        env["PYTHONPATH"] = combined

    return env


# ---------------------------------------------------------------------------
# Harness code builder
# ---------------------------------------------------------------------------

_HARNESS_TEMPLATE = textwrap.dedent(
    """\
    import sys
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
    _out_path = {output_path!r}
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
# LocalSubprocessRunner
# ---------------------------------------------------------------------------


class LocalSubprocessRunner(KernelRunner):
    """Execute user code in a fresh Python subprocess.

    SECURITY: dev-grade isolation only.  See module-level docstring for the
    full residual-risk statement.

    M4-SEC hardening applied
    ------------------------
    * **Process-group kill**: the child is launched with ``start_new_session=True``
      so it becomes a new process-group leader.  On timeout, ``os.killpg`` kills
      the ENTIRE group (including orphan grandchildren).  Falls back to
      ``proc.kill()`` on non-POSIX.
    * **rlimits** (POSIX only): CPU, AS, FSIZE, NPROC limits are applied via
      ``preexec_fn`` to contain fork bombs and memory hogs.
    * **Output caps**: stdout and stderr captured from the child are truncated
      to 1 MiB each with a marker — prevents a huge ``print()`` from OOMing
      the parent.
    * **Temp cleanup**: the temp directory is removed on ALL paths (success,
      error, timeout) via ``try/finally``.
    * **Env scrubbing**: secrets are never passed to the child (see
      ``_build_safe_env``).

    Parameters
    ----------
    (none — stateless; all configuration is per-call)
    """

    def run(
        self,
        code: str,
        inputs: dict[str, pa.Table],
        timeout_s: int,
    ) -> KernelResult:
        """Run *code* in a subprocess, passing *inputs* as Arrow IPC files.

        Steps
        -----
        1. Create a fresh temp directory.
        2. Write each input table to a named Arrow IPC file in the temp dir.
        3. Build a self-contained harness Python script that loads the inputs,
           execs the user code, and writes ``result`` to an output IPC file.
        4. Launch ``sys.executable harness.py`` with a SCRUBBED env and a hard
           timeout, in a new process group/session (``start_new_session=True``).
        5. On timeout, kill the ENTIRE process group (orphan grandchildren too).
        6. Truncate captured stdout/stderr to 1 MiB each.
        7. Read the output IPC file and return a :class:`KernelResult`.
        8. Always clean up the temp directory (finally block).

        Raises
        ------
        AppError("kernel_timeout", 504)
            If the subprocess exceeds *timeout_s* seconds.
        AppError("kernel_output_too_large", 413)
            If the output Arrow IPC file exceeds 64 MiB.
        AppError("kernel_error", 400)
            If the subprocess exits with a non-zero code.
        """
        start = time.monotonic()
        tmp_dir = tempfile.mkdtemp(prefix="nubi_kernel_")
        try:
            return self._run_in_tmpdir(code, inputs, timeout_s, tmp_dir, start)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Internal implementation (split for readability)
    # ------------------------------------------------------------------

    def _run_in_tmpdir(
        self,
        code: str,
        inputs: dict[str, pa.Table],
        timeout_s: int,
        tmp_dir: str,
        start: float,
    ) -> KernelResult:
        # ── 1. Write input tables to temp IPC files ───────────────────
        input_paths: dict[str, str] = {}
        for name, table in inputs.items():
            ipc_path = os.path.join(tmp_dir, f"input_{name}.arrow")
            sink = pa.BufferOutputStream()
            with pa_ipc.new_stream(sink, table.schema) as writer:
                for batch in table.to_batches():
                    writer.write_batch(batch)
            with open(ipc_path, "wb") as f:
                f.write(sink.getvalue().to_pybytes())
            input_paths[name] = ipc_path

        # ── 2. Build harness script ────────────────────────────────────
        output_path = os.path.join(tmp_dir, "output.arrow")
        harness_path = os.path.join(tmp_dir, "harness.py")

        harness_body = _HARNESS_TEMPLATE.format(
            input_paths=input_paths,
            output_path=output_path,
        )
        # Embed user code as a literal string inside the harness.
        # We use repr() to safely escape newlines / quotes.
        harness_source = f"_code = {code!r}\n" + harness_body

        with open(harness_path, "w", encoding="utf-8") as f:
            f.write(harness_source)

        # ── 3. Build scrubbed environment ─────────────────────────────
        safe_env = _build_safe_env()

        # ── 4–7. Launch via the shared hardened sandbox ───────────────
        # run_sandboxed applies: start_new_session=True (new process group so
        # os.killpg can kill the entire subtree on timeout), rlimits via
        # preexec_fn (POSIX only), process-GROUP SIGKILL on timeout, and
        # byte-level stdout/stderr truncation (1 MiB caps + marker).
        run = run_sandboxed(
            [sys.executable, harness_path],
            env=safe_env,
            cwd=tmp_dir,
            timeout_s=timeout_s,
            cpu_limit_s=timeout_s + _RLIMIT_CPU_GRACE_S,
            stdout_cap=_STDOUT_CAP_BYTES,
            stderr_cap=_STDERR_CAP_BYTES,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if run.timed_out:
            raise AppError(
                "kernel_timeout",
                f"Kernel execution timed out after {timeout_s}s.",
                504,
            )

        stdout_text: str = run.stdout.decode("utf-8", errors="replace")
        stderr_text: str = run.stderr.decode("utf-8", errors="replace")

        # ── 8. Check exit code ─────────────────────────────────────────
        if run.returncode != 0:
            stderr_tail = stderr_text[-_STDERR_TAIL_CHARS:]
            raise AppError(
                "kernel_error",
                f"Kernel exited with code {run.returncode}. "
                f"stderr: {stderr_tail}",
                400,
            )

        # ── 9. Enforce output size cap ─────────────────────────────────
        try:
            output_size = os.path.getsize(output_path)
        except OSError:
            raise AppError(
                "kernel_error",
                "Kernel did not produce an output file.",
                400,
            )

        if output_size > _OUTPUT_SIZE_CAP_BYTES:
            raise AppError(
                "kernel_output_too_large",
                f"Kernel output exceeds the {_OUTPUT_SIZE_CAP_BYTES // (1024 * 1024)} MiB cap "
                f"({output_size} bytes).",
                413,
            )

        # ── 10. Read output table ──────────────────────────────────────
        with open(output_path, "rb") as f:
            reader = pa_ipc.open_stream(f)
            result_table = reader.read_all()

        return KernelResult(
            table=result_table,
            stdout=stdout_text,
            tier="local_kernel",
            elapsed_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# RemoteRunner (stub)
# ---------------------------------------------------------------------------


class RemoteRunner(KernelRunner):
    """Stub for a Modal/E2B-style remote sandbox (M4-A placeholder).

    When ``configured=False`` (the default), any call to ``run()`` raises
    ``AppError("kernel_unavailable", 503)``.  A future M4-B / M4-C agent will
    replace the body with real Modal/E2B client calls.

    Parameters
    ----------
    configured:
        Set to ``True`` only when the remote infrastructure is actually
        provisioned and all required credentials are available.
    """

    def __init__(self, configured: bool = False) -> None:
        self.configured = configured

    def run(
        self,
        code: str,
        inputs: dict[str, pa.Table],
        timeout_s: int,
    ) -> KernelResult:
        """Raise 503 — remote kernel is not yet configured.

        Raises
        ------
        AppError("kernel_unavailable", 503)
            Always, until a real remote runner is wired in.
        """
        raise AppError(
            "kernel_unavailable",
            "Remote kernel is not configured.  "
            "Set up Modal/E2B credentials and pass configured=True.",
            503,
        )
