"""Scale-to-zero query execution seam (MANAGED_LAKEHOUSE.md §3).

THE MODEL
=========
Heavy/batch analytics should not hold a VM hostage between queries.  The goal is
to bill **query-seconds, not VM-hours**: idle compute scales to zero, a query
arrives, compute wakes (cold-start), runs, returns, and the machine goes back to
sleep.  A small **always-warm tier** stays up so *interactive* queries skip the
cold-start entirely (latency floor) — those queries deliberately bypass
scale-to-zero (see :data:`WARM_TIER_NOTE`).

This module defines the SEAM, not a full Fly orchestrator:

  * :class:`ServerlessExecutor` — the protocol the planner/route layer codes
    against: ``await submit(plan, connector_cfg) -> ExecResult``.  Conceptually
    the executor wakes a worker, runs the plan, records the wall-clock
    query-seconds it billed for, and lets the worker sleep again.

  * :class:`HeavyPoolExecutor` — the DEFAULT, working-today backing.  It does not
    talk to the Fly Machines API; instead it delegates to the EXISTING
    heavy-query-pool forward path (``NUBI_HEAVY_QUERY_URL``).  On Fly that pool
    is a ``query`` process group with ``auto_stop_machines`` already configured
    in ``fly.toml`` — i.e. the platform ALREADY scales those machines to zero and
    wakes them on the inbound request.  So "delegate to the heavy pool" *is*
    scale-to-zero today, just orchestrated by Fly's proxy rather than by us.

  * :class:`FlyMachineExecutor` — a clearly-marked SKELETON for *explicit*
    wake/sleep, where Nubi (not the Fly proxy) drives ``scale count 0 -> 1`` on
    demand, waits for readiness, runs, and scales back.  This is the honest TODO:
    the methods raise / no-op with NotImplementedError-style guards so the seam
    compiles and can be selected, but the Machines API calls are stubs.

NO ROUTE WIRING happens here.  ``routes/query.py`` is untouched; wiring the
planner to call ``get_default_executor().submit(...)`` is a later task.  This
file is the interface + a backing that already works via the pool, so the rest
of Wave 4 has something concrete to depend on.

OPEN-CORE / INVARIANTS
----------------------
  * No billing logic lives here.  The executor REPORTS ``billed_seconds`` on the
    result; converting seconds -> CU/ZAR stays in ``ee/billing`` (core only
    meters).  ``query-seconds`` is the metering unit this seam exposes.
  * Secrets (connector credentials) are passed through opaquely and never logged
    or written to disk by this module.
  * The default backing re-uses the pool's auth/quota/RLS path verbatim, so RLS
    and bound-parameter safety are preserved upstream — this layer adds no SQL
    surface of its own.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import Request


# ---------------------------------------------------------------------------
# Warm tier
# ---------------------------------------------------------------------------
WARM_TIER_NOTE = (
    "Interactive queries (dashboards, ad-hoc explore) MUST skip scale-to-zero: "
    "the cold-start wake (machine boot + DuckDB attach + httpfs warm-up) would "
    "blow the interactive latency budget. They route to the always-warm tier "
    "instead — a small fixed pool kept up regardless of load. Only heavy/batch "
    "work (refreshes, large scans, exports) is worth a scale-to-zero wake, where "
    "seconds-of-latency is acceptable in exchange for paying ~$0 while idle."
)


def is_interactive(plan: "ExecPlan | Any") -> bool:
    """True when a plan should ride the WARM tier and skip scale-to-zero.

    Heuristic seam only — the real classifier (interactive vs batch) lands with
    the planner. Today: anything explicitly tagged ``interactive`` (or NOT
    tagged ``batch``/``heavy``) is treated as interactive, so the default is the
    low-latency, warm path. Batch refreshes opt IN to scale-to-zero.
    """
    tier = _plan_attr(plan, "tier")
    if isinstance(tier, str):
        t = tier.strip().lower()
        if t in ("interactive", "warm"):
            return True
        if t in ("batch", "heavy", "cold"):
            return False
    # Unknown/untagged → treat as interactive (latency-safe default).
    return not bool(_plan_attr(plan, "batch"))


# ---------------------------------------------------------------------------
# Plan / result value objects (intentionally thin — the real plan type lives in
# the planner; this seam only needs to forward an opaque plan + a connector cfg
# and hand back rows/bytes/seconds).
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ExecPlan:
    """Opaque-ish execution plan handed to the executor.

    ``payload`` is whatever the route/planner already builds (e.g. the
    ``QueryIn`` body for the pool path). ``tier``/``batch`` are advisory hints
    used by :func:`is_interactive` to pick warm-vs-scale-to-zero. Nothing here
    parses SQL — bound params stay bound, inside ``payload``.
    """

    payload: Any
    tier: Optional[str] = None
    batch: bool = False
    # Carried opaquely for the pool backing: the inbound request supplies the
    # auth/origin headers the pool re-verifies. None for non-HTTP callers.
    request: Optional["Request"] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecResult:
    """Result of a serverless execution.

    ``billed_seconds`` is the query-second meter this seam exposes (wall clock of
    the run on the worker). ``bytes_scanned`` mirrors the Wave-4 bytes-scanned
    metric when the backing can report it (the pool path can forward it via a
    header later); ``None`` when unknown. ``cold_start`` records whether a wake
    was paid for (always False for the warm tier).
    """

    ok: bool
    payload: Any = None
    billed_seconds: float = 0.0
    bytes_scanned: Optional[int] = None
    cold_start: bool = False
    tier: str = "scale_to_zero"
    error: Optional[str] = None


def _plan_attr(plan: Any, name: str) -> Any:
    """Read an attribute or dict key off a plan-ish object (duck-typed)."""
    if isinstance(plan, dict):
        return plan.get(name)
    return getattr(plan, name, None)


# ---------------------------------------------------------------------------
# The protocol the rest of the system codes against.
# ---------------------------------------------------------------------------
@runtime_checkable
class ServerlessExecutor(Protocol):
    """Scale-to-zero execution interface.

    Conceptual lifecycle per ``submit``:  *wake* (ensure a worker exists, paying
    cold-start iff scaled to zero) → *run* (execute the plan, clock the
    query-seconds) → *sleep* (let the worker scale back to zero after idle).
    Interactive plans skip the wake/sleep and run on the warm tier.

    Implementations MUST be safe to fail-open: if scale-to-zero machinery is
    unavailable, execution should still complete (e.g. locally / on the pool),
    never hang an idle resource.
    """

    async def submit(
        self, plan: "ExecPlan", connector_cfg: dict[str, Any]
    ) -> "ExecResult":
        """Wake → run → sleep. Return rows/bytes/seconds in an ExecResult."""
        ...


# ---------------------------------------------------------------------------
# DEFAULT backing — delegates to the existing heavy-query pool (works today).
# ---------------------------------------------------------------------------
class HeavyPoolExecutor:
    """Scale-to-zero via the EXISTING heavy-query-pool forward path.

    Why this is already scale-to-zero: the pool is a Fly ``query`` process group
    with ``auto_stop_machines`` in ``fly.toml``. Fly's proxy stops idle machines
    and boots one on the next inbound request — so forwarding a heavy query to
    ``NUBI_HEAVY_QUERY_URL`` IS a wake-run-sleep cycle, just orchestrated by the
    platform. We pay roughly $0 while idle and per-second while running, which is
    exactly the §3 model. This backing therefore needs no Fly Machines API calls.

    It does NOT re-implement forwarding (that lives in ``routes/query.py`` and we
    must not edit it). Instead the route layer passes a bound ``forward`` callable
    — ``_forward_heavy_query``-shaped — through the plan/ctor, and this executor
    just times it. When no forwarder/URL is configured, it fails open to the
    ``local_run`` callable so self-host/dev still works in-process.
    """

    def __init__(
        self,
        forward: Optional[Any] = None,
        local_run: Optional[Any] = None,
    ) -> None:
        # ``forward(request, payload) -> response | None`` — None means "run
        # locally" (no pool / this IS the pool / already forwarded). Mirrors the
        # contract of routes.query._forward_heavy_query without importing it
        # (avoids a route-layer dependency in the compute package).
        self._forward = forward
        # ``local_run(plan, connector_cfg) -> Any`` — in-process execution used
        # when no pool is configured or the pool is unreachable (fail-open).
        self._local_run = local_run

    @staticmethod
    def pool_configured() -> bool:
        """True when a heavy pool URL is set and this process isn't the pool."""
        if os.getenv("NUBI_QUERY_POOL", "").strip().lower() == "heavy":
            return False
        return bool(os.getenv("NUBI_HEAVY_QUERY_URL", "").strip())

    async def submit(
        self, plan: "ExecPlan", connector_cfg: dict[str, Any]
    ) -> "ExecResult":
        # Interactive → warm tier: do not wake/sleep, just run (locally/warm).
        interactive = is_interactive(plan)
        started = time.monotonic()

        try:
            if not interactive and self._forward is not None:
                # Heavy/batch → forward to the scale-to-zero pool. A cold machine
                # wakes here; Fly bills the seconds it's up, we bill the run.
                resp = await self._forward(plan.request, plan.payload)
                if resp is not None:
                    elapsed = time.monotonic() - started
                    return ExecResult(
                        ok=resp.status_code < 400
                        if hasattr(resp, "status_code")
                        else True,
                        payload=resp,
                        billed_seconds=elapsed,
                        bytes_scanned=_bytes_from_resp(resp),
                        cold_start=True,  # conservative: assume a wake was paid
                        tier="scale_to_zero",
                    )
                # resp is None → fall through to local (fail-open).

            # Warm tier OR no pool OR pool declined → run in-process.
            payload = None
            if self._local_run is not None:
                payload = await _maybe_await(
                    self._local_run(plan, connector_cfg)
                )
            elapsed = time.monotonic() - started
            return ExecResult(
                ok=True,
                payload=payload,
                billed_seconds=elapsed,
                cold_start=False,
                tier="warm" if interactive else "local",
            )
        except Exception as exc:  # noqa: BLE001 - report, never leak a stuck VM
            elapsed = time.monotonic() - started
            return ExecResult(
                ok=False,
                billed_seconds=elapsed,
                error=str(exc),
                tier="warm" if interactive else "scale_to_zero",
            )


