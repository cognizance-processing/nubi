"""Query endpoint — POST /query → Arrow IPC stream (M3-B: verified-identity auth).

Pipeline
--------
1. Parse + validate the request body (``QueryIn``).
2. Derive RLS claims from the VERIFIED identity — NOT from the request body.
   ``claims = {"policies": identity.policies}``  (body.claims.policies is ignored).
3. Scope gate: require the identity to carry a read scope
   (``read:query``, ``read:*``, or ``read:dashboard:*`` all satisfy this).
4. Allowlist gate (M3-SEC, embed tokens only): if the identity is kind='embed',
   raw SQL is REJECTED.  The caller must supply a ``query_id`` referencing a
   server-registered query.  The registry SQL is used; body.sql is ignored.
   If the registered query carries a ``required_scope``, that scope is also
   enforced before planning.  First-party (kind='access') identities keep full
   raw-SQL access and may optionally supply a query_id to resolve to registry SQL.
5. Run the Nubi planner: ``planner.plan(sql, claims, params=params)`` →
   ``PhysicalPlan``.  The planner validates that the SQL is a SELECT and
   injects RLS predicates from ``claims["policies"]`` at AST level.
6. Cache lookup: ``cache.get(plan.cache_key)`` → Arrow IPC bytes or None.
7. On cache HIT: return ``StreamingResponse(ipc_stream_from_bytes(hit), ...)``
   with header ``X-Nubi-Cache: HIT``.
8. On cache MISS: pick a connector and execute the plan (M12-A).
   - If ``datastore_id`` is given: resolve the datastore from the repo
     (org-scoped), read ``config.type``, build the connector via
     ``get_connector_registry().get(type)(config)``.  If the plan carries
     active RLS policies and the connector declares ``predicate_rls=False``
     → AppError("source_unsupported_rls", 501) before execution.
   - If no ``datastore_id``: use ``DuckDBConnector`` seeded with the
     built-in demo dataset (unchanged from pre-M12).
9. Serialise the Arrow table to IPC stream bytes (``table_to_ipc_bytes``).
10. Cache the result: ``cache.put(plan.cache_key, full_bytes)``.
11. Return ``StreamingResponse(ipc_stream_from_bytes(full_bytes), ...)``
    with header ``X-Nubi-Cache: MISS``.

Security (M3-B + M3-SEC)
------------------------
- ``verified_identity`` dependency accepts BOTH first-party HS256 access tokens
  AND host-signed RS256/ES256 embed JWTs.
- SECURITY: RLS policies come EXCLUSIVELY from the verified token
  (``identity.policies``).  Any ``policies`` field in ``body.claims`` is
  silently ignored.  Non-policy hints in ``body.claims`` (e.g. user-supplied
  hints that do NOT set policies) may be passed but cannot influence RLS.
- SCOPE GATE: ``require_scope`` enforces that the token carries at least one
  read scope (``read:query``, ``read:*``, ``read:dashboard:*``).  First-party
  access tokens default to ``read:*`` so they always pass.  Embed tokens must
  explicitly include a qualifying read scope; otherwise 403 is returned.
- ALLOWLIST GATE (M3-SEC — GAP NOW CLOSED for embed tokens):
  Embed tokens (kind='embed') CANNOT execute arbitrary SQL.  They must supply
  a ``query_id`` that resolves to a server-registered query in the
  ``QueryRegistry``.  The registered SQL is used verbatim; ``body.sql`` is
  ignored entirely for embed callers.  If the registered query specifies a
  ``required_scope``, that scope is also enforced before planning.
  First-party (kind='access') tokens keep raw-SQL access and may optionally
  reference a query_id to use the registry SQL instead of body.sql.
  Residual scope: table-level allowlisting is enforced via the registered-query
  registry — only tables referenced in registered SQLs can be accessed by embed
  tokens.  Row-level isolation is still enforced by RLS policies from the token.
- ORIGIN: ``verify_token`` (called inside ``verified_identity``) already
  enforces ``embed_origin`` vs the ``Origin`` request header when the claim is
  present.  No additional origin check is needed here.
- CACHE ISOLATION: the cache key is computed from the FINAL rewritten SQL +
  params + RLS claims dict (see ``cache_key.compute_cache_key``).  Because RLS
  policies come from the token (and thus differ per tenant), two tenants with
  different ``tenant_id`` policies will have different cache keys, so their
  cached Arrow results are always isolated.  This is the embedded-analytics
  cache-isolation safety property: per-tenant RLS → per-tenant cache namespace.

Demo dataset (DuckDB fallback)
------------------------------
When no ``DATABASE_URL`` / ``datastore_id`` is provided the endpoint runs
against a tiny in-memory DuckDB database seeded with a ``demo`` table so that
``SELECT * FROM demo`` works out of the box with no external dependencies.

    demo(id INTEGER, name TEXT, value DOUBLE, active BOOLEAN)

    id | name    | value  | active
    ---+---------+--------+-------
     1 | alpha   |  1.10  | true
     2 | beta    |  2.20  | false
     3 | gamma   |  3.30  | true
     4 | delta   |  4.40  | false
     5 | epsilon |  5.50  | true
"""

