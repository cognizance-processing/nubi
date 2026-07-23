"""Datastore → connector resolution — the ONE place this is done.

Extracted verbatim from ``app.routes.query._build_connector_for_plan`` so that
every caller that needs a live connector for a planned query resolves it the
same way. Before this module existed there were THREE divergent copies of this
logic, and the divergence was not academic:

- ``app.dashboards.collect`` had a copy that did neither template resolution nor
  secret injection nor network/bridge resolution. It therefore built connections
  with **no password** (``Access denied for user 'root'@... (using password: NO)``)
  and silently broke EVERY server-side board export — pdf, csv, json and
  thumbnails — for every real (non-demo) board, while the identical query ran
  fine through ``POST /query``.
- ``POST /query`` still carries its own older inline copy that predates the
  pluggable secret resolver and template_config (tracked separately).

The resolution order below is load-bearing and must not be reordered:

  1. ctype        — ``connector_type`` first, legacy ``type`` as fallback.
  2. template_config — per-tenant fields from verified claims. Runs BEFORE
     secrets so resolved host/database are available to the secret resolver.
     Failure is HARD (TemplateSecurityError propagates) — never partial.
  3. secrets      — pluggable resolver, legacy secret_store on ImportError.
     Failure is HARD — never silently fall back to wrong-tenant credentials.
  4. network mode — direct vs VPC bridge; bridge rewrites host/port onto an
     ephemeral tunnel and yields a cleanup the CALLER must invoke.
  5. construction — per-ctype factory.
  6. RLS gate     — refuse a policy-bearing query on a source that cannot
     enforce RLS server-side (tears the tunnel down before raising).

SECURITY: this module is the single choke point for credential injection and
the capability-gated RLS refusal. Do not add a second path — add a parameter.
"""

from __future__ import annotations

from typing import Any, Callable

from app.connectors.registry import get_connector_registry
from app.errors import AppError


