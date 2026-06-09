"""Shared subprocess sandbox hardening (M4-SEC) — single source of truth.

This module extracts the hardening that ``LocalSubprocessRunner``
(app/compute/runner.py) applies to kernel subprocesses so that other
subprocess-based executors (e.g. Flows Python cells in
app/flows/registry.py ``_handle_python``) share the EXACT same protections
instead of re-implementing (or omitting) them:

* **Process-group kill**: children are launched with ``start_new_session=True``
  so they become a new process-group leader; on timeout the ENTIRE group is
  killed via ``os.killpg(SIGKILL)`` (reaps orphan grandchildren).  Falls back
  to ``proc.kill()`` on platforms without ``killpg``.
* **rlimits (POSIX only)**: ``RLIMIT_CPU`` (timeout + grace), ``RLIMIT_AS``
  (2 GiB, with the macOS skip-if-unsupported guard), ``RLIMIT_FSIZE``
  (128 MiB) and ``RLIMIT_NPROC`` (64) applied via ``preexec_fn``.
  When no CPU budget is supplied (``cpu_limit_s=None`` — e.g. a flow cell
  configured with ``timeout_s=0`` meaning "no timeout"), ``RLIMIT_CPU`` is
  skipped so legitimate long CPU work is not silently killed, but the
  memory / file-size / nproc caps still apply.
* **Output caps**: captured stdout/stderr are truncated to 1 MiB each (bytes,
  before decoding) with a clear marker, so a huge ``print()`` cannot OOM the
  parent process.

Limits are overridable via environment variables (read once at import time,
matching the ``KERNEL_*`` naming convention used elsewhere, e.g.
``KERNEL_LOCAL_ENABLED``):

* ``KERNEL_RLIMIT_CPU_GRACE_S``  — CPU grace seconds added to the timeout (2)
* ``KERNEL_RLIMIT_AS_BYTES``     — address-space cap (2 GiB)
* ``KERNEL_RLIMIT_FSIZE_BYTES``  — max writable file size (128 MiB)
* ``KERNEL_RLIMIT_NPROC``        — max processes/threads (64)
* ``KERNEL_STDOUT_CAP_BYTES``    — captured stdout cap (1 MiB)
* ``KERNEL_STDERR_CAP_BYTES``    — captured stderr cap (1 MiB)

SECURITY NOTICE: this is **dev-grade isolation only** — same OS user, host
network namespace, no cgroups.  See app/compute/runner.py module docstring
and docs/kernel-security.md for the full residual-risk statement.
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

# ---------------------------------------------------------------------------
# Try to import resource (POSIX only — not available on Windows).
# ---------------------------------------------------------------------------
try:
    import resource as _resource
    HAVE_RESOURCE = True
except ImportError:
    _resource = None  # type: ignore[assignment]
    HAVE_RESOURCE = False


def _int_env(name: str, default: int) -> int:
    """Read an integer env override; fall back to *default* on absence/garbage."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Limits (env-overridable; defaults match the original M4-SEC values in
# runner.py so behavior is unchanged when no override is set).
# ---------------------------------------------------------------------------
# CPU time: timeout_s + grace; kernel raises SIGXCPU when exceeded.
RLIMIT_CPU_GRACE_S: int = _int_env("KERNEL_RLIMIT_CPU_GRACE_S", 2)
# Address space: 2 GiB — prevents runaway memory allocation.
RLIMIT_AS_BYTES: int = _int_env("KERNEL_RLIMIT_AS_BYTES", 2 * 1024 * 1024 * 1024)
# Maximum file size writable: 128 MiB — prevents filling the disk.
RLIMIT_FSIZE_BYTES: int = _int_env("KERNEL_RLIMIT_FSIZE_BYTES", 128 * 1024 * 1024)
# Maximum number of child processes/threads: 64 — contains fork bombs.
RLIMIT_NPROC: int = _int_env("KERNEL_RLIMIT_NPROC", 64)
# Captured output caps: 1 MiB each.
STDOUT_CAP_BYTES: int = _int_env("KERNEL_STDOUT_CAP_BYTES", 1 * 1024 * 1024)
STDERR_CAP_BYTES: int = _int_env("KERNEL_STDERR_CAP_BYTES", 1 * 1024 * 1024)

