"""DataProvider resolver for composite board data (BET-3).

Public API
----------
resolve_provider_data(board_id, provider_id, params, org_id, claims, repo)
    -> dict[str, pa.Table]
    Resolve a single DataProvider declared in a board's DashboardSpec.

    * ``kind='flow'``   — runs the named Flow (matched by provider id or name)
      via the flows runtime (``materialize_flow_run`` + ``drain_flow_run``).
      Named results are collected from task_run results whose task_key matches a
      declared ``result.name``.
    * ``kind='inline'`` — executes the provider's ``base_cte`` / per-result
      queries directly via the same connector pipeline as ``run_query_rows``.
    * RLS comes ONLY from ``claims["policies"]`` (the verified token).  It is
      NEVER read from the request body.
    * Results are cached by ``(provider_id, frozen_params, rls_hash)`` using the
      Wave-2 base-scan cache helpers.

Two execution modes
-------------------
``ephemeral``     (default) — in-memory + TTL cache; used for live interactive
                  board views.
``materialized``  — serves flow-backed providers from the latest SUCCESSFUL
                  flow_run's task_run results (the 'scheduled → derived tables'
                  contract).  Falls back transparently to the ephemeral (live)
                  path when no successful run exists yet.  For inline providers,
                  always falls through to the ephemeral path (no derived tables).

Security invariants
-------------------
* The board is resolved **org-scoped** via the repo — never cross-org.
* RLS predicates are sourced from the verified token's ``policies`` claim only.
* Org-scoping is enforced even when ``params`` override provider params: the
  resolver re-checks the board exists in org before executing.
* Cache keys incorporate the full ``policies`` dict so different tenants NEVER
  share a cached result (same guarantee as the base-scan cache in cache_key.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
from typing import Any

import pyarrow as pa

# Maximum rows materialised per inline-provider result into Arrow IPC.
# Mirrors the cap used by collect.py (NUBI_COLLECT_ROW_CAP).  0 = unlimited.
_DEFAULT_ROW_CAP = 100_000
_ROW_CAP: int = int(os.environ.get("NUBI_COLLECT_ROW_CAP", _DEFAULT_ROW_CAP))

# [MED resource] Cap: max task_runs loaded from the store per flow run.
# A map fan-out run may spawn thousands of child task_runs; loading them all
# into RAM on every provider HTTP request is unbounded.  We fetch at most
# _MAX_TASK_RUNS+1 records: if we get more than _MAX_TASK_RUNS back we log a
# warning (the +1 lets us detect truncation without an extra COUNT query).
# Override via env var NUBI_MAX_TASK_RUNS_CEILING.
_DEFAULT_MAX_TASK_RUNS = 10_000
_MAX_TASK_RUNS: int = int(
    os.environ.get("NUBI_MAX_TASK_RUNS_CEILING", _DEFAULT_MAX_TASK_RUNS)
)

# [MED resource] Cap: max number of result tables per provider response.
# A provider returning huge numbers of named result sets would produce an
# unbounded Arrow IPC payload cached in memory.  Override via env var.
_DEFAULT_PROVIDER_MAX_TABLES = 50
_PROVIDER_MAX_TABLES: int = int(
    os.environ.get("NUBI_PROVIDER_MAX_TABLES", _DEFAULT_PROVIDER_MAX_TABLES)
)

# [MED resource] Cap: max total serialised bytes across all result tables in a
# single provider response.  0 = unlimited.  Default 64 MiB.
_DEFAULT_PROVIDER_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB
_PROVIDER_MAX_BYTES: int = int(
    os.environ.get("NUBI_PROVIDER_MAX_BYTES", _DEFAULT_PROVIDER_MAX_BYTES)
)

from app.errors import AppError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-(org_id, provider_id) embed concurrency semaphore registry
# ---------------------------------------------------------------------------
# Embed tokens are never billed, but on a cache miss a flow provider still
# drives full live execution (materialize + drain).  Without a concurrency
# guard, a client can cache-bust with unique params and fan out arbitrarily
# many concurrent live runs, amplifying compute unboundedly.
#
# Fix: for is_embed + kind=='flow' we acquire a small per-(org, provider)
# asyncio.Semaphore before executing.  The semaphore ceiling is controlled by
# NUBI_EMBED_FLOW_CONCURRENCY (default 2).  Callers that cannot acquire within
# NUBI_EMBED_FLOW_TIMEOUT_S seconds receive a 429 "provider_busy" error.
#
# Embed stays unmetered; this guard only limits SERVER-SIDE compute parallelism.
_DEFAULT_EMBED_FLOW_CONCURRENCY = 2
_EMBED_FLOW_CONCURRENCY: int = int(
    os.environ.get("NUBI_EMBED_FLOW_CONCURRENCY", _DEFAULT_EMBED_FLOW_CONCURRENCY)
)
_DEFAULT_EMBED_FLOW_TIMEOUT_S = 10.0
_EMBED_FLOW_TIMEOUT_S: float = float(
    os.environ.get("NUBI_EMBED_FLOW_TIMEOUT_S", _DEFAULT_EMBED_FLOW_TIMEOUT_S)
)

# [LOW resource] Cap: maximum number of (org_id, provider_id) semaphore entries
# kept in the registry at any time.  When the registry is full, idle semaphores
# (value == _EMBED_FLOW_CONCURRENCY, i.e. no in-flight or waiting callers) are
# evicted in LRU order before a new entry is created.  A semaphore that still
# has callers holding or waiting on it is NEVER evicted — those entries survive
# until they become idle.  Override via env var NUBI_EMBED_SEM_REGISTRY_CAP.
_DEFAULT_EMBED_SEM_REGISTRY_CAP = 1024
_EMBED_SEM_REGISTRY_CAP: int = int(
    os.environ.get("NUBI_EMBED_SEM_REGISTRY_CAP", _DEFAULT_EMBED_SEM_REGISTRY_CAP)
)

# ---------------------------------------------------------------------------
# Per-(org_id, provider_id) NON-EMBED (interactive) flow concurrency guard
# ---------------------------------------------------------------------------
# Interactive (first-party writer/viewer) flow-provider requests are also
# vulnerable to the same cache-bust fan-out problem as embed tokens: N concurrent
# interactive requests on a cache miss each spawn a full live flow run.
#
# Fix: apply the same per-(org, provider) asyncio.Semaphore pattern used for
# embed tokens to the non-embed (interactive) path as well.  A separate registry
# and ceiling allow operators to tune embed and interactive concurrency
# independently via env vars.
#
# NUBI_FLOW_PROVIDER_CONCURRENCY : max concurrent live flow runs per (org, provider)
#   for non-embed callers (default 4 — interactive writers tend to be trusted so
#   the ceiling is higher than for public embed tokens).
# NUBI_FLOW_PROVIDER_TIMEOUT_S   : wait timeout before 429 (default 10 s, same as embed).
# NUBI_FLOW_PROVIDER_SEM_CAP     : max semaphore registry entries (default 1024).
_DEFAULT_FLOW_PROVIDER_CONCURRENCY = 4
_FLOW_PROVIDER_CONCURRENCY: int = int(
    os.environ.get("NUBI_FLOW_PROVIDER_CONCURRENCY", _DEFAULT_FLOW_PROVIDER_CONCURRENCY)
)
_DEFAULT_FLOW_PROVIDER_TIMEOUT_S = 10.0
_FLOW_PROVIDER_TIMEOUT_S: float = float(
    os.environ.get("NUBI_FLOW_PROVIDER_TIMEOUT_S", _DEFAULT_FLOW_PROVIDER_TIMEOUT_S)
)
_DEFAULT_FLOW_PROVIDER_SEM_CAP = 1024
_FLOW_PROVIDER_SEM_CAP: int = int(
    os.environ.get("NUBI_FLOW_PROVIDER_SEM_CAP", _DEFAULT_FLOW_PROVIDER_SEM_CAP)
)

# [MED event-loop starvation] Wall-clock EXECUTION timeout for a flow-provider
# drain, applied AFTER the per-(org, provider) semaphore slot is acquired.  The
# semaphore's wait_for only bounds ACQUISITION (10s); once acquired,
# drain_flow_run runs with no execution timeout (max_steps=200) so a slow flow
# holds the slot for minutes, starving the shared semaphore for every other
# caller of that (org, provider).  We wrap the post-acquisition execution in
# asyncio.wait_for(timeout=_FLOW_PROVIDER_EXEC_TIMEOUT_S) and raise
# AppError('provider_timeout', 504) on TimeoutError, releasing the slot in
# finally.  The same budget is threaded into drain_flow_run(wall_timeout_s=...)
# so the drain self-aborts even if the outer wait_for is somehow bypassed.
# Override via NUBI_FLOW_PROVIDER_EXEC_TIMEOUT_S (seconds; default 120).
_DEFAULT_FLOW_PROVIDER_EXEC_TIMEOUT_S = 120.0
_FLOW_PROVIDER_EXEC_TIMEOUT_S: float = float(
    os.environ.get(
        "NUBI_FLOW_PROVIDER_EXEC_TIMEOUT_S", _DEFAULT_FLOW_PROVIDER_EXEC_TIMEOUT_S
    )
)

# Registry: (org_id, provider_id) -> asyncio.Semaphore
# Implemented as an OrderedDict so we can evict the least-recently-used idle
# entry when the registry would otherwise grow beyond _EMBED_SEM_REGISTRY_CAP.
# Invariant: a semaphore with _value < _EMBED_FLOW_CONCURRENCY has in-flight or
# waiting callers and MUST NOT be evicted.
import collections as _collections

_embed_semaphores: "_collections.OrderedDict[tuple[str, str], asyncio.Semaphore]" = (
    _collections.OrderedDict()
)
_embed_semaphores_lock = threading.Lock()

# Non-embed (interactive) flow-provider semaphore registry — parallel to the
# embed registry but with its own ceiling and concurrency cap.
_flow_provider_semaphores: "_collections.OrderedDict[tuple[str, str], asyncio.Semaphore]" = (
    _collections.OrderedDict()
)
_flow_provider_semaphores_lock = threading.Lock()


def _embed_semaphore_is_idle(sem: asyncio.Semaphore) -> bool:
    """Return True when *sem* has no in-flight or waiting callers.

    A semaphore is idle when its internal counter equals the initial value set
    at construction (_EMBED_FLOW_CONCURRENCY) — i.e. all tokens are free and
    no coroutines are waiting on it.  Such an entry can safely be evicted.

    asyncio.Semaphore exposes ``_value`` (the current token count) as a private
    attribute; there is no public API for this in the stdlib.  The check is safe
    because this function is only called while holding _embed_semaphores_lock and
    while the event loop is idle (no other coroutine can acquire/release the sem
    between the check and the eviction decision under the GIL).
    """
    # _value is the remaining token count; when equal to the concurrency cap all
    # tokens are free.  When < cap at least one caller holds a token or is waiting.
    #
    # Safety: if _value is absent (Python upgrade / alternate runtime), default to
    # the concurrency cap so the semaphore is conservatively treated as IDLE (safe
    # to evict).  The wrong default (0) would classify every semaphore as "in use",
    # preventing LRU eviction and silently un-enforcing the per-(org,provider) cap.
    return getattr(sem, "_value", _EMBED_FLOW_CONCURRENCY) >= _EMBED_FLOW_CONCURRENCY


def _flow_provider_semaphore_is_idle(sem: asyncio.Semaphore) -> bool:
    """Return True when *sem* (non-embed flow registry) has no in-flight callers."""
    # Same conservative fallback: absent _value -> treat as idle (safe to evict).
    return getattr(sem, "_value", _FLOW_PROVIDER_CONCURRENCY) >= _FLOW_PROVIDER_CONCURRENCY


def _evict_idle_embed_semaphores() -> None:
    """Evict LRU idle entries from _embed_semaphores until len <= cap.

    Called while holding _embed_semaphores_lock.  Only removes entries whose
    semaphore is idle (no in-flight or waiting callers).  If the registry is
    over-cap but all entries are in-use, no eviction happens — correctness is
    preserved at the cost of temporarily exceeding the cap.
    """
    target = _EMBED_SEM_REGISTRY_CAP - 1  # make room for one new entry
    # Iterate in LRU order (oldest first in OrderedDict); collect idle keys.
    idle_keys = [
        k for k, sem in _embed_semaphores.items() if _embed_semaphore_is_idle(sem)
    ]
    # Evict oldest idle entries until we're at or below target.
    for key in idle_keys:
        if len(_embed_semaphores) <= target:
            break
        _embed_semaphores.pop(key, None)


def _evict_idle_flow_provider_semaphores() -> None:
    """Evict LRU idle entries from _flow_provider_semaphores until len <= cap.

    Mirrors _evict_idle_embed_semaphores for the non-embed registry.
    Called while holding _flow_provider_semaphores_lock.
    """
    target = _FLOW_PROVIDER_SEM_CAP - 1
    idle_keys = [
        k for k, sem in _flow_provider_semaphores.items()
        if _flow_provider_semaphore_is_idle(sem)
    ]
    for key in idle_keys:
        if len(_flow_provider_semaphores) <= target:
            break
        _flow_provider_semaphores.pop(key, None)


def _get_embed_semaphore(org_id: str, provider_id: str) -> asyncio.Semaphore:
    """Return the asyncio.Semaphore for *(org_id, provider_id)*, creating it once.

    LRU-capped: when the registry is at _EMBED_SEM_REGISTRY_CAP capacity, idle
    entries (no in-flight or waiting callers) are evicted in LRU order before the
    new entry is inserted.  In-use semaphores are never evicted.
    """
    key = (org_id, provider_id)
    with _embed_semaphores_lock:
        sem = _embed_semaphores.get(key)
        if sem is not None:
            # Move to MRU position (most recently used).
            _embed_semaphores.move_to_end(key)
            return sem
        # New entry: evict idle LRU entries if at capacity.
        if len(_embed_semaphores) >= _EMBED_SEM_REGISTRY_CAP:
            _evict_idle_embed_semaphores()
        sem = asyncio.Semaphore(_EMBED_FLOW_CONCURRENCY)
        _embed_semaphores[key] = sem
        return sem


def _get_flow_provider_semaphore(org_id: str, provider_id: str) -> asyncio.Semaphore:
    """Return the non-embed asyncio.Semaphore for *(org_id, provider_id)*.

    LRU-capped via _FLOW_PROVIDER_SEM_CAP; concurrency limited to
    _FLOW_PROVIDER_CONCURRENCY.  Mirrors _get_embed_semaphore for interactive
    (non-embed) flow-provider callers.
    """
    key = (org_id, provider_id)
    with _flow_provider_semaphores_lock:
        sem = _flow_provider_semaphores.get(key)
        if sem is not None:
            _flow_provider_semaphores.move_to_end(key)
            return sem
        if len(_flow_provider_semaphores) >= _FLOW_PROVIDER_SEM_CAP:
            _evict_idle_flow_provider_semaphores()
        sem = asyncio.Semaphore(_FLOW_PROVIDER_CONCURRENCY)
        _flow_provider_semaphores[key] = sem
        return sem

# ---------------------------------------------------------------------------
# Per-connector threading lock registry
# ---------------------------------------------------------------------------
# DuckDB connections (including the demo singleton) are NOT safe for concurrent
# use from multiple OS threads.  asyncio.to_thread spawns executor threads, so
# two concurrent inline-provider requests would both call connector.execute on
# the same shared DuckDB connection from different threads — leading to crashes
# or data corruption.
#
# Fix: serialise all execute calls that share the same DuckDBConnector instance
# via a per-instance threading.Lock.  The lock is stored in a module-level
# WeakKeyDictionary so it is created once per connector object and is
# automatically collected when the connector is GC'd.
#
# This satisfies the HARD requirement: keep the asyncio.to_thread offload (the
# event loop is never blocked) but guarantee at most one thread executes against
# a given DuckDB connection at a time.
import weakref as _weakref

_connector_locks: "_weakref.WeakKeyDictionary[object, threading.Lock]" = (
    _weakref.WeakKeyDictionary()
)
_connector_locks_mutex = threading.Lock()  # guards _connector_locks itself


def _get_connector_lock(connector: object) -> threading.Lock:
    """Return the threading.Lock for *connector*, creating it if needed."""
    with _connector_locks_mutex:
        lock = _connector_locks.get(connector)
        if lock is None:
            lock = threading.Lock()
            _connector_locks[connector] = lock
        return lock


def _execute_with_lock(connector: object, physical_plan: object) -> "pa.Table":
    """Execute *physical_plan* on *connector* under the connector's thread lock.

    Called from asyncio.to_thread so the event loop is not blocked, and at most
    one thread may use a given connector simultaneously (DuckDB thread-safety).
    """
    lock = _get_connector_lock(connector)
    with lock:
        return connector.execute(physical_plan)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SQL identifier validation
# ---------------------------------------------------------------------------

# Strict identifier pattern: must start with a letter or underscore, followed
# by letters, digits, or underscores only.  This matches the SQL standard for
# unquoted identifiers and makes it safe to interpolate into SQL without
# quoting.  Any other character (spaces, semicolons, dashes, quotes, etc.)
# would indicate an attacker-influenced or mis-configured result name.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_result_name(name: str) -> None:
    """Raise ``AppError`` if *name* is not a safe SQL identifier.

    Called before interpolating a provider result-name into inline SQL
    (``SELECT * FROM {name}``).  An attacker who can influence the result-name
    — e.g. via a crafted DashboardSpec payload — could otherwise inject
    arbitrary SQL.

    Accepts: letters, digits, and underscores; must start with a letter or
    underscore (``[A-Za-z_][A-Za-z0-9_]*``).

    Raises
    ------
    AppError("invalid_result_name", 400)
        When *name* contains characters outside the allowed set.
    """
    if not _IDENTIFIER_RE.match(name):
        raise AppError(
            "invalid_result_name",
            f"Provider result name {name!r} is not a valid SQL identifier "
            "(must match [A-Za-z_][A-Za-z0-9_]*).",
            400,
        )


# ---------------------------------------------------------------------------
# Cache key helpers
# ---------------------------------------------------------------------------


def _rls_hash(policies: dict[str, Any]) -> str:
    """Return a stable 16-char hex digest of *policies* for use in cache keys.

    The hash incorporates the full policies dict so different tenants NEVER share
    a cache entry — even for structurally identical queries.
    """
    canonical = json.dumps(policies, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _params_hash(params: dict[str, Any]) -> str:
    """Return a stable 16-char hex digest of *params*."""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _provider_cache_key(
    org_id: str,
    provider_id: str,
    params: dict[str, Any],
    policies: dict[str, Any],
    board_id: str = "",
) -> str:
    """Composite cache key: ``provider:<org_id>:<board_id>:<pid>:<params_hash>:<rls_hash>``.

    ``org_id`` is the first component so two different orgs sharing the same
    ``provider_id`` and empty policies can NEVER collide in the cache.

    ``board_id`` is included so the same ``provider_id`` used on two different
    boards within the same org gets distinct cache entries — a cross-board
    collision would otherwise serve board-A's data to board-B when both declare
    a provider with the same id but different board-level context.

    Stored in the base-scan cache namespace so it shares TTL + invalidation
    infrastructure without colliding with exact-result plan keys.
    """
    return f"provider:{org_id}:{board_id}:{provider_id}:{_params_hash(params)}:{_rls_hash(policies)}"


# ---------------------------------------------------------------------------
# Arrow serialisation helpers (re-use the existing IPC helpers when available)
# ---------------------------------------------------------------------------


def _tables_to_bytes(
    tables: dict[str, pa.Table],
    max_bytes: int = 0,
) -> bytes:
    """Serialise *tables* to a simple JSON-framed IPC byte blob.

    Format:
        4-byte big-endian count N
        then N frames, each:
            4-byte big-endian name_len
            name bytes (UTF-8)
            4-byte big-endian ipc_len
            ipc bytes (Arrow IPC stream)

    This format is trivially parseable without Arrow on the other end and
    survives the cache round-trip as raw bytes.

    Parameters
    ----------
    max_bytes:
        When > 0, raises ``AppError("provider_result_too_large", 422)`` as soon
        as the running serialised total would exceed this limit — BEFORE
        serialising further tables.  This prevents materialising GiBs of Arrow
        IPC when a provider returns many large tables.  Pass
        ``_PROVIDER_MAX_BYTES`` here so the cap fires early rather than after
        full materialisation.
    """
    import io
    import struct

    buf = io.BytesIO()
    n = len(tables)
    buf.write(struct.pack(">I", n))
    running_bytes = 4  # the count header we just wrote
    for name, tbl in tables.items():
        name_b = name.encode("utf-8")
        ipc_buf = io.BytesIO()
        writer = pa.ipc.new_stream(ipc_buf, tbl.schema)
        writer.write_table(tbl)
        writer.close()
        ipc_bytes = ipc_buf.getvalue()
        # 4 (name_len) + len(name_b) + 4 (ipc_len) + len(ipc_bytes)
        frame_size = 4 + len(name_b) + 4 + len(ipc_bytes)
        if max_bytes > 0 and running_bytes + frame_size > max_bytes:
            raise AppError(
                "provider_result_too_large",
                f"Provider serialised result exceeds {max_bytes:,} bytes limit "
                f"(set NUBI_PROVIDER_MAX_BYTES to raise the limit).",
                422,
            )
        buf.write(struct.pack(">I", len(name_b)))
        buf.write(name_b)
        buf.write(struct.pack(">I", len(ipc_bytes)))
        buf.write(ipc_bytes)
        running_bytes += frame_size
    return buf.getvalue()


def _bytes_to_tables(data: bytes) -> dict[str, pa.Table]:
    """Deserialise a blob produced by :func:`_tables_to_bytes`."""
    import io
    import struct

    buf = io.BytesIO(data)
    (n,) = struct.unpack(">I", buf.read(4))
    tables: dict[str, pa.Table] = {}
    for _ in range(n):
        (name_len,) = struct.unpack(">I", buf.read(4))
        name = buf.read(name_len).decode("utf-8")
        (ipc_len,) = struct.unpack(">I", buf.read(4))
        ipc_bytes = buf.read(ipc_len)
        reader = pa.ipc.open_stream(io.BytesIO(ipc_bytes))
        tables[name] = reader.read_all()
    return tables


# ---------------------------------------------------------------------------
# Flow-backed provider
# ---------------------------------------------------------------------------


async def _resolve_flow_provider(
    provider: Any,  # DataProvider
    merged_params: dict[str, Any],
    org_id: str,
    claims: dict[str, Any],
    *,
    org_flows_by_key: dict[str, Any] | None = None,
    exec_timeout_s: float = 0.0,
) -> dict[str, pa.Table]:
    """Execute a ``kind='flow'`` provider and return named Arrow tables.

    The provider ``id`` is used as the Flow lookup key.  The flow is identified
    by looking for a flow in the org whose ``name`` (or ``id``) matches the
    provider id.

    Parameters
    ----------
    org_flows_by_key:
        Optional pre-built mapping of ``{id: flow, name: flow, ...}`` for all
        flows in *org_id*, produced once per board-load by
        ``resolve_provider_data`` to avoid O(providers × flows) list scans.
        When *None* the function falls back to calling ``list_flows`` itself
        (backwards-compatible for direct callers / tests that patch
        ``_resolve_flow_provider``).

    SECURITY: ``claims["policies"]`` from the verified token is forwarded to
    the flow runtime as RLS context.  No RLS from request body.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from app.flows.runtime import drain_flow_run, materialize_flow_run  # noqa: PLC0415
    from app.flows.store import get_flow_store  # noqa: PLC0415

    store = get_flow_store()
    policies: dict[str, Any] = dict((claims or {}).get("policies") or {})

    # ── Locate the flow by provider id ────────────────────────────────────────
    # Try direct id lookup first; fall back to name-match within org.
    flow = await store.get_flow(provider.id)
    if flow is None or str(flow.get("org_id", "")) != org_id:
        # Try name-based lookup using the pre-built per-board dict (avoids an
        # extra list_flows call per provider → O(1) per provider).
        if org_flows_by_key is not None:
            flow = org_flows_by_key.get(provider.id)
        else:
            # Fallback: direct list_flows call (e.g. when called from tests
            # that mock _resolve_flow_provider directly).
            all_flows = await store.list_flows(org_id=org_id)
            flow = next(
                (f for f in all_flows if f.get("name") == provider.id or str(f.get("id")) == provider.id),
                None,
            )

    if flow is None:
        raise AppError(
            "provider_flow_not_found",
            f"No flow found for provider id {provider.id!r} in org {org_id!r}.",
            404,
        )

    if str(flow.get("org_id", "")) != org_id:
        raise AppError(
            "provider_flow_not_found",
            f"Flow for provider {provider.id!r} does not belong to org {org_id!r}.",
            403,
        )

    # ── Build claims for the flow run ─────────────────────────────────────────
    run_claims: dict[str, Any] = {
        "policies": policies,
        "sub": (claims or {}).get("sub", ""),
        "org_id": org_id,
    }

    now = datetime.now(timezone.utc)
    flow_run = await materialize_flow_run(store, flow, merged_params, "agent", now)
    # Layer 2: pass the execution budget so the drain self-aborts (TimeoutError)
    # at the deadline even if the outer asyncio.wait_for is bypassed.  0 keeps
    # legacy unbounded behaviour for direct callers / tests.
    flow_run = await drain_flow_run(
        store,
        flow_run["id"],
        now,
        claims=run_claims,
        wall_timeout_s=exec_timeout_s,
    )

    if flow_run.get("state") != "success":
        raise AppError(
            "provider_flow_failed",
            f"Flow for provider {provider.id!r} finished with state={flow_run.get('state')!r}.",
            500,
        )

    # ── Collect named results from task_runs ──────────────────────────────────
    _fetch_limit = _MAX_TASK_RUNS + 1
    task_runs = await store.list_task_runs(flow_run["id"], limit=_fetch_limit)
    if len(task_runs) > _MAX_TASK_RUNS:
        logger.warning(
            "flow provider %r run %r: task_run count exceeds ceiling %d — "
            "truncating to %d (set NUBI_MAX_TASK_RUNS_CEILING to raise limit). "
            "Some task_run results may be silently omitted.",
            provider.id,
            flow_run["id"],
            _MAX_TASK_RUNS,
            _MAX_TASK_RUNS,
        )
        task_runs = task_runs[:_MAX_TASK_RUNS]
    result_names = {r.name for r in provider.results}
    tables: dict[str, pa.Table] = {}

    for tr in task_runs:
        key = tr.get("task_key", "")
        if key not in result_names:
            continue
        if tr.get("state") != "success":
            continue
        result_payload = tr.get("result") or {}
        # The executor stores Arrow tables serialised as IPC bytes or as
        # columns/rows dicts.  Handle both shapes.
        arrow_bytes = result_payload.get("__arrow_ipc__")
        if arrow_bytes and isinstance(arrow_bytes, (bytes, bytearray)):
            reader = pa.ipc.open_stream(arrow_bytes)
            tables[key] = reader.read_all()
        elif "columns" in result_payload and "rows" in result_payload:
            columns: list[str] = result_payload["columns"]
            rows: list[list[Any]] = result_payload["rows"]
            arrays = [pa.array([r[i] for r in rows]) for i in range(len(columns))]
            tables[key] = pa.table(dict(zip(columns, arrays)))
        else:
            # Wrap scalar result dict as a single-row table.
            if isinstance(result_payload, dict):
                scalar_cols = {k: pa.array([v]) for k, v in result_payload.items()
                               if not k.startswith("__")}
                if scalar_cols:
                    tables[key] = pa.table(scalar_cols)

        # [MED resource] Apply per-table row cap — mirrors the inline path.
        # Each flow result table is row-capped here so downstream serialisation
        # never materialises unbounded Arrow IPC from a single oversized table.
        if key in tables and _ROW_CAP > 0 and tables[key].num_rows > _ROW_CAP:
            logger.warning(
                "flow provider %r result %r: truncating %d rows to %d "
                "(set NUBI_COLLECT_ROW_CAP to raise limit)",
                provider.id,
                key,
                tables[key].num_rows,
                _ROW_CAP,
            )
            tables[key] = tables[key].slice(0, _ROW_CAP)

    # Fill in empty tables for declared results that produced nothing.
    for r in provider.results:
        if r.name not in tables:
            logger.warning(
                "provider %r: declared result %r not found in flow task_runs; "
                "returning empty table.",
                provider.id,
                r.name,
            )
            tables[r.name] = pa.table({})

    return tables


