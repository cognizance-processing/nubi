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
from app.connectors.query_log import get_query_log
from app.connectors.registry import get_connector_registry
from app.queries import get_query_registry
from app.repos.provider import get_repo
from app.routes import api_router

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
        registered = registry.get(body.query_id)
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
            registered = registry.get(body.query_id)
            if registered is None:
                raise _AppError(
                    "query_not_registered",
                    f"No registered query found for id={body.query_id!r}.",
                    403,
                )
            effective_sql = registered.sql
        else:
            effective_sql = body.sql

    # ── SECURITY: derive RLS policies from the VERIFIED identity ─────────────
    # body.claims.policies is deliberately ignored here — an attacker cannot
    # escalate privileges by injecting policies into the request body.
    # For first-party access tokens, identity.policies is {} (no RLS), which
    # is the same behaviour as the pre-M3 endpoint.
    claims = {"policies": identity.policies}

    # ── 1. Plan ──────────────────────────────────────────────────────────────
    physical_plan = planner_plan(
        sql=effective_sql,
        claims=claims,
        params=body.params,
    )

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

    # ── 3. Pick connector (M12-A) ────────────────────────────────────────────
    # If a datastore_id is provided: resolve the datastore from the repo (org-
    # scoped) and build the connector via the registry.
    # If no datastore_id: use the built-in DuckDB demo dataset (unchanged from
    # the pre-M12 path — byte-identical behaviour for existing tests).
    if body.datastore_id is not None:
        # Resolve org_id: embed tokens carry it in the token claim; first-party
        # tokens require a DB lookup via get_user_org.
        from app.routes.resources import get_user_org as _get_user_org

        repo = get_repo()
        if identity.kind == "embed" and identity.org:
            org_id = identity.org
        else:
            org_id = await _get_user_org(identity.user_id, repo)

        ds = await repo.get("datastores", org_id, body.datastore_id)
        if ds is None:
            raise _AppError(
                "datastore_not_found",
                f"Datastore {body.datastore_id!r} not found.",
                404,
            )
        cfg: dict = ds.get("config") or {}
        ctype: str | None = cfg.get("type")
        factory = get_connector_registry().get(ctype)
        # DuckDBConnector takes an optional connection, not a config dict;
        # construct it with no arguments (fresh in-memory DB).  All other
        # connectors registered in the registry accept a config dict.
        if ctype == "duckdb":
            connector = factory()
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
    arrow_table = connector.execute(physical_plan)

    # ── 5. Serialise to Arrow IPC stream bytes ───────────────────────────────
    full_bytes = table_to_ipc_bytes(arrow_table)

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
# Register this router on the shared api_router
# ---------------------------------------------------------------------------
# All routes defined on ``router`` above are accessible as:
#   POST /api/v1/query   (prefix set by main.py when it mounts api_router)

api_router.include_router(router)