def _bytes_from_resp(resp: Any) -> Optional[int]:
    """Best-effort bytes-scanned from a pool response header (Wave-4 metric).

    The pool can later attach ``X-Nubi-Bytes-Scanned``; until then this is None.
    """
    try:
        headers = getattr(resp, "headers", None)
        if headers is None:
            return None
        raw = headers.get("x-nubi-bytes-scanned")
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it's awaitable, else return it (sync local_run ok)."""
    if hasattr(value, "__await__"):
        return await value
    return value


# ---------------------------------------------------------------------------
# SKELETON — explicit Fly Machine wake/sleep (the honest TODO).
# ---------------------------------------------------------------------------
class FlyMachineExecutor:
    """SKELETON: drive ``scale count 0 -> 1`` ourselves on the Fly Machines API.

    Unlike :class:`HeavyPoolExecutor` (where Fly's PROXY wakes machines on the
    inbound request), this backing would have Nubi explicitly orchestrate the
    lifecycle — useful when work isn't request-shaped (e.g. a scheduled rollup
    refresh with no inbound HTTP) or when we want tighter control over warm-pool
    size, machine class, and sleep timing than ``auto_stop_machines`` gives.

    Lifecycle (TODO — none of this is implemented; calls below are stubs):

      1. wake():  POST machines API ``start`` (or create from a template) for the
         target app/process group; poll until ``state == "started"`` and the
         health check passes. Record cold-start latency.
      2. run():   forward the plan to the now-warm machine (same path as the pool
         backing) and clock query-seconds.
      3. sleep(): after an idle window, ``stop`` the machine (or rely on
         ``auto_stop_machines``) so it scales back to zero.

    Config it WILL need (env, not wired yet):
      * ``FLY_API_TOKEN`` — Machines API auth.
      * ``FLY_APP_NAME`` / ``NUBI_HEAVY_MACHINE_GROUP`` — which group to scale.
      * machine template (image == this image, size == heavy class).

    Guard rails it MUST keep: fail-open to the pool/local path if the API is
    unreachable (never block on a wake); cap concurrent wakes; idempotent sleep.
    """

    def __init__(self, fallback: Optional[ServerlessExecutor] = None) -> None:
        # Until the Machines API is wired, delegate everything to a working
        # backing (default: the heavy-pool executor). This keeps the seam usable
        # and honest: selecting FlyMachineExecutor does NOT silently break.
        self._fallback: ServerlessExecutor = fallback or HeavyPoolExecutor()

    async def _wake(self, connector_cfg: dict[str, Any]) -> bool:
        """TODO: scale count 0 -> 1 via Fly Machines API; return True when warm.

        Skeleton: returns False (no machine woken) so callers fall back.
        """
        # TODO(W4-E): httpx POST {FLY_MACHINES_API}/apps/{app}/machines/{id}/start
        # then poll GET .../machines/{id} until state == "started"; health check.
        raise NotImplementedError(
            "FlyMachineExecutor._wake: Fly Machines wake not yet implemented "
            "(see MANAGED_LAKEHOUSE.md §3 / Wave 4 W4-E). Use HeavyPoolExecutor."
        )

    async def _sleep(self) -> None:
        """TODO: scale back to zero (stop the machine) after the idle window."""
        # TODO(W4-E): POST .../machines/{id}/stop, or lean on auto_stop_machines.
        raise NotImplementedError(
            "FlyMachineExecutor._sleep: explicit machine stop not implemented."
        )

    async def submit(
        self, plan: "ExecPlan", connector_cfg: dict[str, Any]
    ) -> "ExecResult":
        # Fail-open: explicit wake/sleep is not implemented, so delegate to the
        # working backing (pool/local). When _wake is implemented this becomes
        # wake() → run-on-machine → sleep(), with the same fail-open guarantee.
        return await self._fallback.submit(plan, connector_cfg)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def get_default_executor(
    forward: Optional[Any] = None,
    local_run: Optional[Any] = None,
) -> ServerlessExecutor:
    """Return the executor backing selected by env (default: heavy pool).

    ``NUBI_SERVERLESS_BACKEND``:
      * unset / ``pool`` / ``auto`` → :class:`HeavyPoolExecutor` (works today).
      * ``fly`` → :class:`FlyMachineExecutor` (skeleton; falls back to pool).
      * ``local`` → :class:`HeavyPoolExecutor` with no forwarder (always
        in-process — self-host / dev).

    ``forward``/``local_run`` are the callables the route layer will inject when
    it wires this up (no route wiring happens in this file).
    """
    backend = os.getenv("NUBI_SERVERLESS_BACKEND", "").strip().lower()
    if backend == "fly":
        return FlyMachineExecutor(
            fallback=HeavyPoolExecutor(forward=forward, local_run=local_run)
        )
    if backend == "local":
        return HeavyPoolExecutor(forward=None, local_run=local_run)
    # Default / "pool" / "auto".
    return HeavyPoolExecutor(forward=forward, local_run=local_run)
