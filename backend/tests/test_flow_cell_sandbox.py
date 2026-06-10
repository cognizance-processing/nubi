"""M4-SEC hardening tests for Flows Python cells (shared sandbox).

Flows Python cells (``kind='python'`` → ``_handle_python`` in
app/flows/registry.py) now execute through the same hardened subprocess
helper as the compute kernel (``app.compute.sandbox.run_sandboxed``):

1. Cell contract preserved — ``result`` dict + ``_stdout_lines`` metadata
   behave exactly as before the hardening.
2. Timeout kills the ENTIRE process group: a cell spawning a grandchild with
   ``timeout_s=1`` fails promptly AND the grandchild does not survive (no
   orphan sentinel file is written).
3. Output cap: a cell printing > 1 MiB has its captured logs truncated with
   a clear marker; the task still completes without OOMing the parent.
4. RLIMIT_CPU is applied when a timeout is set, and SKIPPED when
   ``timeout_s=0`` (no timeout ⇒ legit long CPU work must not be killed);
   the other rlimits (AS / FSIZE / NPROC) still apply.

Notes
-----
- POSIX-specific tests are skipped when ``resource`` is unavailable, with the
  same skip markers as tests/test_kernel_security.py.
- Timeouts are kept small (~1 s) so the suite stays fast in CI.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from typing import Any

import pytest

from app.compute.sandbox import (
    RLIMIT_CPU_GRACE_S,
    STDOUT_CAP_BYTES,
    TRUNCATION_MARKER,
    run_sandboxed,
    truncate_output,
)
from app.flows.executor import TaskContext
from app.flows.registry import get_task_kind_registry, reset_for_tests

# ---------------------------------------------------------------------------
# POSIX guard (same convention as test_kernel_security.py)
# ---------------------------------------------------------------------------
try:
    import resource as _resource
    _HAVE_RESOURCE = True
except ImportError:
    _HAVE_RESOURCE = False

CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(inputs=None, params=None, secrets=None) -> TaskContext:
    return TaskContext(
        flow_params=params or {},
        inputs=inputs or {},
        secrets=secrets or {},
    )


def _python_handler():
    reset_for_tests()
    return get_task_kind_registry().get("python")


# ===========================================================================
# 1. Cell contract preserved (result dict + _stdout_lines)
# ===========================================================================


class TestCellContractPreserved:
    def test_result_and_stdout_lines(self):
        handler = _python_handler()
        config = {
            "code": "print('log line one')\nprint('log line two')\nresult = {'ok': 1}",
        }
        out = handler(config, _ctx(), CLAIMS)
        assert out["ok"] == 1
        assert out["_stdout_lines"] == ["log line one", "log line two"]

    def test_nonzero_exit_raises_with_stderr(self):
        handler = _python_handler()
        config = {"code": "raise ValueError('boom')"}
        with pytest.raises(RuntimeError) as exc_info:
            handler(config, _ctx(), CLAIMS)
        assert "boom" in str(exc_info.value)


# ===========================================================================
# 2. Timeout kills the process GROUP (no orphan grandchildren)
# ===========================================================================


@pytest.mark.skipif(not _HAVE_RESOURCE, reason="resource module unavailable (non-POSIX)")
@pytest.mark.skipif(sys.platform == "win32", reason="process groups not supported on Windows")
class TestTimeoutKillsProcessGroup:
    def test_orphan_grandchild_is_killed(self, monkeypatch):
        """timeout_s=1 cell spawning a grandchild → prompt failure, no orphan.

        The grandchild would write a sentinel file after sleeping 2.5 s.  The
        cell itself sleeps 30 s so the 1 s timeout fires first.  Because the
        sandbox launches the cell with ``start_new_session=True`` and kills
        the whole group via ``os.killpg(SIGKILL)``, the grandchild must die
        too — the sentinel must never appear.

        RLIMIT_NPROC counts ALL processes owned by the OS user; on a busy dev
        machine the default cap (64) would block the grandchild fork outright
        and make this test pass vacuously.  Raise it so the grandchild really
        spawns and the group-kill is what prevents the sentinel.
        """
        import app.compute.sandbox as sandbox_mod
        monkeypatch.setattr(sandbox_mod, "RLIMIT_NPROC", 4096)

        handler = _python_handler()
        sentinel = tempfile.mktemp(prefix="nubi_flow_orphan_sentinel_", suffix=".txt")

        # NOTE: the inner '-c' payload is an f-string evaluated INSIDE the
        # cell (where `sentinel` is bound) — mirrors test_kernel_security.py.
        code = (
            "import subprocess, sys, time\n"
            f"sentinel = {sentinel!r}\n"
            "subprocess.Popen(\n"
            "    [sys.executable, '-c',\n"
            "     f'import time, pathlib; time.sleep(2.5); pathlib.Path({sentinel!r}).write_text(\"orphan\")'],\n"
            ")\n"
            "time.sleep(30)\n"
            "result = {'never': 'reached'}\n"
        )

        t0 = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            handler({"code": code, "timeout_s": 1}, _ctx(), CLAIMS)
        elapsed = time.monotonic() - t0
        # Prompt failure: well under the 30 s the cell wanted to sleep.
        assert elapsed < 5, f"timeout not prompt: {elapsed:.1f}s"

        # Wait past the grandchild's 2.5 s sleep; the sentinel must NOT exist.
        time.sleep(3)
        assert not os.path.exists(sentinel), (
            f"Orphan grandchild was NOT killed — sentinel file exists: {sentinel}"
        )


# ===========================================================================
# 3. Output cap: huge print truncated with marker, task completes
# ===========================================================================


class TestOutputCap:
    def test_huge_print_truncated_with_marker(self):
        """A cell printing ~2 MiB → captured logs capped at 1 MiB + marker."""
        handler = _python_handler()
        code = (
            "import sys\n"
            "sys.stdout.write('A' * 2 * 1024 * 1024 + '\\n')\n"
            "sys.stdout.flush()\n"
            "result = {'ok': True}\n"
        )
        out = handler({"code": code}, _ctx(), CLAIMS)

        # Task completed cleanly (no OOM / no exception).
        lines = out["_stdout_lines"]
        captured = "\n".join(lines)
        marker = TRUNCATION_MARKER.decode().strip()
        assert marker in captured, "Truncation marker missing from captured logs"
        # Capped at ~1 MiB (+ marker overhead).
        assert len(captured) <= STDOUT_CAP_BYTES + 200, (
            f"stdout not capped: {len(captured)} bytes"
        )

    def test_small_output_not_truncated(self):
        handler = _python_handler()
        code = "print('small')\nresult = {'ok': True}"
        out = handler({"code": code}, _ctx(), CLAIMS)
        assert out["ok"] is True
        marker = TRUNCATION_MARKER.decode().strip()
        assert marker not in "\n".join(out["_stdout_lines"])

    def test_truncate_output_unit(self):
        data = b"x" * 10
        kept, truncated = truncate_output(data, 100)
        assert kept == data and truncated is False
        kept, truncated = truncate_output(data, 4)
        assert truncated is True
        assert kept.startswith(b"xxxx") and kept.endswith(TRUNCATION_MARKER)


# ===========================================================================
# 4. rlimits: CPU applied with a timeout, skipped when timeout_s=0
# ===========================================================================


@pytest.mark.skipif(not _HAVE_RESOURCE, reason="resource module unavailable (non-POSIX)")
class TestRlimitCpuSemantics:
    _PROBE = (
        "import resource; "
        "print(resource.getrlimit(resource.RLIMIT_CPU)[0])"
    )

    def _probe_child_cpu_limit(self, cpu_limit_s):
        run = run_sandboxed(
            [sys.executable, "-c", self._PROBE],
            env={"PATH": os.environ.get("PATH", "")},
            timeout_s=30,
            cpu_limit_s=cpu_limit_s,
        )
        assert run.returncode == 0, run.stderr.decode(errors="replace")
        return int(run.stdout.decode().strip())

    def test_cpu_rlimit_applied_when_timeout_set(self):
        """cpu_limit_s = timeout + grace → child soft RLIMIT_CPU matches."""
        limit = 5 + RLIMIT_CPU_GRACE_S
        assert self._probe_child_cpu_limit(limit) == limit

    def test_cpu_rlimit_skipped_when_no_timeout(self):
        """cpu_limit_s=None (flow cell timeout_s=0) → child inherits parent's
        RLIMIT_CPU unchanged (long CPU work is not silently killed)."""
        parent_soft = _resource.getrlimit(_resource.RLIMIT_CPU)[0]
        assert self._probe_child_cpu_limit(None) == parent_soft