# Marker appended (as bytes, pre-decode) when a captured stream is truncated.
TRUNCATION_MARKER: bytes = b"\n[... output truncated by nubi kernel cap ...]\n"


# ---------------------------------------------------------------------------
# rlimit preexec_fn (POSIX only)
# ---------------------------------------------------------------------------

def make_rlimit_preexec(cpu_limit_s: Optional[int]) -> Optional[Callable[[], None]]:
    """Return a preexec_fn applying conservative rlimits, or None on non-POSIX.

    Parameters
    ----------
    cpu_limit_s:
        CPU-seconds budget for ``RLIMIT_CPU`` (callers typically pass
        ``timeout_s + RLIMIT_CPU_GRACE_S``).  Pass ``None`` to SKIP the CPU
        limit (used when a task has no wall-clock timeout — long CPU work must
        not be silently killed); the AS / FSIZE / NPROC caps still apply.

    Limits applied
    --------------
    RLIMIT_CPU
        CPU seconds (*cpu_limit_s*).  Gives the kernel a chance to raise
        SIGXCPU if the wall-clock timeout kill is delayed.  Skipped when
        *cpu_limit_s* is ``None``.
    RLIMIT_AS
        Address-space (virtual memory): ``RLIMIT_AS_BYTES`` (2 GiB).
        **macOS caveat**: on macOS ``RLIMIT_AS`` is typically set to
        ``RLIM_INFINITY`` and the kernel rejects any hard cap lower than the
        current soft limit, raising ``ValueError``.  We catch this and
        silently skip the AS limit on platforms where it cannot be applied.
    RLIMIT_FSIZE
        Maximum writable file size: ``RLIMIT_FSIZE_BYTES`` (128 MiB).
    RLIMIT_NPROC
        Maximum simultaneous processes/threads: ``RLIMIT_NPROC`` (64).
        Counts across all processes owned by the OS user, not just the
        subtree.  Only applied if the requested value is within the OS hard cap.
    """
    if not HAVE_RESOURCE:
        return None

    def _preexec() -> None:
        # ── RLIMIT_CPU ────────────────────────────────────────────────────────
        if cpu_limit_s is not None:
            try:
                _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_limit_s, cpu_limit_s))
            except (ValueError, OSError):
                pass  # platform does not allow lowering CPU limit

        # ── RLIMIT_AS (address space) ────────────────────────────────────────
        # macOS ships with RLIMIT_AS == RLIM_INFINITY; setting a finite cap
        # fails with ValueError ("current limit exceeds maximum limit") unless
        # the hard limit is also unlimited.  We skip gracefully.
        try:
            _cur_soft, cur_hard = _resource.getrlimit(_resource.RLIMIT_AS)
            if cur_hard == _resource.RLIM_INFINITY or cur_hard >= RLIMIT_AS_BYTES:
                _resource.setrlimit(
                    _resource.RLIMIT_AS,
                    (RLIMIT_AS_BYTES, RLIMIT_AS_BYTES),
                )
            # If cur_hard < RLIMIT_AS_BYTES the existing cap is already tighter.
        except (ValueError, OSError):
            pass  # platform cannot apply AS limit (e.g. macOS)

        # ── RLIMIT_FSIZE ──────────────────────────────────────────────────────
        try:
            _resource.setrlimit(
                _resource.RLIMIT_FSIZE,
                (RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES),
            )
        except (ValueError, OSError):
            pass

        # ── RLIMIT_NPROC ──────────────────────────────────────────────────────
        try:
            _cur_soft, cur_hard = _resource.getrlimit(_resource.RLIMIT_NPROC)
            nproc_target = (
                min(RLIMIT_NPROC, cur_hard)
                if cur_hard != _resource.RLIM_INFINITY
                else RLIMIT_NPROC
            )
            _resource.setrlimit(_resource.RLIMIT_NPROC, (nproc_target, nproc_target))
        except (ValueError, OSError):
            pass

    return _preexec


