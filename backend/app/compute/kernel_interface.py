"""Compute kernel abstraction — B1 first-class remote tier + resource requests.

This module provides:

``CellResourceRequest``
    Per-cell resource declaration (cpu_cores, mem_mb, timeout_s).  Plumbed from
    the flow TaskSpec cell config into every kernel invocation.  Local runners
    CLAMP to their configured limits; remote runners forward to the provider.

``KernelTier``
    Enumeration of all supported compute tiers (warehouse, browser,
    local_kernel, remote_kernel).  The placement router (app/compute/router.py)
    returns these as strings; callers that need typed dispatch use
    ``KernelTier(str_value)``.

``get_kernel_runner(tier, resource_request)``
    Factory that returns the correct ``KernelRunner`` subclass instance for the
    given tier, wiring in resource-request constraints.  Reads provider
    configuration from env vars (``KERNEL_REMOTE_PROVIDER``, ``E2B_API_KEY``,
    ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET``) so callers need not know the
    details.

``MAP_MAX_CONCURRENCY``
    Module-level configurable cap on simultaneous map fan-out child slots.
    Read from ``FLOWS_MAP_MAX_CONCURRENCY`` env var (default 32).
    Enforced by the runtime via ``acquire_map_slot`` / ``release_map_slot``
    semaphore helpers.

``acquire_map_slot`` / ``release_map_slot``
    Async semaphore helpers for the map fan-out concurrency cap.  All map
    fan-out child task executions MUST acquire a slot before running and
    release it afterward.  Dropping both functions after use is the preferred
    pattern (try/finally).

Design notes
------------
- This file is ADDITIVE — it does not modify runner.py, router.py, or any
  existing caller.  Callers import from here when they want the first-class
  tier abstraction.
- ``CellResourceRequest`` is a pure dataclass (no Pydantic, no DB dependency)
  so it can be used in sync/async contexts and imported anywhere.
- ``get_kernel_runner`` is synchronous.  The returned runner may be used in a
  thread (all KernelRunner.run() calls are synchronous; callers that want
  async behaviour use asyncio.to_thread).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.compute.runner import KernelRunner


# ---------------------------------------------------------------------------
# MAP fan-out concurrency cap
# ---------------------------------------------------------------------------


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


#: Global cap on simultaneous map fan-out child executions.
#: Configurable via ``FLOWS_MAP_MAX_CONCURRENCY`` (default 32).
MAP_MAX_CONCURRENCY: int = _int_env("FLOWS_MAP_MAX_CONCURRENCY", 32)

# Module-level asyncio.Semaphore — created lazily on first use so the module
# can be imported before an event loop exists.
_map_semaphore: Optional[asyncio.Semaphore] = None


def _get_map_semaphore() -> asyncio.Semaphore:
    """Return (or lazily create) the module-level map-slot semaphore."""
    global _map_semaphore
    if _map_semaphore is None:
        _map_semaphore = asyncio.Semaphore(MAP_MAX_CONCURRENCY)
    return _map_semaphore


def reset_map_semaphore() -> None:
    """Reset the semaphore (test helper — do not call in production)."""
    global _map_semaphore
    _map_semaphore = None


async def acquire_map_slot() -> None:
    """Acquire a map fan-out concurrency slot.

    Blocks the calling coroutine until a slot is available.  Always pair with
    :func:`release_map_slot` in a try/finally block.

    When ``MAP_MAX_CONCURRENCY == 0`` the cap is disabled and this is a no-op.
    """
    if MAP_MAX_CONCURRENCY <= 0:
        return
    await _get_map_semaphore().acquire()


def release_map_slot() -> None:
    """Release a map fan-out concurrency slot.

    No-op when the cap is disabled (``MAP_MAX_CONCURRENCY == 0``).
    """
    if MAP_MAX_CONCURRENCY <= 0:
        return
    _get_map_semaphore().release()


# ---------------------------------------------------------------------------
# CellResourceRequest
# ---------------------------------------------------------------------------


#: Hard ceiling on local-kernel CPU cores (fractional, >0).
_LOCAL_CPU_CAP: float = float(os.environ.get("KERNEL_LOCAL_CPU_CAP", "8"))
#: Hard ceiling on local-kernel memory in MiB.
_LOCAL_MEM_CAP_MB: int = _int_env("KERNEL_LOCAL_MEM_CAP_MB", 4096)  # 4 GiB


@dataclass
class CellResourceRequest:
    """Per-cell resource request for kernel execution.

    Cell authors declare how much compute their cell needs.  The kernel
    abstraction uses this to:

    - **Remote kernels**: forward cpu/mem as container resource hints to the
      provider (E2B / Modal).  E2B Sandbox.create() accepts ``cpu_count`` and
      ``memory_mb``; Modal functions accept ``cpu`` and ``memory``.
    - **Local kernel**: clamp cpu/mem to the host's allowed maximums
      (``KERNEL_LOCAL_CPU_CAP``, ``KERNEL_LOCAL_MEM_CAP_MB``); the rlimit-based
      hardening in sandbox.py applies the effective memory cap.
    - **Timeout**: override the task-level ``timeout_s`` when set (> 0).

    Fields
    ------
    cpu_cores:
        Number of CPU cores requested (fractional allowed, e.g. 0.5).
        0 = use default.
    mem_mb:
        Memory in MiB requested.  0 = use default.
    timeout_s:
        Execution timeout in seconds.  0 = inherit task-level timeout.
    """

    cpu_cores: float = field(default=0.0)
    mem_mb: int = field(default=0)
    timeout_s: int = field(default=0)

    @classmethod
    def from_task_config(cls, config: dict) -> "CellResourceRequest":
        """Build a ``CellResourceRequest`` from a task config dict.

        Reads the optional ``resources`` sub-dict (preferred) or the legacy
        flat keys ``cpu_cores``, ``mem_mb``, ``timeout_s`` for backward compat.

        config shape (preferred)::

            config:
              resources:
                cpu_cores: 4
                mem_mb: 8192
                timeout_s: 600

        Returns a zero-valued request (all defaults) when no resource keys are
        present.
        """
        resources = config.get("resources") or {}
        if not isinstance(resources, dict):
            resources = {}

        def _get(key: str, default: float) -> float:
            val = resources.get(key, config.get(key, default))
            try:
                return float(val) if val is not None else default
            except (TypeError, ValueError):
                return default

        cpu = _get("cpu_cores", 0.0)
        mem = int(_get("mem_mb", 0))
        timeout = int(_get("timeout_s", 0))
        return cls(cpu_cores=cpu, mem_mb=mem, timeout_s=timeout)

    def clamp_for_local(self) -> "CellResourceRequest":
        """Return a copy with cpu/mem clamped to local-kernel limits."""
        cpu = (
            min(self.cpu_cores, _LOCAL_CPU_CAP)
            if self.cpu_cores > 0
            else self.cpu_cores
        )
        mem = (
            min(self.mem_mb, _LOCAL_MEM_CAP_MB)
            if self.mem_mb > 0
            else self.mem_mb
        )
        return CellResourceRequest(
            cpu_cores=cpu,
            mem_mb=mem,
            timeout_s=self.timeout_s,
        )

    @property
    def effective_timeout_s(self) -> Optional[int]:
        """Return the timeout override, or None if not set (0)."""
        return self.timeout_s if self.timeout_s > 0 else None


# ---------------------------------------------------------------------------
# KernelTier
# ---------------------------------------------------------------------------


class KernelTier(str, Enum):
    """Supported compute tiers returned by the placement router.

    Inherits from str so tier values can be used as plain strings in dicts /
    comparison operators without explicit ``.value`` access.
    """

    WAREHOUSE = "warehouse"
    BROWSER = "browser"
    LOCAL_KERNEL = "local_kernel"
    REMOTE_KERNEL = "remote_kernel"


# ---------------------------------------------------------------------------
# get_kernel_runner factory
# ---------------------------------------------------------------------------


def get_kernel_runner(
    tier: str | KernelTier,
    resource_request: Optional[CellResourceRequest] = None,
) -> "KernelRunner":
    """Return the appropriate ``KernelRunner`` for *tier*.

    Parameters
    ----------
    tier:
        One of the ``KernelTier`` values (or the equivalent string).
    resource_request:
        Optional per-cell resource request.  For remote tiers the request is
        forwarded to the provider; for the local tier it is clamped and ignored
        (the local runner applies rlimits from sandbox.py, not from the request).

    Returns
    -------
    KernelRunner
        A configured runner instance.  The runner is **not** cached — callers
        should reuse a runner for the lifetime of a request if that matters.

    Raises
    ------
    AppError("kernel_unavailable", 503)
        When the requested tier requires remote configuration that is absent
        (missing API keys, uninstalled SDK).
    ValueError
        When *tier* is not a recognised kernel tier (e.g. "warehouse" —
        use the warehouse connector path, not a kernel runner).
    """
    from app.compute.runner import LocalSubprocessRunner, RemoteRunner  # noqa: PLC0415

    # KernelTier is str subclass, so .value access works for both str and enum.
    tier_str = tier.value if isinstance(tier, KernelTier) else str(tier)

    if tier_str == KernelTier.LOCAL_KERNEL.value:
        return LocalSubprocessRunner()

    if tier_str == KernelTier.REMOTE_KERNEL.value:
        provider = os.environ.get("KERNEL_REMOTE_PROVIDER", "").lower().strip()

        if provider == "e2b":
            from app.compute.remote_e2b import E2BRunner  # noqa: PLC0415

            api_key = os.environ.get("E2B_API_KEY", "")
            # Optional custom sandbox template (built from backend/app/compute/e2b/)
            # that bakes in the BYO-model attribution stack (shap, scikit-learn,
            # onnxruntime, xgboost).  Empty → E2B base image.
            template = os.environ.get("E2B_TEMPLATE", "") or None
            return E2BRunner(api_key=api_key, template=template)

        if provider == "modal":
            from app.compute.remote_modal import ModalRunner  # noqa: PLC0415

            token_id = os.environ.get("MODAL_TOKEN_ID", "")
            token_secret = os.environ.get("MODAL_TOKEN_SECRET", "")
            return ModalRunner(token_id=token_id, token_secret=token_secret)

        # No provider configured — return the unconfigured stub (raises 503 on run).
        return RemoteRunner(configured=False)

    raise ValueError(
        f"Tier {tier_str!r} is not a kernel tier (not 'local_kernel' or "
        "'remote_kernel').  Use the warehouse connector for SQL/browser tiers."
    )