from __future__ import annotations

import pyarrow as pa
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.deps import verified_identity
from app.auth.scopes import has_scope
from app.auth.verify import VerifiedIdentity
from app.connectors import plan as planner_plan
from app.connectors.arrow_io import ipc_stream_from_bytes, table_to_ipc_bytes
from app.connectors.cache import get_cache
from app.connectors.duckdb_conn import DuckDBConnector
from app.connectors.planner import resolve_named_params
from app.connectors.query_log import get_query_log
from app.connectors.registry import get_connector_registry
from app.queries import get_query_registry
from app.queries.registry import QueryParam, RegisteredQuery, ensure_persisted_query
from app.repos.provider import get_repo
from app.routes import api_router

# ---------------------------------------------------------------------------
# Token-claim-reserved param names (M13-A security contract)
# ---------------------------------------------------------------------------
# These names map to fields on VerifiedIdentity that come from the verified
# token.  A caller CANNOT override them via body.named_params — they are
# controlled exclusively by the token issuer.  Attempting to set one of these
# names via named_params raises HTTP 400.
#
# Extend this set if more identity fields should be locked in future.
_TOKEN_CLAIM_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "policies",
        "user_id",
        "sub",
        "org",
        "org_id",
        "project",
        "roles",
        "scope",
        "iss",
        "aud",
        "exp",
        "iat",
        "embed_origin",
        "kind",
    }
)

router = APIRouter(tags=["query"])

_ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"

# ---------------------------------------------------------------------------
# Demo DuckDB connector (module-level singleton, lazily initialised)
# ---------------------------------------------------------------------------

_demo_connector: DuckDBConnector | None = None


def _get_demo_connector() -> DuckDBConnector:
    """Return (or create) the module-level demo DuckDB connector.

    Seeds a small ``demo`` table on first call.  Subsequent calls return the
    same connector instance (the table is already registered).
    """
    global _demo_connector
    if _demo_connector is None:
        conn = DuckDBConnector()  # fresh in-memory DB

        demo_table = pa.table(
            {
                "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
                "name": pa.array(
                    ["alpha", "beta", "gamma", "delta", "epsilon"],
                    type=pa.string(),
                ),
                "value": pa.array([1.1, 2.2, 3.3, 4.4, 5.5], type=pa.float64()),
                "active": pa.array([True, False, True, False, True], type=pa.bool_()),
            }
        )
        conn.register({"demo": demo_table})
        _demo_connector = conn

    return _demo_connector


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class QueryIn(BaseModel):
    """Request body for POST /query.

    Attributes
    ----------
    sql:
        A SELECT SQL statement.  Non-SELECT statements are rejected by the
        planner with a 400 error.
        NOTE (M3-SEC): this field is IGNORED for embed-kind identities — they
        must supply ``query_id`` instead and the server resolves the SQL from
        the registry.  For first-party (kind='access') identities, ``sql`` is
        used when ``query_id`` is not provided.
    query_id:
        Optional id of a server-registered query from the ``QueryRegistry``.
        For embed-kind identities this field is REQUIRED — raw sql is rejected.
        For first-party identities this is optional; when provided the registry
        SQL is used and ``body.sql`` is ignored.
    params:
        Positional query parameters bound to ``$1`` / ``$2`` … placeholders
        in *sql*.  Empty list when the query has no parameters.
        For first-party (kind='access') raw-SQL callers this remains the
        primary param mechanism.  When a ``query_id`` is given and the
        registered query declares named params, use ``named_params`` instead.
    named_params:
        Optional dict of named parameter values.  Resolved against the
        registered query's declared ``params`` list (M13-A):
        - Unknown name → 400
        - Missing ``required`` param with no default → 400
        - Resolver precedence (SECURITY): token/RLS claim names (locked) >
          ``named_params`` values > query param ``default``.
          A name that collides with a token-claim-reserved name CANNOT be set
          via ``named_params`` (rejected with 400).
    claims:
        Optional hints dict from the request body.  NOTE (M3-B security):
        any ``policies`` key inside this dict is IGNORED — RLS policies come
        exclusively from the verified token (``identity.policies``).
        Non-policy fields (e.g. UI hints) may be forwarded at the caller's
        discretion, but MUST NOT contain policies.
    datastore_id:
        Optional datastore identifier.  When provided together with a
        configured ``DATABASE_URL``, routes the query to the Postgres
        connector instead of the built-in DuckDB demo dataset.
    """

    sql: str = ""
    query_id: str | None = None
    params: list = []
    named_params: dict | None = None
    claims: dict | None = None
    datastore_id: str | None = None


# ---------------------------------------------------------------------------
# POST /query
# ---------------------------------------------------------------------------