# ---------------------------------------------------------------------------
# Inline-provider connector resolution
# ---------------------------------------------------------------------------


async def _resolve_org_connector(
    org_id: str,
    repo: Any,
) -> tuple[Any, bool]:
    """Return (connector, owned) for the org's real datastore, or the demo fallback.

    Mirrors the pattern in ``app.dashboards.collect._resolve_connector``:

    * Lists all datastores for *org_id* (org-scoped — no cross-tenant access).
    * Picks the first non-system, non-demo datastore (i.e. a real user-configured
      connector).
    * Builds and returns that connector so ``_resolve_inline_provider`` executes
      against the org's actual data source, not always the demo singleton.
    * Falls back to the demo connector when no real connector is configured
      (preserves existing demo/dev behaviour).

    ``owned`` is ``True`` when the caller is responsible for closing the
    connector (freshly-created real connector).  ``False`` means the returned
    connector is the shared demo singleton and must NOT be closed.

    Security: the repo lookup is already org-scoped (``repo.list("datastores",
    org_id)``), so cross-tenant reads are impossible here.
    """
    from app.routes.query import _get_demo_connector  # noqa: PLC0415

    try:
        datastores = await repo.list("datastores", org_id)
    except Exception:  # noqa: BLE001
        datastores = []

    # Filter out system/demo datastores — only pick real user-configured connectors.
    _DEMO_SYSTEM_TYPES = frozenset({"__demo__", "__demo_hidden__"})
    real_ds = None
    for ds in (datastores or []):
        cfg = ds.get("config") or {}
        ctype = cfg.get("connector_type") or cfg.get("type") or ""
        # Skip system-flagged rows (e.g. the demo-hidden marker) and the virtual
        # demo connector sentinel.
        if cfg.get("system") or ctype in _DEMO_SYSTEM_TYPES:
            continue
        real_ds = ds
        break

    if real_ds is None:
        # No real connector configured — fall back to the demo singleton.
        return _get_demo_connector(), False

    cfg: dict[str, Any] = dict(real_ds.get("config") or {})
    ctype = cfg.get("connector_type") or cfg.get("type")

    try:
        from app.connectors.registry import get_connector_registry  # noqa: PLC0415

        factory = get_connector_registry().get(ctype)
        if factory is None:
            logger.warning(
                "inline provider: org %r datastore type %r has no registered factory; "
                "falling back to demo connector.",
                org_id,
                ctype,
            )
            return _get_demo_connector(), False

        if ctype == "duckdb":
            db_path = cfg.get("database") or cfg.get("path")
            if db_path and db_path != ":memory:":
                import duckdb  # noqa: PLC0415

                from app.connectors.duckdb_conn import harden_connection as _harden  # noqa: PLC0415

                conn = duckdb.connect(database=db_path, read_only=True)
                _harden(conn, disable_external_access=True)
                return factory(conn), True
            return factory(), True
        if ctype == "postgres":
            dsn = cfg.get("dsn")
            if dsn is None:
                host = cfg.get("host", "localhost")
                port = cfg.get("port", 5432)
                dbname = cfg.get("dbname") or cfg.get("database") or "postgres"
                user = cfg.get("user") or cfg.get("username") or "postgres"
                password = cfg.get("password", "")
                dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
            return factory(dsn), True

        return factory(cfg), True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "inline provider: failed to build connector for org %r datastore type %r: %s; "
            "falling back to demo connector.",
            org_id,
            ctype,
            exc,
        )
        return _get_demo_connector(), False