async def resolve_datastore_connector(
    physical_plan: Any,
    effective_datastore_id: str,
    org_id: str | None,
    repo: Any,
    *,
    ds: dict[str, Any] | None = None,
) -> tuple[Any, str, Callable[[], None]]:
    """Resolve a datastore id to a live connector.

    The demo connector is deliberately NOT handled here — it lives in
    ``app.routes.query`` alongside its dataset seeding, and importing routes from
    ``app.connectors`` would invert the layering. Callers keep their own demo
    branch; this function is strictly "datastore row → live connector".

    Parameters
    ----------
    physical_plan:
        Needs ``.rls_claims`` — used for template resolution and the RLS gate.
    effective_datastore_id:
        The datastore to resolve. Looked up org-scoped.
    org_id:
        The caller's verified org. The lookup is scoped to it — never cross-org.
    repo:
        Active repository implementation.
    ds:
        Optional pre-fetched datastore row. When given, the repo lookup is
        skipped — for callers that already batched the fetch (see
        ``app.dashboards.collect._prefetch_datastores``, which exists to kill an
        N+1 when many widgets share one datastore).

    Returns
    -------
    tuple[connector, conn_kind, net_cleanup]
        *net_cleanup* tears down any ephemeral bridge tunnel and MUST be called
        by the caller in a ``finally``. It is always callable (no-op default).
    """
    _net_cleanup: Callable[[], None] = lambda: None  # noqa: E731
    _conn_kind = "unknown"

    if ds is None:
        ds = await repo.get("datastores", org_id, effective_datastore_id)
    if ds is None:
        raise AppError(
            "datastore_not_found",
            f"Datastore {effective_datastore_id!r} not found.",
            404,
        )
    cfg: dict = dict(ds.get("config") or {})
    ctype: str | None = cfg.get("connector_type") or cfg.get("type")
    _conn_kind = ctype or "unknown"

    # ── Claim-templated field resolution (per-tenant datastores) ─────────────
    # When the datastore carries a ``template_config`` dict (e.g.
    # ``{"database": "ks_{{ claims.org }}"}``), resolve the template fields
    # from the verified JWT claims in the physical plan.  This runs BEFORE
    # secret injection so that resolved host/database values are available to
    # the secret resolver if it needs them.
    #
    # Failure is hard — TemplateSecurityError propagates to the caller.
    # No fallback, no partial resolution.
    _template_cfg: dict | None = cfg.get("template_config")
    if _template_cfg and isinstance(_template_cfg, dict):
        try:
            from app.connectors.claim_template import (
                get_template_resolver as _get_tmpl_resolver,
            )
        except ImportError:
            _template_cfg = None  # module absent — skip gracefully

        if _template_cfg:
            _rls_claims: dict = dict(physical_plan.rls_claims or {})
            _resolved_fields = _get_tmpl_resolver().resolve_config(
                _template_cfg, _rls_claims
            )
            # Merge resolved fields into cfg — they override static config keys.
            cfg.update(_resolved_fields)

    # ── Secret injection (M22-A) ──────────────────────────────────────────────
    # Use the pluggable secret resolver.  The default ``EncryptedStoreResolver``
    # wraps the existing SecretStore call so existing connectors are unaffected.
    # Templated datastores can specify ``secret_resolver = "external"`` in their
    # config to route to a vault-backed resolver.  Failure is hard — any
    # exception from the resolver propagates (no silent fallback to wrong-tenant
    # credentials).
    _rls_claims_for_secret: dict = dict(physical_plan.rls_claims or {})
    try:
        from app.connectors.secret_resolver import get_resolver as _get_resolver

        _resolver = _get_resolver(cfg, org_id=org_id)
        _secret: dict | None = await _resolver.resolve(
            effective_datastore_id, _rls_claims_for_secret
        )
        # EncryptedStoreResolver returns {} for secret-less connectors; treat
        # empty dict the same as None (no credentials to inject).
        if not _secret:
            _secret = None
    except ImportError:
        # secret_resolver module absent (rare deploy edge case) — fall back to
        # the legacy inline SecretStore call so nothing regresses.
        _secret = None
        try:
            from app.connectors.secret_store import get_secret_store as _get_secret_store

            _secret_store = _get_secret_store()
            _secret = await _secret_store.get(effective_datastore_id, org_id)
        except ImportError:
            _secret = None

    if _secret:
        if ctype == "postgres":
            if "dsn" not in cfg and "password" not in cfg:
                cfg["password"] = _secret.get("password", "")
            elif "password" not in cfg:
                cfg["password"] = _secret.get("password", "")
        elif ctype == "http_json":
            _headers: dict = dict(cfg.get("headers") or {})
            if "token" in _secret:
                _headers["Authorization"] = f"Bearer {_secret['token']}"
            elif "api_key" in _secret:
                _headers["X-API-Key"] = _secret["api_key"]
            cfg["headers"] = _headers
        elif ctype == "bigquery":
            if "service_account_json" in _secret:
                cfg["service_account_json"] = _secret["service_account_json"]
        else:
            for k, v in _secret.items():
                if k not in cfg:
                    cfg[k] = v

    # ── Network-mode resolution (M22-A / M22-B VPC bridge) ────────────────────
    from app.connectors.network import (
        resolve_network as _resolve_network,
        resolve_network_async as _resolve_network_async,
    )

    if "network_mode" not in cfg:
        cfg["network_mode"] = ds.get("network_mode") or "direct"
    _mode: str = (cfg.get("network_mode") or "direct").strip().lower()
    _bridge_id: str | None = ds.get("bridge_id") or cfg.get("bridge_id")
    _bridge: dict | None = None
    if _bridge_id:
        try:
            from app.routes.bridges import _get_bridge as _fetch_bridge  # type: ignore[attr-defined]

            _bridge = await _fetch_bridge(org_id, _bridge_id, repo)
        except (ImportError, AttributeError, AppError):
            _bridge = None

    if _mode == "direct":
        _resolve_network(cfg, _bridge)
    elif _mode == "bridge" and _bridge is not None:
        _target = await _resolve_network_async(cfg, _bridge)
        cfg["host"] = _target.host
        cfg["port"] = _target.port
        _net_cleanup = _target.cleanup
    else:
        _resolve_network(cfg, _bridge)

    # ── Build the connector ───────────────────────────────────────────────────
    factory = get_connector_registry().get(ctype)
    if ctype == "duckdb":
        _db_path = cfg.get("database") or cfg.get("path")
        if _db_path and _db_path != ":memory:":
            import duckdb

            _conn = duckdb.connect(database=_db_path, read_only=True)
            from app.connectors.duckdb_conn import harden_connection as _harden

            _harden(_conn, disable_external_access=True)
            connector = factory(_conn)
        else:
            import duckdb as _duckdb_mem

            _mem_conn = _duckdb_mem.connect(database=":memory:")
            # Execute view_sql if present — e.g. the local-parquet demo/sample
            # datastore config registers ``CREATE VIEW <t> AS
            # read_parquet('<local path>')`` statements here (see
            # app.demo_bundle.local_parquet_datastore_config).
            _view_sql: str | None = cfg.get("view_sql")
            if _view_sql:
                for _stmt in _view_sql.split(";"):
                    _stmt = _stmt.strip()
                    if not _stmt:
                        continue
                    try:
                        _mem_conn.execute(_stmt)
                    except Exception:  # noqa: BLE001
                        pass
            from app.connectors.duckdb_conn import harden_connection as _harden
            from app.demo_bundle import LOCAL_PARQUET_DIR

            # The only view_sql source for a ":memory:" duckdb datastore is
            # the local-parquet demo/sample config, which points at
            # LOCAL_PARQUET_DIR — allow-list just that directory rather than
            # leaving external access (incl. local-file reads) wide open.
            _harden(
                _mem_conn,
                disable_external_access=True,
                allowed_directories=[str(LOCAL_PARQUET_DIR)],
            )
            connector = factory(_mem_conn)
    elif ctype == "postgres":
        _dsn: str | None = cfg.get("dsn")
        if _dsn is None:
            _host = cfg.get("host", "localhost")
            _port = cfg.get("port", 5432)
            _dbname = cfg.get("dbname") or cfg.get("database") or "postgres"
            _user = cfg.get("user") or cfg.get("username") or "postgres"
            _password = cfg.get("password", "")
            _dsn = f"postgresql://{_user}:{_password}@{_host}:{_port}/{_dbname}"
        connector = factory(_dsn)
    else:
        connector = factory(cfg)

    # ── CAPABILITY-GATED RLS (security) ──────────────────────────────────────
    policies = (physical_plan.rls_claims or {}).get("policies") or {}
    if policies and connector.capabilities().get("predicate_rls") is False:
        try:
            _net_cleanup()
        except Exception:  # noqa: BLE001
            pass
        raise AppError(
            "source_unsupported_rls",
            "This source does not support Row-Level Security (predicate_rls=False). "
            "Cannot execute a policy-bearing query on an unsecurable source.",
            501,
        )

    return connector, _conn_kind, _net_cleanup