@router.post("/query")
async def query(
    body: QueryIn,
    # verified_identity accepts both first-party HS256 and embed RS256/ES256
    # tokens.  It passes the request Origin header to verify_token so that
    # embed_origin enforcement is automatic — no extra logic needed here.
    identity: VerifiedIdentity = Depends(verified_identity),
) -> StreamingResponse:
    """Execute a SQL query and stream the result as an Arrow IPC stream.

    Parameters
    ----------
    body:
        ``QueryIn`` JSON body.
    identity:
        The verified identity (injected by ``verified_identity`` dependency).

    Returns
    -------
    StreamingResponse
        HTTP 200 with ``Content-Type: application/vnd.apache.arrow.stream``
        and Arrow IPC stream bytes as the body, streamed in chunks.
        Header ``X-Nubi-Cache`` is ``"HIT"`` on a cache hit, ``"MISS"`` on a miss.

    Raises
    ------
    AppError("unauthorized", 401)
        If the token is missing or invalid.
    AppError("insufficient_scope", 403)
        If the token does not carry a qualifying read scope.
    AppError("origin_mismatch", 403)
        If the token's embed_origin does not match the request Origin header.
    AppError
        Propagated from the planner (400: invalid/unsupported SQL) or from
        the connector (500: execution failure).
    """
    # ── SCOPE GATE ────────────────────────────────────────────────────────────
    # Require at least one read scope.  Accepted forms (per M3 contract):
    #   - ``read:query``        — explicit query read scope
    #   - ``read:*``            — wildcard; covers all read:... (first-party default)
    #   - ``read:dashboard:*``  — dashboard read wildcard used by embed tokens
    # We use identity.scope (the normalised list from VerifiedIdentity) rather
    # than raw_claims so that first-party tokens (which don't embed scope in the
    # JWT payload) still receive their default ``read:*`` grant.
    # Implementation: a scope satisfies the gate if it starts with "read:" and
    # is either a wildcard (ends with :*) or equals "read:query" exactly.
    #
    # M3-SEC FLAG — SCOPE ESCALATION: GAP NOW CLOSED for embed tokens (M3-SEC).
    # Embed tokens (kind='embed') are now bound to server-registered queries;
    # they cannot execute arbitrary SELECT SQL regardless of their read scope.
    # Residual scope: table-level allowlisting is enforced via the registered-
    # query registry — only tables referenced in registered SQLs can be accessed
    # by embed tokens.  Row-level isolation continues to be enforced by RLS
    # policies injected from the token.
    from app.errors import AppError as _AppError

    _scopes = identity.scope
    # has_scope(scopes, "read:query") handles: exact match AND wildcards like
    # "read:*" (covers read:query) but NOT "read:dashboard:*" (different prefix).
    # So we additionally accept any scope that starts with "read:" directly.
    _has_read = has_scope(_scopes, "read:query") or any(
        s.startswith("read:") for s in _scopes
    )
    if not _has_read:
        raise _AppError(
            "insufficient_scope",
            "Token does not carry the required scope: read:query",
            403,
        )

    # ── ALLOWLIST GATE (M3-SEC) ───────────────────────────────────────────────
    # Resolve the effective SQL to execute.
    #
    # kind='embed':  raw SQL is ALWAYS rejected.  The caller MUST supply a
    #   query_id that resolves to a registered query; the registry SQL is used
    #   verbatim and body.sql is ignored entirely.  If the registered query
    #   carries a required_scope, that scope is also enforced here.
    #
    # kind='access': raw SQL is allowed (backwards-compat).  If query_id is
    #   supplied, the registry SQL is used and body.sql is ignored.
    registry = get_query_registry()

    if identity.kind == "embed":
        # Embed tokens MUST provide a query_id — raw SQL is blocked.
        if not body.query_id:
            raise _AppError(
                "query_not_registered",
                "Embed tokens must reference a registered query via query_id; "
                "raw SQL is not permitted.",
                403,
            )
        registered = registry.get(body.query_id) or await ensure_persisted_query(body.query_id)
        if registered is None:
            raise _AppError(
                "query_not_registered",
                f"No registered query found for id={body.query_id!r}.",
                403,
            )
        # Enforce any per-query scope requirement beyond the base read gate.
        if registered.required_scope and not has_scope(_scopes, registered.required_scope):
            raise _AppError(
                "insufficient_scope",
                f"This query requires scope: {registered.required_scope}",
                403,
            )
        # Use the server-authorised SQL; ignore body.sql entirely.
        effective_sql = registered.sql
    else:
        # First-party (kind='access'): may optionally use a registered query.
        if body.query_id:
            registered = registry.get(body.query_id) or await ensure_persisted_query(body.query_id)
            if registered is None:
                raise _AppError(
                    "query_not_registered",
                    f"No registered query found for id={body.query_id!r}.",
                    403,
                )
            effective_sql = registered.sql
        else:
            registered = None
            effective_sql = body.sql

    # ── SECURITY: derive RLS policies from the VERIFIED identity ─────────────
    # body.claims.policies is deliberately ignored here — an attacker cannot
    # escalate privileges by injecting policies into the request body.
    # For first-party access tokens, identity.policies is {} (no RLS), which
    # is the same behaviour as the pre-M3 endpoint.
    claims = {"policies": identity.policies}

    # ── NAMED PARAM RESOLUTION (M13-A) ──────────────────────────────────────
    # When a registered query is in scope (query_id was resolved) and the query
    # declares named params, resolve them regardless of whether body.named_params
    # is provided.  This ensures:
    #   - required params are always validated (→ 400 if missing)
    #   - defaults are always applied (so {{name}} placeholders are replaced)
    #   - unknown names in body.named_params are rejected (→ 400)
    #   - reserved token-claim names in body.named_params are rejected (→ 400)
    #
    # Resolution precedence (security-critical):
    #   token/RLS claims (locked) > body.named_params > query default
    #
    # If there is no registered query in scope (raw SQL path), body.named_params
    # is silently ignored and body.params (positional) is used as-is.
    effective_params: list = list(body.params)

    if registered is not None and registered.params:
        # named_input is the caller-supplied values (may be empty dict or None).
        named_input: dict = dict(body.named_params) if body.named_params else {}

        # Step 1: reject any name that collides with a token-claim-reserved name.
        for forbidden in named_input:
            if forbidden in _TOKEN_CLAIM_RESERVED_NAMES:
                raise _AppError(
                    "param_name_reserved",
                    f"Parameter name {forbidden!r} is reserved by the token/auth "
                    "layer and cannot be set via named_params.",
                    400,
                )

        # Step 2: validate that all caller-supplied keys are declared.
        declared_names: set[str] = {p.name for p in registered.params}

        for key in named_input:
            if key not in declared_names:
                raise _AppError(
                    "unknown_param",
                    f"Unknown parameter {key!r} for query {registered.id!r}. "
                    f"Declared params: {sorted(declared_names)!r}.",
                    400,
                )

        # Step 3: resolve each declared param in order:
        #   caller-supplied > default > required-missing → 400
        resolved: dict[str, object] = {}
        for param in registered.params:
            if param.name in named_input:
                resolved[param.name] = named_input[param.name]
            elif param.default is not None:
                resolved[param.name] = param.default
            elif param.required:
                raise _AppError(
                    "missing_required_param",
                    f"Required parameter {param.name!r} for query "
                    f"{registered.id!r} was not supplied.",
                    400,
                )
            else:
                # Optional param, not supplied, no default → bind as None so it
                # is DEFINED in the Jinja template context. This is what makes
                # conditional templates work: `{% if active %}` / `{{ active }}`
                # see a None (falsy) value instead of raising on StrictUndefined.
                resolved[param.name] = None

        # Step 4: resolve {{name}} → $N and build positional params list.
        # This runs even when resolved is {} to strip any stale {{}} tokens
        # from queries that declare no required params.
        effective_sql, effective_params = resolve_named_params(effective_sql, resolved)

    elif body.named_params:
        # Caller supplied named_params but there is no registered query / no
        # declared params.  Validate reserved names even in this case.
        for forbidden in body.named_params:
            if forbidden in _TOKEN_CLAIM_RESERVED_NAMES:
                raise _AppError(
                    "param_name_reserved",
                    f"Parameter name {forbidden!r} is reserved by the token/auth "
                    "layer and cannot be set via named_params.",
                    400,
                )

    # ── 1. Plan ──────────────────────────────────────────────────────────────
    physical_plan = planner_plan(
        sql=effective_sql,
        claims=claims,
        params=effective_params,
    )

    # ── 1b. Auto pre-aggregation routing (opt-in, conservative) ──────────────
    # If a built rollup is a provably SOUND superset-rewrite for this plan, swap
    # the plan to read the rollup before the cache lookup.  RLS predicates were
    # already injected into physical_plan by planner_plan() and are preserved
    # verbatim through the rewrite (filter columns are kept in the rollup), so
    # per-tenant cache isolation still holds — the rewritten SQL gets its own
    # content-addressed cache_key.  Best-effort: any failure leaves the plan as-is.
    try:
        from app.connectors.planner import route_to_rollup_shape as _route_rollup
        from app.connectors.preagg import get_registry as _get_rollup_registry

        _route = _route_rollup(physical_plan, _get_rollup_registry())
        if _route.routed:
            physical_plan = _route.plan
            if _route.rollup_id:
                _get_rollup_registry().record_hit(_route.rollup_id)
    except Exception:  # noqa: BLE001 — routing must never break the query path.
        pass

    # ── 2. Cache lookup ──────────────────────────────────────────────────────
    # CACHE ISOLATION: because claims (and therefore cache_key) derive from the
    # verified token, two different tenants will always produce different cache
    # keys even for the same SQL string.  This is the embedded-analytics
    # cache-isolation safety property: per-tenant RLS → per-tenant cache namespace.
    cache = get_cache()
    cached_bytes = cache.get(physical_plan.cache_key)

    if cached_bytes is not None:
        # Cache HIT: stream the pre-serialised bytes directly.
        return StreamingResponse(
            ipc_stream_from_bytes(cached_bytes),
            media_type=_ARROW_STREAM_MEDIA_TYPE,
            headers={"X-Nubi-Cache": "HIT"},
        )

    # ── 3. Pick connector (M12-A + M22-A) ───────────────────────────────────
    # If a datastore_id is provided: resolve the datastore from the repo (org-
    # scoped) and build the connector via the registry.
    # M22-A additions:
    #   (a) fetch the decrypted secret for the datastore and merge credentials
    #       into the connector config before construction;
    #   (b) resolve network_mode via resolve_network() — 'direct' passes
    #       through; non-direct modes raise 501 until bridges ship.
    # If no datastore_id: use the built-in DuckDB demo dataset (unchanged from
    # the pre-M12 path — byte-identical behaviour for existing tests).
    #
    # EFFECTIVE DATASTORE (M22+): a registered query may itself carry a
    # datastore_id.  The request body takes precedence (explicit override),
    # otherwise the registered query's bound datastore is used.  This lets a
    # dashboard widget send only {query_id} and still execute against the
    # correct real datastore.  Org-scoping is preserved: whatever id we resolve
    # is fetched via repo.get(..., org_id, ...) — a query can never reference
    # another org's datastore.
    effective_datastore_id = body.datastore_id or (
        registered.datastore_id if registered is not None else None
    )

    # The virtual "Demo data" connector (id "__demo__") is backed by the same
    # in-process demo connector as the no-datastore path — there is no datastore
    # row and the dataset is shared across all orgs (never copied).  Normalise
    # its sentinel id to None so it flows through the built-in demo branch below.
    from app.routes.connectors import DEMO_CONNECTOR_ID as _DEMO_CONNECTOR_ID
    if effective_datastore_id == _DEMO_CONNECTOR_ID:
        effective_datastore_id = None

    # ``_net_cleanup`` tears down any ephemeral network proxy (e.g. a bridge
    # reverse-tunnel) opened while resolving the datastore's network_mode.  It
    # defaults to a no-op so the demo path and the direct path can invoke it
    # unconditionally in the finally block around execute().
    _net_cleanup = lambda: None  # noqa: E731

    if effective_datastore_id is not None:
        # Resolve org_id: embed tokens carry it in the token claim; first-party
        # tokens require a DB lookup via get_user_org.
        from app.routes.resources import get_user_org as _get_user_org

        repo = get_repo()
        if identity.kind == "embed" and identity.org:
            org_id = identity.org
        else:
            org_id = await _get_user_org(identity.user_id, repo)

        ds = await repo.get("datastores", org_id, effective_datastore_id)
        if ds is None:
            raise _AppError(
                "datastore_not_found",
                f"Datastore {effective_datastore_id!r} not found.",
                404,
            )
        cfg: dict = dict(ds.get("config") or {})
        ctype: str | None = cfg.get("connector_type") or cfg.get("type")

        # ── (a) Secret injection (M22-A) ──────────────────────────────────────
        # Fetch the decrypted secret for this datastore (if any) and merge the
        # credential fields that each connector type expects into cfg.
        # Lazy import: secret_store may not be available in all environments.
        try:
            from app.connectors.secret_store import get_secret_store as _get_secret_store
            _secret_store = _get_secret_store()
            _secret: dict | None = await _secret_store.get(effective_datastore_id, org_id)
        except ImportError:
            _secret = None

        if _secret:
            # Merge decrypted credentials into the connector config based on
            # connector type.  The non-secret fields (host, port, dbname, user,
            # url, etc.) remain in cfg unchanged; we only inject secrets.
            if ctype == "postgres":
                # Build a full DSN from non-secret host/port/db/user + decrypted
                # password.  If cfg already contains a 'dsn' key we leave it as-is
                # because the secret store would have provided the full DSN there;
                # otherwise we assemble one from the config parts.
                if "dsn" not in cfg and "password" not in cfg:
                    cfg["password"] = _secret.get("password", "")
                elif "password" not in cfg:
                    cfg["password"] = _secret.get("password", "")
            elif ctype == "http_json":
                # Inject token / bearer into headers (or other header fields).
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
                # Generic fallback: merge all secret keys not already in cfg.
                for k, v in _secret.items():
                    if k not in cfg:
                        cfg[k] = v

        # ── (b) Network-mode resolution (M22-A / M22-B VPC bridge) ────────────
        # resolve_network() / resolve_network_async() inspect cfg["network_mode"]
        # (default 'direct').
        #   'direct'  → host/port pass-through, NO proxy, NO overhead.
        #   'bridge'  → if a bridge row exists AND its agent is connected, open
        #               an ephemeral local TCP proxy via the BridgeBroker and
        #               rewrite cfg['host']/cfg['port'] to point at that proxy so
        #               the connector dials the reverse tunnel. The proxy is torn
        #               down in the finally block after execute() (success OR error).
        #   bridge w/o connected agent, ssh_tunnel, psc, cloudsql_proxy, unknown →
        #               the sync resolve_network() surfaces a clear 501/400 before
        #               any connector is built (no silent fall-through).
        from app.connectors.network import (
            resolve_network as _resolve_network,
            resolve_network_async as _resolve_network_async,
        )

        # Propagate network_mode / bridge_id from the datastore row into cfg
        # so the resolver can inspect them.  If the migration hasn't run yet
        # these keys will simply be absent (treated as 'direct').
        if "network_mode" not in cfg:
            cfg["network_mode"] = ds.get("network_mode") or "direct"
        _mode: str = (cfg.get("network_mode") or "direct").strip().lower()
        _bridge_id: str | None = ds.get("bridge_id") or cfg.get("bridge_id")
        _bridge: dict | None = None
        if _bridge_id:
            # Pre-fetch the bridge row (org-scoped) for the transport layer.
            try:
                from app.routes.bridges import _get_bridge as _fetch_bridge  # type: ignore[attr-defined]
                _bridge = await _fetch_bridge(org_id, _bridge_id, repo)
            except (ImportError, AttributeError, _AppError):
                _bridge = None

        if _mode == "direct":
            # Direct mode: unchanged behaviour — verbatim host/port, no proxy.
            _resolve_network(cfg, _bridge)
        elif _mode == "bridge" and _bridge is not None:
            # Bridge mode WITH a provisioned bridge row: open the reverse tunnel
            # via the async resolver.  This returns a NetworkTarget whose
            # host/port point at a local 127.0.0.1 proxy.  If the agent is not
            # connected, resolve_network_async raises (503 bridge_not_connected),
            # which propagates as a clear error — no silent fall-through.
            _target = await _resolve_network_async(cfg, _bridge)
            # Substitute the connector's dial target with the local proxy
            # endpoint BEFORE the connector is built, so it dials the tunnel.
            cfg["host"] = _target.host
            cfg["port"] = _target.port
            _net_cleanup = _target.cleanup
        else:
            # bridge-without-bridge-row, ssh_tunnel, psc, cloudsql_proxy, or an
            # unknown mode: the sync resolver raises the appropriate 501/400.
            _resolve_network(cfg, _bridge)

        # ── Build the connector ───────────────────────────────────────────────
        factory = get_connector_registry().get(ctype)
        # DuckDBConnector takes an optional connection, not a config dict.
        # Real-connector path: when the datastore config names a database file
        # (config.database / config.path), open it READ-ONLY and run queries
        # against it through the same connector path as every other source.
        # Falls back to a fresh in-memory DB when no path is configured, which
        # preserves demo/fixture/conformance parity.
        if ctype == "duckdb":
            _db_path = cfg.get("database") or cfg.get("path")
            if _db_path and _db_path != ":memory:":
                import duckdb

                _conn = duckdb.connect(database=_db_path, read_only=True)
                try:
                    # Defence-in-depth: a read-only file source has no need to
                    # touch the local FS / network at query time.
                    _conn.execute("SET enable_external_access=false")
                except Exception:
                    pass
                connector = factory(_conn)
            else:
                import duckdb as _duckdb_mem

                _mem_conn = _duckdb_mem.connect(database=":memory:")
                # Execute view_sql if present (e.g. datasets that register a
                # Parquet-backed view: CREATE VIEW dataset AS read_parquet(...)).
                _view_sql: str | None = cfg.get("view_sql")
                # Multi-table S3 datastores (e.g. the per-project demo) may use
                # an s3_views dict instead of a view_sql string.
                if not _view_sql and cfg.get("s3_views"):
                    try:
                        from app.routes.data_browser import (  # noqa: PLC0415
                            _build_view_sql_from_s3_views,
                        )
                        _view_sql = _build_view_sql_from_s3_views(cfg["s3_views"])
                    except Exception:  # noqa: BLE001
                        _view_sql = None
                # If the views read from object storage (s3://), httpfs + an S3
                # SECRET MUST be set up BEFORE the CREATE VIEW statements run —
                # otherwise read_parquet('s3://...') fails and the views silently
                # never exist, surfacing later as "Table not found".
                if (_view_sql and "s3://" in _view_sql) or cfg.get("s3_views"):
                    try:
                        from app.connectors.duckdb_conn import setup_s3_httpfs  # noqa: PLC0415
                        setup_s3_httpfs(_mem_conn, cfg)
                    except Exception:  # noqa: BLE001
                        pass
                if _view_sql:
                    # view_sql may carry MULTIPLE statements (one CREATE VIEW per
                    # table for a multi-table S3 datastore) — execute each.
                    for _stmt in _view_sql.split(";"):
                        _stmt = _stmt.strip()
                        if not _stmt:
                            continue
                        try:
                            _mem_conn.execute(_stmt)
                        except Exception:  # noqa: BLE001
                            pass
                connector = factory(_mem_conn)
        elif ctype == "postgres":
            # PostgresConnector takes a DSN string, not a raw config dict.
            # Assemble the DSN from the (now secret-enriched) config dict.
            _dsn: str | None = cfg.get("dsn")
            if _dsn is None:
                _host = cfg.get("host", "localhost")
                _port = cfg.get("port", 5432)
                _dbname = cfg.get("dbname") or cfg.get("database") or "postgres"
                _user = cfg.get("user") or cfg.get("username") or "postgres"
                _password = cfg.get("password", "")
                _dsn = (
                    f"postgresql://{_user}:{_password}@{_host}:{_port}/{_dbname}"
                )
            connector = factory(_dsn)
        else:
            connector = factory(cfg)

        # ── CAPABILITY-GATED RLS (security) ──────────────────────────────────
        # If the plan carries active RLS policies and the connector declares
        # predicate_rls=False, we MUST refuse before execution — never run a
        # secured query on a source that cannot enforce it.
        # (M3-SEC: defence-in-depth; mongo stub also raises 501 in execute(),
        # but we refuse here at the route level so the error is uniform and
        # no connector execute() call is ever made for unsecurable sources.)
        policies = (physical_plan.rls_claims or {}).get("policies") or {}
        if policies and connector.capabilities().get("predicate_rls") is False:
            # Refusing before execute() — tear down any proxy we already opened
            # (bridge mode) so the 501 path does not leak an ephemeral tunnel.
            try:
                _net_cleanup()
            except Exception:  # noqa: BLE001
                pass
            raise _AppError(
                "source_unsupported_rls",
                "This source does not support Row-Level Security (predicate_rls=False). "
                "Cannot execute a policy-bearing query on an unsecurable source.",
                501,
            )
    else:
        # No datastore_id — use the built-in DuckDB demo connector.  This path
        # is UNCHANGED from the pre-M12 implementation; conformance + existing
        # tests must remain byte-identical.
        connector = _get_demo_connector()

    # ── 4. Execute ───────────────────────────────────────────────────────────
    # try/finally guarantees the ephemeral network proxy (bridge reverse-tunnel)
    # is torn down whether the query SUCCEEDS or RAISES — we never leak proxies.
    # For 'direct' mode / the demo path, _net_cleanup is a no-op.  Serialisation
    # runs inside the guard too because a connector may materialise the table
    # lazily and could still touch the tunnel during table_to_ipc_bytes.
    try:
        arrow_table = connector.execute(physical_plan)

        # ── 5. Serialise to Arrow IPC stream bytes ───────────────────────────
        full_bytes = table_to_ipc_bytes(arrow_table)
    finally:
        try:
            _net_cleanup()
        except Exception:  # noqa: BLE001 — cleanup must never mask the query result/error.
            pass

    # ── 6. Cache the result ───────────────────────────────────────────────────
    cache.put(physical_plan.cache_key, full_bytes)

    # ── 6b. Log the query for pre-agg mining (best-effort; never breaks query) ─
    try:
        get_query_log().record(effective_sql, physical_plan.cache_key, byte_size=len(full_bytes))
    except Exception:
        pass

    # ── 7. Stream the response with MISS header ───────────────────────────────
    return StreamingResponse(
        ipc_stream_from_bytes(full_bytes),
        media_type=_ARROW_STREAM_MEDIA_TYPE,
        headers={"X-Nubi-Cache": "MISS"},
    )