# ---------------------------------------------------------------------------
# Inline provider
# ---------------------------------------------------------------------------


async def _resolve_inline_provider(
    provider: Any,  # DataProvider
    merged_params: dict[str, Any],
    org_id: str,
    claims: dict[str, Any],
    repo: Any,
) -> dict[str, pa.Table]:
    """Execute a ``kind='inline'`` provider and return named Arrow tables.

    Runs the provider's ``base_cte`` preamble and per-result SQL via the same
    connector pipeline as ``run_query_rows`` (RLS from claims/token only).

    For simplicity this wave runs a single query per result-set using the
    ``base_cte`` as a WITH preamble.  The ``base_cte`` field is optional;
    when absent the result name is used as a standalone query id lookup.
    """
    from app.dashboards.collect import run_query_rows  # noqa: PLC0415

    policies: dict[str, Any] = dict((claims or {}).get("policies") or {})
    tables: dict[str, pa.Table] = {}

    # FIX [LOW N+1 — regression from fix-19]: resolve the org connector ONCE per
    # provider call and reuse it for every result iteration.  The previous code
    # called _resolve_org_connector (which calls repo.list("datastores", org_id))
    # inside the per-result loop, causing O(results) DB roundtrips per provider.
    # Hoisting the lookup here reduces it to at most 1 roundtrip per provider
    # regardless of how many named results the provider declares.
    _shared_connector: Any = None
    _shared_connector_owned: bool = False
    if provider.base_cte:
        try:
            _shared_connector, _shared_connector_owned = await _resolve_org_connector(
                org_id, repo
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "inline provider %r: failed to resolve org connector (pre-loop): %s; "
                "per-result fallback will be attempted.",
                provider.id,
                exc,
            )

    try:
        for r in provider.results:
            if provider.base_cte:
                # Run the inline CTE + a trivial SELECT of the result name.
                # In practice an inline provider with a base_cte exposes named CTEs
                # whose names map to result names, e.g.:
                #   WITH revenue_by_day AS (SELECT ... FROM orders ...)
                # The result name is the CTE alias; we SELECT * FROM it.
                # FIX [MED DB-level LIMIT]: wrap the inline query in an outer SELECT
                # so the DB never returns more than _ROW_CAP rows into memory.
                # The post-fetch slice below is kept as a backstop but the heavy
                # lifting now happens inside the DB engine before any data is
                # transferred to the Python process.
                # FIX [LOW injection]: validate result-name before interpolation
                # into SQL so an attacker-influenced name cannot inject SQL.
                _validate_result_name(r.name)
                inner_sql = f"{provider.base_cte.rstrip(';')} SELECT * FROM {r.name}"
                if _ROW_CAP > 0:
                    sql = f"SELECT * FROM ({inner_sql}) AS _nubi_inline_capped LIMIT {_ROW_CAP}"
                else:
                    sql = inner_sql
                try:
                    from app.connectors import plan as planner_plan  # noqa: PLC0415

                    physical_plan = planner_plan(
                        sql=sql, claims={"policies": policies}, params=[]
                    )
                    # Use the connector resolved once above (hoisted out of the loop).
                    # FIX [MED connector]: resolve the org's actual connector rather
                    # than always falling back to the demo singleton.
                    # If the pre-loop resolve failed (None), fall back to resolving
                    # per-result as a last resort so individual result errors stay isolated.
                    if _shared_connector is not None:
                        _connector = _shared_connector
                    else:
                        _connector, _ = await _resolve_org_connector(org_id, repo)
                    # FIX [LOW async]: connector.execute is a synchronous (blocking)
                    # DuckDB call.  Wrap it in asyncio.to_thread so it does not
                    # block the event loop on a cache miss.
                    # FIX [HIGH concurrency]: DuckDB connections are NOT thread-safe.
                    # Two concurrent to_thread calls on the same shared demo-singleton
                    # connection would race.  _execute_with_lock acquires a per-connector
                    # threading.Lock before calling execute, serialising concurrent
                    # threads while keeping the event-loop offload benefit of to_thread.
                    arrow_table = await asyncio.to_thread(
                        _execute_with_lock, _connector, physical_plan
                    )
                    # FIX [MED resource]: cap rows to _ROW_CAP to prevent unbounded
                    # Arrow IPC payloads from inline providers.
                    if _ROW_CAP > 0 and arrow_table.num_rows > _ROW_CAP:
                        logger.warning(
                            "inline provider %r result %r: truncating %d rows to %d "
                            "(set NUBI_COLLECT_ROW_CAP to raise limit)",
                            provider.id,
                            r.name,
                            arrow_table.num_rows,
                            _ROW_CAP,
                        )
                        arrow_table = arrow_table.slice(0, _ROW_CAP)
                    tables[r.name] = arrow_table
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "inline provider %r result %r: query failed: %s",
                        provider.id,
                        r.name,
                        exc,
                    )
                    tables[r.name] = pa.table({})
            else:
                # Treat the result name as a registered query id.
                try:
                    columns, rows = await run_query_rows(r.name, org_id, repo, policies)
                    # FIX [LOW row cap]: cap rows BEFORE building the Arrow table so
                    # an unbounded run_query_rows result cannot materialise a huge
                    # pa.array in memory.  Mirror the same DB-level LIMIT + post-fetch
                    # slice pattern used by the base_cte branch above.
                    if _ROW_CAP > 0 and len(rows) > _ROW_CAP:
                        logger.warning(
                            "inline provider %r result %r (non-base_cte): truncating "
                            "%d rows to %d (set NUBI_COLLECT_ROW_CAP to raise limit)",
                            provider.id,
                            r.name,
                            len(rows),
                            _ROW_CAP,
                        )
                        rows = rows[:_ROW_CAP]
                    arrays = [
                        pa.array([row[i] for row in rows]) for i in range(len(columns))
                    ]
                    tables[r.name] = pa.table(dict(zip(columns, arrays)))
                except AppError as exc:
                    logger.warning(
                        "inline provider %r result %r: query error %s",
                        provider.id,
                        r.name,
                        exc.code,
                    )
                    tables[r.name] = pa.table({})
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "inline provider %r result %r: unexpected error: %s",
                        provider.id,
                        r.name,
                        exc,
                    )
                    tables[r.name] = pa.table({})

    finally:
        # Close freshly-created (owned) connectors to release their underlying
        # connections.  The shared demo singleton must NOT be closed (owned=False)
        # — it is a module-level singleton.  This finally block replaces the old
        # per-iteration close that was inside the loop.
        if _shared_connector_owned and _shared_connector is not None:
            try:
                _shared_connector.close()
            except Exception:  # noqa: BLE001
                pass

    return tables


