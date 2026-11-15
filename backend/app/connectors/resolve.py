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

import asyncio

from typing import Any, Callable

from app.connectors.registry import get_connector_registry
from app.errors import AppError


def template_claims(plan_claims: Any, org_id: str | None) -> dict[str, Any]:
    """The claims a per-tenant datastore template may be resolved against.

    Built only from verified material: the caller's policy claims (taken from
    the verified token by the route) and the organisation the server resolved
    for the request.  ``org`` is ALWAYS the server-resolved organisation — a
    claim of the same name can never replace it.
    """
    source = dict(plan_claims or {})
    policies = source.pop("policies", None)
    claims: dict[str, Any] = {k: v for k, v in source.items() if k != "org"}
    if isinstance(policies, dict):
        claims.update({k: v for k, v in policies.items() if k != "org"})
    if org_id:
        claims["org"] = str(org_id)
    return claims


def merge_connector_secret(cfg: dict, ctype: str | None, secret: dict) -> None:
    """Merge a connector's decrypted secret blob into its config, in place.

    The ONE implementation used by every code path that builds a connector.
    Non-secret config keys win over the blob, except request headers, where
    the stored secret headers are layered on top of the non-secret ones.
    """
    if not secret:
        return
    if ctype == "http_json":
        headers: dict = dict(cfg.get("headers") or {})
        stored = secret.get("headers")
        if isinstance(stored, dict):
            headers.update(stored)
        if "token" in secret:
            headers["Authorization"] = f"Bearer {secret['token']}"
        elif "api_key" in secret:
            headers["X-API-Key"] = secret["api_key"]
        cfg["headers"] = headers
        for k, v in secret.items():
            if k not in ("headers", "token", "api_key") and k not in cfg:
                cfg[k] = v
        return
    if ctype == "postgres":
        # A stored connection string is used as-is; otherwise the password is
        # combined with the non-secret host/port/db/user parts by the caller.
        if "dsn" in secret and "dsn" not in cfg:
            cfg["dsn"] = secret["dsn"]
        if "password" not in cfg:
            cfg["password"] = secret.get("password", "")
        return
    if ctype == "bigquery":
        if "service_account_json" in secret:
            cfg["service_account_json"] = secret["service_account_json"]
        return
    for k, v in secret.items():
        if k not in cfg:
            cfg[k] = v


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
            _resolved_fields = _get_tmpl_resolver().resolve_config(
                _template_cfg, template_claims(physical_plan.rls_claims, org_id)
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
    _rls_claims_for_secret: dict = template_claims(physical_plan.rls_claims, org_id)
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
        merge_connector_secret(cfg, ctype, _secret)

    # ── Network-mode resolution (M22-A / M22-B VPC bridge) ────────────────────
    from app.connectors.network import (
        effective_network as _effective_network,
        resolve_network as _resolve_network,
        resolve_network_async as _resolve_network_async,
    )

    # Config wins over the legacy top-level columns (see effective_network).
    _mode, _bridge_id = _effective_network({**ds, "config": cfg})
    cfg["network_mode"] = _mode
    if _bridge_id:
        cfg["bridge_id"] = _bridge_id
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
        # One shared, hardened open path for every duckdb datastore config —
        # see connect_duckdb_datastore.
        from app.connectors.duckdb_conn import connect_duckdb_datastore

        connector = factory(
            await asyncio.to_thread(connect_duckdb_datastore, cfg, org_id)
        )
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
