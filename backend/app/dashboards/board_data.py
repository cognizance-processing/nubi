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
``materialized``  — hook/branch for scheduled flows that write to derived tables;
                  full scheduling is a later wave.  This module raises
                  ``NotImplementedError`` for the materialized path as a
                  deliberate placeholder so the later wave has a clear extension
                  point.

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

import hashlib
import json
import logging
from typing import Any

import pyarrow as pa

from app.errors import AppError

logger = logging.getLogger(__name__)


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
) -> str:
    """Composite cache key: ``provider:<org_id>:<pid>:<params_hash>:<rls_hash>``.

    ``org_id`` is the first component so two different orgs sharing the same
    ``provider_id`` and empty policies can NEVER collide in the cache.

    Stored in the base-scan cache namespace so it shares TTL + invalidation
    infrastructure without colliding with exact-result plan keys.
    """
    return f"provider:{org_id}:{provider_id}:{_params_hash(params)}:{_rls_hash(policies)}"


# ---------------------------------------------------------------------------
# Arrow serialisation helpers (re-use the existing IPC helpers when available)
# ---------------------------------------------------------------------------


def _tables_to_bytes(tables: dict[str, pa.Table]) -> bytes:
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
    """
    import io
    import struct

    buf = io.BytesIO()
    n = len(tables)
    buf.write(struct.pack(">I", n))
    for name, tbl in tables.items():
        name_b = name.encode("utf-8")
        buf.write(struct.pack(">I", len(name_b)))
        buf.write(name_b)
        ipc_buf = io.BytesIO()
        writer = pa.ipc.new_stream(ipc_buf, tbl.schema)
        writer.write_table(tbl)
        writer.close()
        ipc_bytes = ipc_buf.getvalue()
        buf.write(struct.pack(">I", len(ipc_bytes)))
        buf.write(ipc_bytes)
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
    flow_run = await drain_flow_run(store, flow_run["id"], now, claims=run_claims)

    if flow_run.get("state") != "success":
        raise AppError(
            "provider_flow_failed",
            f"Flow for provider {provider.id!r} finished with state={flow_run.get('state')!r}.",
            500,
        )

    # ── Collect named results from task_runs ──────────────────────────────────
    task_runs = await store.list_task_runs(flow_run["id"])
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

    for r in provider.results:
        if provider.base_cte:
            # Run the inline CTE + a trivial SELECT of the result name.
            # In practice an inline provider with a base_cte exposes named CTEs
            # whose names map to result names, e.g.:
            #   WITH revenue_by_day AS (SELECT ... FROM orders ...)
            # The result name is the CTE alias; we SELECT * FROM it.
            sql = f"{provider.base_cte.rstrip(';')} SELECT * FROM {r.name}"
            try:
                from app.connectors import plan as planner_plan  # noqa: PLC0415
                from app.routes.query import _get_demo_connector  # noqa: PLC0415

                physical_plan = planner_plan(sql=sql, claims={"policies": policies}, params=[])
                connector = _get_demo_connector()
                arrow_table = connector.execute(physical_plan)
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
                arrays = [pa.array([row[i] for row in rows]) for i in range(len(columns))]
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
        ``'materialized'`` — reserved for a later scheduling wave (raises
        ``NotImplementedError``).

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
    AppError("provider_mode_unsupported", 501)
        When ``mode='materialized'`` is requested (later wave hook).
    NotImplementedError
        Same guard as above — also raised directly so callers relying on the
        Python exception get a clear signal.
    """
    if mode == "materialized":
        # Materialized (scheduled → derived tables) is a later-wave feature.
        # We leave the branch here as a deliberate extension point.
        raise AppError(
            "provider_mode_unsupported",
            "Materialized provider mode is not yet implemented (later wave).",
            501,
        )

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
    cache_key = _provider_cache_key(org_id, provider_id, merged_params, policies)

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

    # ── Execute provider ──────────────────────────────────────────────────────
    if provider.kind == "flow":
        # FIX [LOW metering]: enforce quota before executing the flow so embed
        # viewers cannot trigger unmetered warehouse compute on a cache miss.
        from app.features import enforce_quota  # noqa: PLC0415

        await enforce_quota(org_id, "compute_units", amount=1.0)

        # FIX [MED N+1]: pre-fetch the org's flows once and pass the lookup
        # dict into _resolve_flow_provider so it does NOT call list_flows per
        # provider.  A single board load with N flow providers now issues at
        # most one list_flows rather than up to N.
        from app.flows.store import get_flow_store as _get_flow_store  # noqa: PLC0415

        _store = _get_flow_store()
        _all_org_flows = await _store.list_flows(org_id=org_id)
        # Build a dict keyed by both id and name so _resolve_flow_provider can
        # do O(1) lookups instead of a linear scan.
        org_flows_by_key: dict[str, Any] = {}
        for _f in _all_org_flows:
            _fid = str(_f.get("id", ""))
            _fname = _f.get("name", "")
            if _fid:
                org_flows_by_key[_fid] = _f
            if _fname:
                org_flows_by_key[_fname] = _f

        tables = await _resolve_flow_provider(
            provider, merged_params, org_id, claims, org_flows_by_key=org_flows_by_key
        )
    elif provider.kind == "inline":
        tables = await _resolve_inline_provider(provider, merged_params, org_id, claims, repo)
    else:
        raise AppError(
            "unknown_provider_kind",
            f"Unknown provider kind {provider.kind!r}.",
            400,
        )

    # ── Cache store ──────────────────────────────────────────────────────────
    try:
        serialised = _tables_to_bytes(tables)
        put_base_scan(
            cache_key,
            serialised,
            tags=[f"org:{org_id}", f"board:{board_id}"],
        )
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