# ---------------------------------------------------------------------------
# Materialized flow-provider resolver
# ---------------------------------------------------------------------------


async def _resolve_materialized_flow_provider(
    provider: Any,  # DataProvider with kind='flow'
    merged_params: dict[str, Any],
    org_id: str,
    claims: dict[str, Any],
    *,
    org_flows_by_key: dict[str, Any] | None = None,
) -> dict[str, pa.Table] | None:
    """Attempt to serve a flow provider from its latest materialized run result.

    Looks up the flow backing the provider, finds the most-recent SUCCESSFUL
    flow_run, reads the task_run results for that run, and extracts the named
    result tables declared by the provider.

    Parameters
    ----------
    org_flows_by_key:
        Optional pre-built mapping of ``{id: flow, name: flow, ...}`` for all
        flows in *org_id*, produced once per board-load by ``resolve_provider_data``
        to avoid the O(providers × flows) list_flows scan.  When *None* the
        function falls back to calling ``list_flows`` itself (backwards-compatible
        for direct callers / tests).

    Returns
    -------
    dict[str, pa.Table]
        The named Arrow tables from the latest successful run, or ``None`` when
        no successful run exists yet (caller should fall back to ephemeral).

    Security
    --------
    * ``org_id`` is verified on the flow before accepting its run results —
      cross-org reads are impossible.
    * The data is sourced ONLY from the flow_store (task_run.result), never
      from a caller-supplied URI or path.
    """
    from app.flows.store import get_flow_store  # noqa: PLC0415

    store = get_flow_store()

    # ── 1. Locate the flow for this provider (same logic as ephemeral path). ──
    flow = await store.get_flow(provider.id)
    if flow is None or str(flow.get("org_id", "")) != org_id:
        # Try name-based lookup using the pre-built per-board dict when available
        # (avoids an extra list_flows call per provider → O(1) per provider).
        if org_flows_by_key is not None:
            flow = org_flows_by_key.get(provider.id)
        else:
            # Fallback: direct list_flows call (e.g. when called from tests that
            # mock _resolve_materialized_flow_provider directly).
            all_flows = await store.list_flows(org_id=org_id)
            flow = next(
                (
                    f
                    for f in all_flows
                    if f.get("name") == provider.id or str(f.get("id")) == provider.id
                ),
                None,
            )

    if flow is None or str(flow.get("org_id", "")) != org_id:
        # Flow not found in this org — no materialized result possible.
        return None

    flow_id = str(flow["id"])

    # ── 2. Find the latest SUCCESSFUL flow_run for this flow. ─────────────────
    # We fetch a small window of recent runs and pick the first success.
    # Limit=10 is generous; a healthy scheduled flow should have a success near
    # the top.  We avoid limit=1 because the very latest run might still be
    # running or have failed — we want the most-recent COMPLETED success.
    recent_runs = await store.list_flow_runs(flow_id, limit=10)
    latest_success: dict[str, Any] | None = next(
        (r for r in recent_runs if r.get("state") == "success"),
        None,
    )

    if latest_success is None:
        # No successful run found yet — fall back to ephemeral.
        return None

    run_id = str(latest_success["id"])

    # ── 3. Read task_run results from the successful run. ────────────────────
    _fetch_limit = _MAX_TASK_RUNS + 1
    task_runs = await store.list_task_runs(run_id, limit=_fetch_limit)
    if len(task_runs) > _MAX_TASK_RUNS:
        logger.warning(
            "materialized flow provider %r run %r: task_run count exceeds ceiling %d — "
            "truncating to %d (set NUBI_MAX_TASK_RUNS_CEILING to raise limit). "
            "Some task_run results may be silently omitted.",
            provider.id,
            run_id,
            _MAX_TASK_RUNS,
            _MAX_TASK_RUNS,
        )
        task_runs = task_runs[:_MAX_TASK_RUNS]
    result_names = {r.name for r in provider.results}
    tables: dict[str, pa.Table] = {}

    for tr in task_runs:
        key = tr.get("task_key", "")
        if key not in result_names:
            continue
        if tr.get("state") != "success":
            continue
        result_payload = tr.get("result") or {}
        # Same decoding logic as _resolve_flow_provider.
        arrow_bytes = result_payload.get("__arrow_ipc__")
        if arrow_bytes and isinstance(arrow_bytes, (bytes, bytearray)):
            reader = pa.ipc.open_stream(arrow_bytes)
            tables[key] = reader.read_all()
        elif "columns" in result_payload and "rows" in result_payload:
            columns: list[str] = result_payload["columns"]
            rows: list[list[Any]] = result_payload["rows"]
            arrays = [pa.array([r[i] for r in rows]) for i in range(len(columns))]
            tables[key] = pa.table(dict(zip(columns, arrays)))
        else:
            if isinstance(result_payload, dict):
                scalar_cols = {
                    k: pa.array([v])
                    for k, v in result_payload.items()
                    if not k.startswith("__")
                }
                if scalar_cols:
                    tables[key] = pa.table(scalar_cols)

        # Apply per-table row cap (same invariant as _resolve_flow_provider).
        if key in tables and _ROW_CAP > 0 and tables[key].num_rows > _ROW_CAP:
            logger.warning(
                "materialized flow provider %r result %r: truncating %d rows to %d",
                provider.id,
                key,
                tables[key].num_rows,
                _ROW_CAP,
            )
            tables[key] = tables[key].slice(0, _ROW_CAP)

    # ── 4. Fill missing declared results with empty tables. ───────────────────
    for r in provider.results:
        if r.name not in tables:
            logger.warning(
                "materialized provider %r: declared result %r not found in "
                "latest run %r task_runs; returning empty table.",
                provider.id,
                r.name,
                run_id,
            )
            tables[r.name] = pa.table({})

    return tables


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