# ---------------------------------------------------------------------------
# Process-group kill + output truncation helpers
# ---------------------------------------------------------------------------

def kill_process_group(proc: subprocess.Popen) -> None:
    """SIGKILL the ENTIRE process group of *proc* (orphan grandchildren too).

    ``os.killpg`` is POSIX-only; falls back to ``proc.kill()`` elsewhere.
    """
    if hasattr(os, "killpg"):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # process already exited between timeout and kill
    else:
        proc.kill()


def truncate_output(raw: bytes, cap: int) -> tuple[bytes, bool]:
    """Truncate *raw* to *cap* bytes (+ marker) if it exceeds the cap.

    Returns ``(possibly_truncated_bytes, was_truncated)``.  Truncation happens
    on the bytes buffer BEFORE decoding to avoid OOM on huge outputs.
    """
    if len(raw) > cap:
        return raw[:cap] + TRUNCATION_MARKER, True
    return raw, False


# ---------------------------------------------------------------------------
# run_sandboxed — the shared hardened subprocess executor
# ---------------------------------------------------------------------------


@dataclass
class SandboxedRun:
    """Outcome of a hardened subprocess execution.

    Attributes
    ----------
    returncode:
        The child's exit code (meaningless when ``timed_out`` is True).
    stdout / stderr:
        Captured output as bytes, already truncated to the caps (with
        :data:`TRUNCATION_MARKER` appended when truncation occurred).
        Empty when ``timed_out`` is True.
    stdout_truncated / stderr_truncated:
        Whether the respective stream hit the cap.
    timed_out:
        True when the wall-clock timeout expired; the process GROUP has
        already been SIGKILLed and the pipes drained.  Callers raise their
        own domain-specific error (AppError / TimeoutExpired).
    """

    returncode: Optional[int]
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool


def run_sandboxed(
    argv: Sequence[str],
    *,
    env: dict[str, str],
    cwd: Optional[str] = None,
    timeout_s: Optional[float] = None,
    cpu_limit_s: Optional[int] = None,
    stdout_cap: int = STDOUT_CAP_BYTES,
    stderr_cap: int = STDERR_CAP_BYTES,
) -> SandboxedRun:
    """Run *argv* with the full M4-SEC hardening and return a SandboxedRun.

    Hardening applied (see module docstring): scrubbed env (caller-supplied),
    ``start_new_session=True`` (new process group), rlimits via ``preexec_fn``
    (POSIX), process-GROUP SIGKILL on timeout, and byte-level output caps.

    Parameters
    ----------
    timeout_s:
        Wall-clock timeout passed to ``communicate()``.  ``None`` means wait
        forever (callers map "timeout_s == 0" config semantics to ``None``).
    cpu_limit_s:
        CPU-seconds for ``RLIMIT_CPU``; ``None`` skips the CPU rlimit (other
        rlimits still apply).
    """
    proc = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
        preexec_fn=make_rlimit_preexec(cpu_limit_s),
    )

    try:
        raw_stdout, raw_stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_process_group(proc)
        proc.communicate()  # drain pipes to avoid zombie
        return SandboxedRun(
            returncode=proc.returncode,
            stdout=b"",
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=True,
        )

    raw_stdout, out_trunc = truncate_output(raw_stdout, stdout_cap)
    raw_stderr, err_trunc = truncate_output(raw_stderr, stderr_cap)

    return SandboxedRun(
        returncode=proc.returncode,
        stdout=raw_stdout,
        stderr=raw_stderr,
        stdout_truncated=out_trunc,
        stderr_truncated=err_trunc,
        timed_out=False,
    )