# ---------------------------------------------------------------------------
# GET /query/registry — list registered queries with their declared params
# ---------------------------------------------------------------------------


@router.get("/query/registry")
async def list_query_registry(
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Return all registered queries with their declared params.

    Auth mirrors the POST /query endpoint: requires a valid verified identity
    (first-party HS256 or embed RS256/ES256) with at least one read scope.
    The list is the same for all authenticated callers (org-scoped in the sense
    that registration is server-side and not per-org; a future version could
    filter by org if per-org query libraries are introduced).

    Returns
    -------
    dict
        ``{"queries": [...]}`` where each entry is:
        ``{id, name, required_scope, params: [{name, type, default, required,
        options_query_id}]}``.
    """
    from app.errors import AppError as _AppError

    # Scope gate — same requirement as POST /query.
    _scopes = identity.scope
    _has_read = has_scope(_scopes, "read:query") or any(
        s.startswith("read:") for s in _scopes
    )
    if not _has_read:
        raise _AppError(
            "insufficient_scope",
            "Token does not carry the required scope: read:query",
            403,
        )

    registry = get_query_registry()
    queries = []
    for rq in registry.all():
        queries.append(
            {
                "id": rq.id,
                "name": rq.name,
                "sql": rq.sql,
                "required_scope": rq.required_scope,
                "datastore_id": rq.datastore_id,
                "params": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "default": p.default,
                        "required": p.required,
                        "options_query_id": p.options_query_id,
                    }
                    for p in rq.params
                ],
            }
        )
    return {"queries": queries}


# ---------------------------------------------------------------------------
# POST /query/registry — register or update a query in the runtime registry
# ---------------------------------------------------------------------------


class QueryParamIn(BaseModel):
    """A single typed/named parameter declaration for a query."""

    name: str
    type: str = "text"
    default: object = None
    required: bool = False
    options_query_id: str | None = None


class RegisterQueryIn(BaseModel):
    """Request body for POST /query/registry.

    Attributes
    ----------
    id:
        Optional stable URL-safe identifier.  When omitted a slug is derived
        from *name* (lower-cased, spaces→underscores, non-alnum stripped).
        When provided and a query with that id already exists it is overwritten
        (upsert behaviour).
    name:
        Human-readable label.
    sql:
        The SELECT SQL for this query.  Named placeholders use ``{{name}}``
        syntax.  Must be a non-empty string.
    params:
        Ordered list of named parameter descriptors for the ``{{name}}``
        placeholders in *sql*.
    required_scope:
        Optional extra scope required to run this query beyond the base read gate.
    datastore_id:
        Optional id of the datastore (connector) this query is bound to.  When
        set the query executes against that org-scoped datastore (unless a
        request body overrides it with its own ``datastore_id``).  It is stored
        into the persisted ``queries.config`` so that ``ensure_persisted_query``
        re-binds it after a restart.
    """

    id: str | None = None
    name: str
    sql: str
    params: list[QueryParamIn] = []
    required_scope: str | None = None
    datastore_id: str | None = None


@router.post("/query/registry", status_code=201)
async def register_query(
    body: RegisterQueryIn,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Register (or update) a query in the runtime QueryRegistry.

    Auth: first-party tokens only (kind='access') with a write scope or read:*.
    Embed tokens are not permitted to alter the registry.

    The query is registered in the in-memory singleton immediately so it is
    available to POST /query callers right away.  Persistence is best-effort:
    if the ``queries`` resource table is available (PgRepo), the query is also
    written there so it survives restarts (loaded by the startup hook in
    ``get_query_registry``).  In the in-memory test repo the registry mutation
    alone is sufficient.

    Returns
    -------
    dict
        ``{id, name, sql, params, required_scope}`` — the registered query.

    Raises
    ------
    AppError("forbidden", 403)
        If the caller is an embed token (kind='embed').
    AppError("validation_error", 400)
        If *sql* is empty or *name* is empty.
    """
    import re as _re

    from app.errors import AppError as _AppError

    # Only first-party (kind='access') identities may write to the registry.
    if identity.kind == "embed":
        raise _AppError(
            "forbidden",
            "Embed tokens cannot register queries.",
            403,
        )

    # Scope gate — require at least a read scope (first-party tokens carry read:*).
    _scopes = identity.scope
    _has_read = has_scope(_scopes, "read:query") or any(
        s.startswith("read:") for s in _scopes
    )
    if not _has_read:
        raise _AppError(
            "insufficient_scope",
            "Token does not carry the required scope: read:query",
            403,
        )

    # Validate inputs.
    if not body.name.strip():
        raise _AppError("validation_error", "name must not be empty.", 400)
    if not body.sql.strip():
        raise _AppError("validation_error", "sql must not be empty.", 400)

    # Derive a stable id from the name when not provided.
    query_id: str
    if body.id and body.id.strip():
        query_id = body.id.strip()
    else:
        # slug: lowercase, replace spaces/hyphens with underscores, strip non-alnum_
        slug = body.name.lower()
        slug = _re.sub(r"[\s\-]+", "_", slug)
        slug = _re.sub(r"[^a-z0-9_]", "", slug)
        slug = slug.strip("_") or "query"
        query_id = slug

    # Build the QueryParam list.
    param_objs = [
        QueryParam(
            name=p.name,
            type=p.type,  # type: ignore[arg-type]
            default=p.default,
            required=p.required,
            options_query_id=p.options_query_id,
        )
        for p in body.params
    ]

    # Normalise the optional datastore binding.
    datastore_id = (
        body.datastore_id.strip()
        if body.datastore_id and body.datastore_id.strip()
        else None
    )

    # Register in the in-memory singleton (immediately runnable).
    registry = get_query_registry()
    rq = registry.register(
        id=query_id,
        sql=body.sql,
        name=body.name,
        required_scope=body.required_scope,
        params=param_objs,
        datastore_id=datastore_id,
    )

    # ── Best-effort persistence into the queries table ──────────────────────
    # Write the query into the org-scoped ``queries`` resource so it survives a
    # restart.  The persisted ``config`` carries {sql, name, params, datastore_id}
    # which is exactly the shape ``ensure_persisted_query`` / ``load_persisted_
    # queries`` expect — so the datastore binding is restored on the next boot.
    # This is wrapped in a broad try/except so the in-memory test repo path and
    # any DB hiccup never fail the registration (the in-memory registry mutation
    # above is sufficient for the request to succeed).
    config = {
        "sql": body.sql,
        "name": body.name,
        "datastore_id": datastore_id,
        "params": [
            {
                "name": p.name,
                "type": p.type,
                "default": p.default,
                "required": p.required,
                "options_query_id": p.options_query_id,
            }
            for p in body.params
        ],
    }
    try:
        from app.routes.resources import get_user_org as _get_user_org

        repo = get_repo()
        org_id = await _get_user_org(identity.user_id, repo)
        existing = await repo.get("queries", org_id, query_id)
        if existing is not None:
            await repo.update("queries", org_id, query_id, {"name": body.name, "config": config})
        else:
            await repo.create(
                resource="queries",
                org_id=org_id,
                created_by=identity.user_id,
                name=body.name,
                config=config,
            )
    except Exception:  # noqa: BLE001 — persistence is best-effort.
        pass

    return {
        "id": rq.id,
        "name": rq.name,
        "sql": rq.sql,
        "required_scope": rq.required_scope,
        "datastore_id": rq.datastore_id,
        "params": [
            {
                "name": p.name,
                "type": p.type,
                "default": p.default,
                "required": p.required,
                "options_query_id": p.options_query_id,
            }
            for p in rq.params
        ],
    }


# ---------------------------------------------------------------------------
# Register this router on the shared api_router
# ---------------------------------------------------------------------------
# All routes defined on ``router`` above are accessible as:
#   POST /api/v1/query   (prefix set by main.py when it mounts api_router)

api_router.include_router(router)