async def resolve_provider_data(
    board_id: str,
    provider_id: str,
    params: dict[str, Any],
    org_id: str,
    claims: dict[str, Any],
    repo: Any,
    *,
    mode: str = "ephemeral",
    is_embed: bool = False,
    skip_metering: bool = False,
) -> dict[str, pa.Table]:
    """Resolve a single DataProvider and return named Arrow tables.

    Parameters
    ----------
    board_id:
        The board id to resolve (org-scoped).
    provider_id:
        The ``DataProvider.id`` to execute.
    params:
        Request-time parameter overrides merged with the provider's declared
        ``params`` (provider defaults are a baseline; request params win).
    org_id:
        The caller's resolved org id.  Board + any datastore are looked up
        scoped to this org — never cross-org.
    claims:
        The **verified** token claims.  ONLY ``claims["policies"]`` is used
        for RLS — never sourced from a request body.
    repo:
        Active repository implementation.
    mode:
        ``'ephemeral'`` (default) — resolve in-process with TTL cache.
        ``'materialized'`` — serve from the latest successful flow_run's
        task_run results (scheduled → derived tables).  Falls back to an
        ephemeral live run when no successful run exists yet.
    is_embed:
        When ``True`` the caller is an embed token.  Quota enforcement is
        SKIPPED because embed viewers are never metered.  Sets ``skip_metering``
        to ``True`` automatically (see below).
    skip_metering:
        When ``True`` quota enforcement is SKIPPED entirely on a cache miss.
        The route sets this for BOTH embed tokens (``is_embed=True``) AND
        first-party users whose org role is ``'viewer'``.  Viewers — whether
        embed or first-party — are NEVER metered; compute is charged to the org
        only on first-party writer/admin/member cache misses.  The cache still
        serves non-metered callers: a metered miss populates the cache and
        subsequent viewer requests are served from it at zero marginal cost.

    Returns
    -------
    dict[str, pa.Table]
        One Arrow table per declared ``provider.results[*].name``.

    Raises
    ------
    AppError("board_not_found", 404)
        When no board with *board_id* exists in *org_id*.
    AppError("provider_not_found", 404)
        When *provider_id* does not name a declared provider in the board's
        spec.
    """
    # ── Materialized mode guard (checked before board lookup) ─────────────────
    # When mode='materialized' we attempt to serve from the latest successful
    # flow run's task_run results (derived tables / scheduled flow outputs).
    # If no materialized result exists yet we fall through to the ephemeral
    # (live) path automatically.  501 is NEVER raised here — the provider
    # either serves from cache, from the materialized run, or falls back to
    # a live execution.

    # ── Org-scoped board lookup ───────────────────────────────────────────────
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)

    # ── Resolve spec + provider ───────────────────────────────────────────────
    from app.dashboards.spec import DashboardSpec  # noqa: PLC0415

    config = board.get("config") or {}
    spec_data = config.get("spec") or {}
    try:
        spec = DashboardSpec.model_validate(spec_data) if spec_data else None
    except Exception:  # noqa: BLE001
        spec = None

    provider = None
    if spec is not None:
        for p in spec.data:
            if p.id == provider_id:
                provider = p
                break

    if provider is None:
        raise AppError(
            "provider_not_found",
            f"Provider {provider_id!r} is not declared in board {board_id!r}.",
            404,
        )

    # ── Merge params: provider defaults + request-time overrides ─────────────
    # Provider params may contain {ref: varName} references — resolve literals
    # only here; ref-bound params are deferred (they require the live variable
    # state from the frontend).  Ref-valued entries are kept AS-IS so downstream
    # logic can decide how to handle them.
    merged_params: dict[str, Any] = {}
    for k, v in provider.params.items():
        if isinstance(v, dict) and "ref" in v:
            # Variable reference: leave as-is; the caller already resolved them
            # into the request-time params dict when applicable.
            merged_params[k] = params.get(k, v)
        else:
            merged_params[k] = v
    # Request-time overrides win.
    merged_params.update(params)

    # ── Cache lookup ──────────────────────────────────────────────────────────
    policies: dict[str, Any] = dict((claims or {}).get("policies") or {})
    # FIX [HIGH cross-tenant cache]: org_id is now the first component so two
    # different orgs with the same provider_id + empty policies never collide.
    # FIX [LOW cross-board cache]: board_id is included so the same provider_id
    # on two different boards never shares a cache entry.
    cache_key = _provider_cache_key(org_id, provider_id, merged_params, policies, board_id)

    from app.connectors.cache import get_base_scan, put_base_scan  # noqa: PLC0415

    cached = get_base_scan(cache_key)
    if cached is not None:
        try:
            return _bytes_to_tables(cached)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "provider cache deserialise failed for key %s: %s — re-executing",
                cache_key,
                exc,
            )

    # ── FIX [MED N+1]: Pre-fetch org flows ONCE for all flow-provider paths ──────
    # Both the materialized and ephemeral paths need to locate a flow by provider
    # id.  Building the lookup dict here (once, before the mode branch) means
    # neither path ever calls list_flows again — O(1) lookup per provider instead
    # of O(providers) scans.
    org_flows_by_key: dict[str, Any] = {}
    if provider.kind == "flow":
        from app.flows.store import get_flow_store as _get_flow_store  # noqa: PLC0415

        _store = _get_flow_store()
        _all_org_flows = await _store.list_flows(org_id=org_id)
        for _f in _all_org_flows:
            _fid = str(_f.get("id", ""))
            _fname = _f.get("name", "")
            if _fid:
                org_flows_by_key[_fid] = _f
            if _fname:
                org_flows_by_key[_fname] = _f

    # ── Materialized path (flow providers only) ───────────────────────────────
    # When mode='materialized' and the provider is flow-backed, attempt to serve
    # from the latest SUCCESSFUL flow run's task_run results instead of re-running
    # the flow live.  This implements the "scheduled → derived tables" contract:
    # a scheduled flow writes results into task_runs; the dashboard reads the
    # latest materialized result set without triggering new compute.
    #
    # Fall-back contract: when no successful flow run exists yet (e.g. the
    # schedule has not fired yet), fall through transparently to the ephemeral
    # (live run) path.  This means mode='materialized' NEVER hard-errors on a
    # cold provider — viewers always get something, even if it costs a live run.
    if mode == "materialized" and provider.kind == "flow":
        tables = await _resolve_materialized_flow_provider(
            provider, merged_params, org_id, claims,
            org_flows_by_key=org_flows_by_key,
        )
        if tables is not None:
            # Materialized result found — cache + return; no quota charge because
            # no compute was triggered (reads from stored task_run data only).
            try:
                serialised = _tables_to_bytes(tables, max_bytes=_PROVIDER_MAX_BYTES)
                put_base_scan(
                    cache_key,
                    serialised,
                    tags=[f"org:{org_id}", f"board:{board_id}"],
                )
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "provider cache put failed (materialized) for key %s: %s",
                    cache_key,
                    exc,
                )
            return tables
        # No materialized result yet — fall through to ephemeral execution below.
        logger.info(
            "materialized mode for provider %r: no successful run found; "
            "falling back to ephemeral (live) execution.",
            provider_id,
        )

    # ── Execute provider ──────────────────────────────────────────────────────
    # FIX [MED metering INVARIANT]: enforce quota on a cache miss ONLY for
    # first-party callers whose org role is writer/admin/member.  Viewers are
    # NEVER metered — this covers both embed tokens (is_embed=True) AND
    # first-party users with org role 'viewer' (skip_metering=True set by the
    # route).  The combined flag is: skip if is_embed OR skip_metering.
    _skip = is_embed or skip_metering
    if not _skip:
        from app.features import enforce_quota  # noqa: PLC0415

        await enforce_quota(org_id, "compute_units", amount=1.0)

    if provider.kind == "flow":
        # FIX [MED embed compute amplification]: for embed tokens + flow kind,
        # guard live execution behind a small per-(org_id, provider_id)
        # asyncio.Semaphore so a cache-busting embed client cannot fan out
        # arbitrarily many concurrent live flow runs.  On contention beyond the
        # timeout, raise a 429 "provider_busy" error.  Embed stays unmetered.
        #
        # FIX [MED non-embed compute amplification]: apply the same concurrency
        # guard to interactive (non-embed) flow-provider execution.  N concurrent
        # interactive requests on a cache miss each spawn a full live flow run
        # without any cap.  We use a separate per-(org, provider) registry
        # (_flow_provider_semaphores) with its own ceiling so operators can tune
        # embed and interactive limits independently.
        if is_embed:
            _sem = _get_embed_semaphore(org_id, provider_id)
            _timeout = _EMBED_FLOW_TIMEOUT_S
            _busy_msg = (
                f"Provider {provider_id!r} is currently busy serving other embed "
                f"requests. Retry after a moment."
            )
        else:
            _sem = _get_flow_provider_semaphore(org_id, provider_id)
            _timeout = _FLOW_PROVIDER_TIMEOUT_S
            _busy_msg = (
                f"Provider {provider_id!r} is currently busy. "
                f"Too many concurrent requests — retry after a moment."
            )
        try:
            await asyncio.wait_for(
                _sem.acquire(),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            raise AppError(
                "provider_busy",
                _busy_msg,
                429,
            )
        # FIX [MED event-loop starvation]: the wait_for above only bounds slot
        # ACQUISITION.  Once acquired, drain_flow_run could run for minutes
        # (max_steps=200) holding this per-(org, provider) slot and starving the
        # shared semaphore.  Bound the post-acquisition EXECUTION with a second
        # wait_for; on timeout raise provider_timeout (504).  Either way the slot
        # is released in finally.  Applies to BOTH embed and non-embed paths.
        try:
            tables = await asyncio.wait_for(
                _resolve_flow_provider(
                    provider, merged_params, org_id, claims,
                    org_flows_by_key=org_flows_by_key,
                    exec_timeout_s=_FLOW_PROVIDER_EXEC_TIMEOUT_S,
                ),
                timeout=_FLOW_PROVIDER_EXEC_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            raise AppError(
                "provider_timeout",
                f"Provider {provider_id!r} flow execution exceeded "
                f"{_FLOW_PROVIDER_EXEC_TIMEOUT_S:g}s "
                f"(set NUBI_FLOW_PROVIDER_EXEC_TIMEOUT_S to raise the limit).",
                504,
            )
        finally:
            _sem.release()
    elif provider.kind == "inline":
        tables = await _resolve_inline_provider(provider, merged_params, org_id, claims, repo)
    else:
        raise AppError(
            "unknown_provider_kind",
            f"Unknown provider kind {provider.kind!r}.",
            400,
        )

    # ── [MED resource] Cardinality cap: reject provider responses with too many
    # result tables or too many serialised bytes.  This prevents an unbounded
    # Arrow IPC payload from being materialised in memory and cached.
    if _PROVIDER_MAX_TABLES > 0 and len(tables) > _PROVIDER_MAX_TABLES:
        raise AppError(
            "provider_result_too_large",
            f"Provider {provider_id!r} returned {len(tables)} result tables; "
            f"maximum allowed is {_PROVIDER_MAX_TABLES} "
            f"(set NUBI_PROVIDER_MAX_TABLES to raise the limit).",
            422,
        )

    # ── Cache store ──────────────────────────────────────────────────────────
    # _tables_to_bytes raises AppError("provider_result_too_large", 422) EARLY
    # — as soon as the running serialised total would exceed _PROVIDER_MAX_BYTES
    # — so we never fully materialise an oversized payload before the check fires.
    try:
        serialised = _tables_to_bytes(tables, max_bytes=_PROVIDER_MAX_BYTES)
        put_base_scan(
            cache_key,
            serialised,
            tags=[f"org:{org_id}", f"board:{board_id}"],
        )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("provider cache put failed for key %s: %s", cache_key, exc)

    return tables


# ---------------------------------------------------------------------------
# Multi-table Arrow IPC stream serialiser (for the HTTP route)
# ---------------------------------------------------------------------------


def tables_to_multi_ipc_stream(tables: dict[str, pa.Table]) -> bytes:
    """Serialise *tables* into a concatenated Arrow IPC stream.

    Each table is written as a separate IPC stream message preceded by an 8-byte
    header: 4 bytes big-endian name length + the UTF-8 name bytes + 4 bytes
    big-endian IPC length + the IPC bytes.

    The HTTP route returns this blob with Content-Type
    ``application/vnd.apache.arrow.stream``.  The SpecRenderer on the frontend
    reads the multi-table framing to fan results to bound widgets.
    """
    return _tables_to_bytes(tables)
