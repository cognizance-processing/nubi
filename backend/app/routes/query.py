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

import logging
import os

import pyarrow as pa
from fastapi import APIRouter, Depends, Request, Response
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
from app.vars.store import get_var_store
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
        # `vars` is the org/project variable namespace ({{ vars.* }}); a caller
        # must not be able to shadow it via named_params (workstream A5).
        "vars",
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

logger = logging.getLogger("nubi.query")

_ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"


async def _load_query_vars(
    org_id: str, project_id: str | None
) -> dict[str, object]:
    """Return the ``{{ vars.* }}`` template namespace for an org (+ project).

    Org-global variables (project_id NULL) are overlaid with project-scoped
    variables — a project var SHADOWS an org-global var with the same key.
    Best-effort: a store error yields an empty namespace rather than failing the
    query (an undefined ``{{ vars.key }}`` will then surface as a clear 400).
    """
    store = get_var_store()
    try:
        merged: dict[str, object] = {
            r["key"]: r["value"] for r in await store.list_vars(org_id, None)
        }
        if project_id:
            for r in await store.list_vars(org_id, project_id):
                merged[r["key"]] = r["value"]
        return merged
    except Exception:  # noqa: BLE001 — vars are advisory; never break the query path
        return {}


# ---------------------------------------------------------------------------
# Heavy-query pool forwarding (cloud "warehouse machine class")
# ---------------------------------------------------------------------------
# One architecture, two machine sizes: a datastore flagged
# config.query_pool="heavy" gets its cache-MISS queries proxied verbatim to a
# pool of bigger machines running the SAME image/code (on Fly: the `query`
# process group, reachable over private networking).  The pool re-verifies the
# token, re-plans, enforces quota, executes, and meters — this forwarder adds
# no security or billing surface of its own.
#
# Env contract:
#   NUBI_HEAVY_QUERY_URL  — base URL of the pool (e.g.
#       "http://query.process.nubi.internal:8000").  Unset → never forward
#       (self-host / local dev default: everything executes in-process).
#   NUBI_QUERY_POOL=heavy — set ON the pool machines; short-circuits
#       forwarding so the pool always executes locally (loop guard #1).
# Loop guard #2: forwarded requests carry X-Nubi-Forwarded and are never
# re-forwarded.


async def _forward_heavy_query(request: Request, body: "QueryIn"):
    """Proxy this query to the heavy pool; return the httpx response.

    Returns ``None`` when the query should execute locally instead: no pool
    configured, this process IS the pool, the request was already forwarded,
    or the pool is unreachable (fail-open to local execution — the
    per-connection DuckDB memory limit keeps that safe).  HTTP error
    responses from the pool (4xx/5xx, e.g. 402 quota_exceeded) are returned
    for verbatim propagation, NOT treated as fallback — falling back would
    bypass the pool's quota/plan errors.
    """
    if os.getenv("NUBI_QUERY_POOL", "").strip().lower() == "heavy":
        return None
    base = os.getenv("NUBI_HEAVY_QUERY_URL", "").strip().rstrip("/")
    if not base:
        return None
    if request.headers.get("x-nubi-forwarded"):
        return None

    import httpx  # noqa: PLC0415 — lazy: only the forwarding path needs it

    headers: dict[str, str] = {
        "content-type": "application/json",
        "x-nubi-forwarded": "1",
    }
    # The pool re-runs full auth: forward the bearer token and the Origin
    # header (embed_origin enforcement happens there too).
    for h in ("authorization", "origin"):
        v = request.headers.get(h)
        if v:
            headers[h] = v

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=5.0)
        ) as client:
            return await client.post(
                f"{base}/api/v1/query",
                content=body.model_dump_json(exclude_none=True),
                headers=headers,
            )
    except Exception:  # noqa: BLE001 — pool down/unreachable → execute locally
        logger.warning(
            "heavy-query pool %s unreachable — executing locally", base
        )
        return None

# ---------------------------------------------------------------------------
# Output-shape contract validation (A4)
# ---------------------------------------------------------------------------
# A registered query may declare its output columns + portable types via
# RegisteredQuery.output_schema.  After execution (cache MISS only — cached
# bytes were validated when written), we normalise each Arrow field type to the
# portable vocabulary and compare name + order + type against the declaration.
#
# Modes:
#   WARN (default)  — attach an X-Nubi-Schema: MISMATCH response header + log.
#   STRICT          — raise AppError("output_schema_mismatch", 422).  Enabled
#                     by env NUBI_OUTPUT_SCHEMA_STRICT (truthy) OR a per-query
#                     flag (RegisteredQuery.strict_output_schema).
#
# None output_schema => skip entirely (queries without a contract are
# unaffected).


def _portable_arrow_type(field_type: "pa.DataType") -> str:
    """Normalise an Arrow field type to the portable contract vocabulary (A4).

    Mapping (the only portable types are text|number|bool|date|timestamp|json):
      int*/float*/decimal*           → number
      utf8/large_utf8/string         → text
      bool                           → bool
      date32/date64                  → date
      timestamp                      → timestamp
      anything else (list/struct/…)  → json
    """
    t = field_type
    if pa.types.is_boolean(t):
        return "bool"
    if (
        pa.types.is_integer(t)
        or pa.types.is_floating(t)
        or pa.types.is_decimal(t)
    ):
        return "number"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "text"
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_timestamp(t):
        return "timestamp"
    return "json"


def _validate_output_schema(
    registered: "RegisteredQuery | None",
    arrow_table: "pa.Table",
) -> tuple[bool, str | None]:
    """Validate the executed result against the declared output_schema (A4).

    Returns ``(ok, detail)`` where *ok* is ``True`` when there is no declared
    schema (skip) or the result matches name + order + portable type exactly,
    and *detail* is a human-readable mismatch description otherwise.
    """
    if registered is None or registered.output_schema is None:
        return True, None

    declared = registered.output_schema
    actual_schema = arrow_table.schema
    actual_names = list(actual_schema.names)

    if len(actual_names) != len(declared):
        return False, (
            f"column count mismatch: declared {len(declared)} "
            f"({[c.name for c in declared]}), got {len(actual_names)} ({actual_names})"
        )

    for idx, col in enumerate(declared):
        got_name = actual_names[idx]
        got_type = _portable_arrow_type(actual_schema.field(idx).type)
        if got_name != col.name:
            return False, (
                f"column {idx}: declared name {col.name!r}, got {got_name!r}"
            )
        if got_type != col.type:
            return False, (
                f"column {idx} ({col.name!r}): declared type {col.type!r}, "
                f"got {got_type!r}"
            )
    return True, None


def _output_schema_strict(registered: "RegisteredQuery | None") -> bool:
    """Return True when output-schema mismatches must raise (STRICT mode)."""
    if os.getenv("NUBI_OUTPUT_SCHEMA_STRICT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True
    return bool(registered is not None and registered.strict_output_schema)


# ---------------------------------------------------------------------------
# Strict environment visibility for embed identities (DECISION 4)
# ---------------------------------------------------------------------------


def _is_uuid_str(value: object) -> bool:
    """Return True when *value* parses as a uuid (persisted-row id shape)."""
    import uuid as _uuid  # noqa: PLC0415

    try:
        _uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _apply_embed_env_pin(
    registered: RegisteredQuery,
    query_id: str,
    identity: VerifiedIdentity,
) -> RegisteredQuery:
    """Resolve the env-pinned definition of a persisted query for embed callers.

    Embed/viewer identities never see drafts in a protected environment:

    - slug-only registry ids (non-uuid — the embed allowlist: ``demo_all``,
      host-registered slugs, …) pass through UNCHANGED;
    - persisted queries (uuid ids) resolve through the project's DEFAULT
      environment: when a version is pinned there, its snapshot ``config``
      (sql / params / datastore binding) replaces the draft; when the default
      env is PROTECTED and nothing is pinned, 404 ``not_published`` is raised;
    - when no project/environment data is resolvable (org-less tokens, test
      doubles without an env store) the draft is served — the environments
      layer is optional.
    """
    if not _is_uuid_str(query_id):
        return registered
    org_id = identity.org
    if not org_id:
        return registered

    row = None
    try:
        row = await get_repo().get("queries", org_id, str(query_id))
    except Exception:  # noqa: BLE001 — repo unavailable → draft (best-effort)
        row = None
    if row is None:
        return registered

    from app.environments.store import resolve_default_env_config  # noqa: PLC0415

    # May raise AppError 404 (not_published) when the default env is protected
    # and the query has no pointer — that propagates to the caller by design.
    pinned = await resolve_default_env_config(
        "query", str(row["id"]), row.get("project_id"), org_id
    )
    if not pinned or not pinned.get("sql"):
        return registered

    from app.queries.registry import (  # noqa: PLC0415
        _params_from_config,
        _schema_from_config,
    )

    datastore_id = pinned.get("datastore_id")
    # Carry the output-shape contract (A4) through the env-pin rebuild: prefer
    # the pinned snapshot's declaration, falling back to the draft's when the
    # snapshot does not carry one.
    pinned_schema = _schema_from_config(pinned.get("output_schema"))
    return RegisteredQuery(
        id=registered.id,
        sql=str(pinned["sql"]),
        name=str(pinned.get("name") or registered.name),
        required_scope=registered.required_scope,
        params=tuple(_params_from_config(pinned.get("params"))),
        datastore_id=(
            str(datastore_id) if datastore_id is not None else registered.datastore_id
        ),
        output_schema=(
            pinned_schema if pinned_schema is not None else registered.output_schema
        ),
        strict_output_schema=bool(
            pinned.get("strict_output_schema", registered.strict_output_schema)
        ),
    )


# ---------------------------------------------------------------------------
# Demo DuckDB connector (module-level singleton, lazily initialised)
# ---------------------------------------------------------------------------

_demo_connector: DuckDBConnector | None = None


def _get_demo_connector() -> DuckDBConnector:
    """Return (or create) the module-level demo DuckDB connector.

    Registers the full demo dataset (the 17 tables behind the demo dashboards/
    queries — retail sales, SaaS metrics, web analytics, finance ops) plus the
    tiny legacy ``demo`` table, so demo queries run and the Data browser lists
    every table even on the built-in demo connector. Cached after first call.
    """
    global _demo_connector
    if _demo_connector is None:
        _demo_connector = _build_demo_connector()
    return _demo_connector


def _build_demo_connector() -> DuckDBConnector:
    """Build a DuckDBConnector with all demo tables registered."""
    from seed_data.generators import build_all_flat  # noqa: PLC0415

    conn = DuckDBConnector()  # fresh in-memory DB
    tables = build_all_flat()  # the 17 demo-dataset tables
    # Legacy 5-row ``demo`` table — kept for older fixtures/queries.
    tables["demo"] = pa.table(
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
    conn.register(tables)
    return conn


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
# Shared resolution helpers (used by POST /query AND POST /query/estimate)
# ---------------------------------------------------------------------------
# These factor the request → PhysicalPlan and PhysicalPlan → Connector
# resolution out of the POST /query handler so the /query/estimate route (W4-C)
# resolves the SAME plan + connector + RLS without duplicating the security
# gates. Estimate runs the identical auth/scope/allowlist/RLS path and then
# calls connector.estimate(plan) instead of connector.execute(plan) — so it
# estimates the RLS-rewritten plan.sql, never raw SQL, and never executes,
# caches, or meters.


class _ResolvedPlan:
    """The fully-resolved, RLS-rewritten plan for a query request.

    Bundles the ``PhysicalPlan`` (RLS predicates injected, rollup-routed) with
    the registered query (if any) and the effective datastore id so the caller
    can build the connector. Plain attribute holder — no behaviour.
    """

    __slots__ = (
        "physical_plan",
        "registered",
        "effective_datastore_id",
        "effective_sql",
    )

    def __init__(
        self,
        physical_plan,
        registered,
        effective_datastore_id: str | None,
        effective_sql: str,
    ) -> None:
        self.physical_plan = physical_plan
        self.registered = registered
        self.effective_datastore_id = effective_datastore_id
        # The rendered SQL (post-template, PRE-rollup-routing) — recorded into
        # the query log for pre-agg mining (mining observes the logical query).
        self.effective_sql = effective_sql


async def _resolve_request_plan(
    body: "QueryIn",
    request: Request,
    identity: VerifiedIdentity,
) -> _ResolvedPlan:
    """Resolve a request into an RLS-rewritten ``PhysicalPlan`` (shared path).

    Runs the SAME gates as POST /query, in order:
      1. SCOPE GATE — require a read scope.
      2. ALLOWLIST GATE (M3-SEC) — embed tokens must reference a registered
         query (raw SQL rejected); first-party may use a query_id or raw SQL.
      3. RLS claims derived EXCLUSIVELY from the verified token.
      4. NAMED-PARAM / {{ vars.* }} resolution (reserved names rejected).
      5. Plan via the Nubi planner (injects RLS predicates at the AST level).
      6. Conservative rollup routing (RLS preserved through the rewrite).

    Returns the resolved plan + registered query + effective datastore id. Does
    NOT touch the cache, build a connector, execute, or meter — those are the
    caller's concern (so /query and /query/estimate diverge only after this).
    """
    from app.errors import AppError as _AppError

    # ── SCOPE GATE ────────────────────────────────────────────────────────────
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

    # ── ALLOWLIST GATE (M3-SEC) ───────────────────────────────────────────────
    registry = get_query_registry()

    if identity.kind == "embed":
        if not body.query_id:
            raise _AppError(
                "query_not_registered",
                "Embed tokens must reference a registered query via query_id; "
                "raw SQL is not permitted.",
                403,
            )
        registered = registry.get(body.query_id) or await ensure_persisted_query(
            body.query_id
        )
        if registered is None:
            raise _AppError(
                "query_not_registered",
                f"No registered query found for id={body.query_id!r}.",
                403,
            )
        if registered.required_scope and not has_scope(
            _scopes, registered.required_scope
        ):
            raise _AppError(
                "insufficient_scope",
                f"This query requires scope: {registered.required_scope}",
                403,
            )
        registered = await _apply_embed_env_pin(registered, body.query_id, identity)
        effective_sql = registered.sql
    else:
        if body.query_id:
            registered = registry.get(body.query_id) or await ensure_persisted_query(
                body.query_id
            )
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

    # ── SECURITY: RLS policies from the VERIFIED identity only ───────────────
    claims = {"policies": identity.policies}

    # ── NAMED PARAM + {{ vars.* }} RESOLUTION ────────────────────────────────
    effective_params: list = list(body.params)

    _template_vars: dict[str, object] = {}
    if "{{" in effective_sql and identity.org:
        _vars_project = request.headers.get("X-Project-Id") or None
        _template_vars = await _load_query_vars(identity.org, _vars_project)

    if registered is not None and registered.params:
        named_input: dict = dict(body.named_params) if body.named_params else {}

        for forbidden in named_input:
            if forbidden in _TOKEN_CLAIM_RESERVED_NAMES:
                raise _AppError(
                    "param_name_reserved",
                    f"Parameter name {forbidden!r} is reserved by the token/auth "
                    "layer and cannot be set via named_params.",
                    400,
                )

        declared_names: set[str] = {p.name for p in registered.params}
        for key in named_input:
            if key not in declared_names:
                raise _AppError(
                    "unknown_param",
                    f"Unknown parameter {key!r} for query {registered.id!r}. "
                    f"Declared params: {sorted(declared_names)!r}.",
                    400,
                )

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
                resolved[param.name] = None

        resolved["vars"] = _template_vars
        effective_sql, effective_params = resolve_named_params(effective_sql, resolved)

    elif "{{" in effective_sql:
        for forbidden in body.named_params or {}:
            if forbidden in _TOKEN_CLAIM_RESERVED_NAMES:
                raise _AppError(
                    "param_name_reserved",
                    f"Parameter name {forbidden!r} is reserved by the token/auth "
                    "layer and cannot be set via named_params.",
                    400,
                )
        try:
            effective_sql, effective_params = resolve_named_params(
                effective_sql, {"vars": _template_vars}
            )
        except KeyError as exc:
            raise _AppError(
                "unknown_template_var",
                f"Template references an undefined variable: {exc}. "
                "Use {{ vars.<key> }} for an org/project variable.",
                400,
            ) from exc

    elif body.named_params:
        for forbidden in body.named_params:
            if forbidden in _TOKEN_CLAIM_RESERVED_NAMES:
                raise _AppError(
                    "param_name_reserved",
                    f"Parameter name {forbidden!r} is reserved by the token/auth "
                    "layer and cannot be set via named_params.",
                    400,
                )

    # ── Plan (RLS predicates injected at the AST level) ──────────────────────
    physical_plan = planner_plan(
        sql=effective_sql,
        claims=claims,
        params=effective_params,
    )

    # ── Conservative rollup routing (RLS preserved through the rewrite) ──────
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

    effective_datastore_id = body.datastore_id or (
        registered.datastore_id if registered is not None else None
    )
    from app.routes.connectors import DEMO_CONNECTOR_ID as _DEMO_CONNECTOR_ID

    if effective_datastore_id == _DEMO_CONNECTOR_ID:
        effective_datastore_id = None

    return _ResolvedPlan(
        physical_plan, registered, effective_datastore_id, effective_sql
    )


async def _resolve_caller_org(
    identity: VerifiedIdentity, repo
) -> tuple[str | None, Exception | None]:
    """Resolve the caller's org id for attribution/quota (shared path).

    Embed tokens carry the org in the token claim; first-party tokens require a
    DB lookup. Returns ``(org_id, lookup_error)`` — a non-None error is only
    surfaced by the caller on the datastore path (the demo path tolerates a
    no-org caller).
    """
    if identity.kind == "embed" and identity.org:
        return identity.org, None
    from app.routes.resources import get_user_org as _get_user_org

    try:
        return await _get_user_org(identity.user_id, repo), None
    except Exception as exc:  # noqa: BLE001 — demo path tolerates no-org callers
        return None, exc


async def _build_connector_for_plan(
    physical_plan,
    effective_datastore_id: str | None,
    org_id: str | None,
    org_lookup_error: Exception | None,
    repo,
):
    """Build the connector for a resolved plan (shared by /query + /estimate).

    Mirrors the connector-construction block of POST /query: datastore lookup
    (org-scoped), secret injection, network-mode resolution, connector build,
    and the capability-gated RLS refusal. The heavy-query-pool forwarding and
    the metering live in the /query handler only — estimate never forwards or
    meters.

    Returns ``(connector, conn_kind, net_cleanup)``. ``net_cleanup`` tears down
    any ephemeral bridge tunnel and MUST be called by the caller in a finally.
    """
    from app.errors import AppError as _AppError

    _net_cleanup = lambda: None  # noqa: E731
    _conn_kind = "demo"

    if effective_datastore_id is None:
        return _get_demo_connector(), _conn_kind, _net_cleanup

    if org_id is None and org_lookup_error is not None:
        raise org_lookup_error

    ds = await repo.get("datastores", org_id, effective_datastore_id)
    if ds is None:
        raise _AppError(
            "datastore_not_found",
            f"Datastore {effective_datastore_id!r} not found.",
            404,
        )
    cfg: dict = dict(ds.get("config") or {})
    ctype: str | None = cfg.get("connector_type") or cfg.get("type")
    _conn_kind = ctype or "unknown"

    # ── Secret injection (M22-A) ──────────────────────────────────────────────
    try:
        from app.connectors.secret_store import get_secret_store as _get_secret_store

        _secret_store = _get_secret_store()
        _secret: dict | None = await _secret_store.get(effective_datastore_id, org_id)
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
        except (ImportError, AttributeError, _AppError):
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
            _view_sql: str | None = cfg.get("view_sql")
            if not _view_sql and cfg.get("s3_views"):
                try:
                    from app.routes.data_browser import (  # noqa: PLC0415
                        _build_view_sql_from_s3_views,
                    )

                    _view_sql = _build_view_sql_from_s3_views(cfg["s3_views"])
                except Exception:  # noqa: BLE001
                    _view_sql = None
            if (_view_sql and "s3://" in _view_sql) or cfg.get("s3_views"):
                try:
                    from app.connectors.duckdb_conn import setup_s3_httpfs  # noqa: PLC0415

                    setup_s3_httpfs(_mem_conn, cfg)
                except Exception:  # noqa: BLE001
                    pass
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

            _parquet_ref = str(cfg.get("parquet_path") or "")
            _s3_only = bool(cfg.get("s3_views")) or _parquet_ref.startswith("s3://")
            _harden(_mem_conn, block_local_fs=_s3_only)
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
        raise _AppError(
            "source_unsupported_rls",
            "This source does not support Row-Level Security (predicate_rls=False). "
            "Cannot execute a policy-bearing query on an unsecurable source.",
            501,
        )

    return connector, _conn_kind, _net_cleanup


# ---------------------------------------------------------------------------
# POST /query
# ---------------------------------------------------------------------------


@router.post("/query")
async def query(
    body: QueryIn,
    request: Request,
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

    # ── RESOLVE THE RLS-REWRITTEN PLAN (shared with /query/estimate) ─────────
    # _resolve_request_plan runs the SAME gates this handler historically ran
    # inline: scope gate, allowlist gate (M3-SEC), token-only RLS claims,
    # named-param/{{vars.*}} resolution, planning (RLS predicates injected),
    # conservative rollup routing, and effective-datastore resolution. The
    # /query/estimate route reuses it verbatim so both paths plan identically.
    _resolved = await _resolve_request_plan(body, request, identity)
    physical_plan = _resolved.physical_plan
    registered = _resolved.registered
    effective_datastore_id = _resolved.effective_datastore_id

    # ``_net_cleanup`` tears down any ephemeral network proxy (e.g. a bridge
    # reverse-tunnel) opened while resolving the datastore's network_mode.  It
    # defaults to a no-op so the demo path and the direct path can invoke it
    # unconditionally in the finally block around execute().
    _net_cleanup = lambda: None  # noqa: E731

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
    # EFFECTIVE DATASTORE (M22+): ``effective_datastore_id`` was resolved by
    # ``_resolve_request_plan`` above (body override → registered query binding,
    # __demo__ sentinel normalised to None). Org-scoping is preserved: whatever
    # id we resolve is fetched via repo.get(..., org_id, ...) — a query can
    # never reference another org's datastore.

    # ── 2b. Org attribution + usage quota (billing) ──────────────────────────
    # Resolve the caller's org BEFORE building a connector so (a) the EE quota
    # checker can gate compute up front and (b) the post-execute metering
    # event is org-attributable.  Embed tokens carry the org in the token
    # claim; first-party tokens require a DB lookup.  Demo-path callers
    # without an org membership keep working (org_id=None → quota allows,
    # metering logs a warning); the datastore path re-raises the original
    # lookup error below to preserve its error contract.
    from app.routes.resources import get_user_org as _get_user_org

    repo = get_repo()
    org_id: str | None
    _org_lookup_error: Exception | None = None
    if identity.kind == "embed" and identity.org:
        org_id = identity.org
    else:
        try:
            org_id = await _get_user_org(identity.user_id, repo)
        except Exception as exc:  # noqa: BLE001 — demo path tolerates no-org callers
            org_id = None
            _org_lookup_error = exc

    from app.features import enforce_quota as _enforce_quota

    await _enforce_quota(org_id, "compute_units", amount=1.0)

    # Connector kind for the metering event's ``tier`` dimension.
    _conn_kind = "demo"

    if effective_datastore_id is not None:
        if org_id is None and _org_lookup_error is not None:
            raise _org_lookup_error

        ds = await repo.get("datastores", org_id, effective_datastore_id)
        if ds is None:
            raise _AppError(
                "datastore_not_found",
                f"Datastore {effective_datastore_id!r} not found.",
                404,
            )
        cfg: dict = dict(ds.get("config") or {})
        ctype: str | None = cfg.get("connector_type") or cfg.get("type")
        _conn_kind = ctype or "unknown"

        # ── Heavy-query pool routing ──────────────────────────────────────────
        # Datastores flagged query_pool="heavy" execute on the big-machine
        # pool when one is configured.  Happens BEFORE secret injection /
        # network resolution so this machine never opens tunnels or builds
        # connectors for work it won't run.  The pool's Arrow bytes are
        # cached here under the same content-addressed cache key, so
        # subsequent identical queries are local HITs.
        if str(cfg.get("query_pool") or "").strip().lower() == "heavy":
            # Warehouse execution is tier-gated (EE: Pro+).  Enforced on the
            # app machine before forwarding AND on the pool itself (this
            # block runs in both processes — the pool just never forwards).
            await _enforce_quota(org_id, "warehouse", amount=1.0)
            _pool_resp = await _forward_heavy_query(request, body)
            if _pool_resp is not None:
                if _pool_resp.status_code == 200:
                    cache.put(physical_plan.cache_key, _pool_resp.content)
                    return StreamingResponse(
                        ipc_stream_from_bytes(_pool_resp.content),
                        media_type=_ARROW_STREAM_MEDIA_TYPE,
                        headers={
                            "X-Nubi-Cache": _pool_resp.headers.get(
                                "x-nubi-cache", "MISS"
                            ),
                            "X-Nubi-Pool": "heavy",
                        },
                    )
                # Pool answered with an error (quota 402, plan 400, …):
                # propagate it verbatim — do NOT fall back to local execution.
                return Response(
                    content=_pool_resp.content,
                    status_code=_pool_resp.status_code,
                    media_type=_pool_resp.headers.get("content-type"),
                )

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
                # Defence-in-depth: a read-only file source has no need to
                # touch the local FS / network at query time.
                from app.connectors.duckdb_conn import harden_connection as _harden

                _harden(_conn, disable_external_access=True)
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
                # Harden AFTER httpfs/secret setup and view creation
                # (lock_configuration freezes settings).  Views scan lazily at
                # query time, so the local filesystem is blocked only when the
                # datastore reads object storage exclusively — a local-Parquet
                # view still needs FS reads at scan time.
                from app.connectors.duckdb_conn import harden_connection as _harden

                _parquet_ref = str(cfg.get("parquet_path") or "")
                _s3_only = bool(cfg.get("s3_views")) or _parquet_ref.startswith("s3://")
                _harden(_mem_conn, block_local_fs=_s3_only)
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
    import time as _time

    _t0 = _time.perf_counter()
    try:
        arrow_table = connector.execute(physical_plan)

        # ── 4b. Output-shape contract validation (A4) ────────────────────────
        # Only on cache MISS — cached bytes were validated when first written.
        # No declared output_schema → skipped entirely (queries without a
        # contract are unaffected).  WARN mode (default) flags via a response
        # header + log; STRICT mode raises 422 before serialisation.
        _schema_ok, _schema_detail = _validate_output_schema(registered, arrow_table)
        if not _schema_ok:
            if _output_schema_strict(registered):
                raise _AppError(
                    "output_schema_mismatch",
                    "Query result does not match the declared output_schema: "
                    f"{_schema_detail}",
                    422,
                )
            logger.warning(
                "output_schema mismatch for query_id=%s: %s",
                getattr(registered, "id", None),
                _schema_detail,
            )

        # ── 5. Serialise to Arrow IPC stream bytes ───────────────────────────
        full_bytes = table_to_ipc_bytes(arrow_table)
    finally:
        try:
            _net_cleanup()
        except Exception:  # noqa: BLE001 — cleanup must never mask the query result/error.
            pass

    # ── 5b. Meter the execution (billing: compute_units) ─────────────────────
    # One event per cache MISS — hits cost no compute and are not metered.
    # units = compute-seconds (reconcile sums these into compute_units).
    # On the heavy-query pool (the "warehouse machine class"), CUs are billed
    # at a multiplier (NUBI_CU_MULTIPLIER, canonical value
    # ee.billing.tiers.WAREHOUSE_CU_MULTIPLIER) and the event tier carries a
    # ":warehouse" suffix for observability.
    # Best-effort: metering must never break the query path.
    _elapsed_ms = int((_time.perf_counter() - _t0) * 1000)
    try:
        from app.compute.metering import record_usage as _record_usage

        _cu_multiplier = 1.0
        try:
            _cu_multiplier = max(float(os.getenv("NUBI_CU_MULTIPLIER", "1")), 1.0)
        except ValueError:
            pass
        _meter_tier = _conn_kind
        if os.getenv("NUBI_QUERY_POOL", "").strip().lower() == "heavy":
            _meter_tier = f"{_conn_kind}:warehouse"

        await _record_usage(
            kind="compute",
            user_id=str(identity.user_id or "embed"),
            org_id=org_id,
            units=(_elapsed_ms / 1000.0) * _cu_multiplier,
            tier=_meter_tier,
            elapsed_ms=_elapsed_ms,
            output_bytes=len(full_bytes),
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        pass

    # ── 5c. Meter bytes scanned (billing: query_scan — W4-A) ─────────────────
    # The Wave-4 billed metric is BYTES SCANNED (BigQuery-comparable), captured
    # here as a SECOND usage event on cache MISS ONLY — a cache HIT scans
    # nothing and returned above without reaching this code.
    #
    # PROXY: the ideal figure is post-pruning Parquet bytes read from the
    # lakehouse (DuckDB parquet_metadata / httpfs range-read counters). When a
    # connector cannot surface a true scanned-bytes figure we fall back to the
    # result Arrow table's in-memory buffer footprint (``total_buffer_nbytes``)
    # as a best-effort proxy. This UNDER-counts wide scans that aggregate down
    # to a small result and OVER-counts nothing — it is advisory and only ever
    # used until W4-D/W4-F wire the real lakehouse counters through the plan.
    # ``units`` is the scanned-byte count; reconcile (W4-B) sums query_scan
    # units into the TiB-scanned line. Best-effort: never breaks the query path.
    try:
        from app.compute.metering import record_usage as _record_usage_scan

        try:
            _scanned_bytes = int(arrow_table.get_total_buffer_size())
        except Exception:  # noqa: BLE001 — fall back to serialised IPC size
            _scanned_bytes = len(full_bytes)

        await _record_usage_scan(
            kind="query_scan",
            user_id=str(identity.user_id or "embed"),
            org_id=org_id,
            units=float(_scanned_bytes),
            tier=_conn_kind,
            output_bytes=_scanned_bytes,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        pass

    # ── 6. Cache the result ───────────────────────────────────────────────────
    cache.put(physical_plan.cache_key, full_bytes)

    # ── 6b. Log the query for pre-agg mining (best-effort; never breaks query) ─
    try:
        get_query_log().record(
            _resolved.effective_sql, physical_plan.cache_key, byte_size=len(full_bytes)
        )
    except Exception:
        pass

    # ── 7. Stream the response with MISS header ───────────────────────────────
    # WARN-mode output-schema mismatch (A4): surface an advisory header so the
    # caller can detect the contract drift without the request failing.  STRICT
    # mode already raised 422 above, so reaching here with _schema_ok False
    # means WARN mode.
    _resp_headers = {"X-Nubi-Cache": "MISS"}
    if not _schema_ok:
        _resp_headers["X-Nubi-Schema"] = "MISMATCH"
    return StreamingResponse(
        ipc_stream_from_bytes(full_bytes),
        media_type=_ARROW_STREAM_MEDIA_TYPE,
        headers=_resp_headers,
    )


# ---------------------------------------------------------------------------
# POST /query/estimate  (W4-C — BigQuery dry-run parity, no execution)
# ---------------------------------------------------------------------------


@router.post("/query/estimate")
async def query_estimate(
    body: QueryIn,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Pre-run cost/scan estimate for a query — WITHOUT executing it.

    BigQuery dry-run parity: resolves the SAME plan + connector + RLS as
    POST /query (identical auth/scope/allowlist/RLS gates, via the shared
    ``_resolve_request_plan`` + ``_build_connector_for_plan`` helpers), then
    calls ``connector.estimate(plan)`` and returns the figures as JSON. It
    estimates the RLS-REWRITTEN ``plan.sql`` (never the caller's raw SQL — the
    connector contract requires estimating ``plan.sql``), so an estimate can
    never reveal rows outside the caller's scope.

    Unlike POST /query this route NEVER executes the query, reads or writes the
    result cache, forwards to the heavy-query pool, or meters usage. It still
    enforces the EE compute-units quota up front (an estimate consumes a small
    amount of planning/dry-run budget and the front-end gates the run on it).

    Returns
    -------
    dict
        ``{supported, est_bytes_scanned, est_rows, mechanism, exact,
        connector_type}``. ``supported`` is ``False`` (with the numeric fields
        ``None``) when the connector cannot dry-run/EXPLAIN — the UI then shows
        no estimate chip rather than a misleading zero.

    Raises
    ------
    AppError
        The SAME auth/scope/allowlist/RLS/datastore errors as POST /query
        (insufficient_scope 403, query_not_registered 403, datastore_not_found
        404, source_unsupported_rls 501, …) — estimate shares every gate.
    """
    # ── Resolve the SAME RLS-rewritten plan as POST /query ───────────────────
    _resolved = await _resolve_request_plan(body, request, identity)
    physical_plan = _resolved.physical_plan
    effective_datastore_id = _resolved.effective_datastore_id

    # ── Org attribution + quota (mirror /query; estimate consumes plan budget) ─
    repo = get_repo()
    org_id, _org_lookup_error = await _resolve_caller_org(identity, repo)

    from app.features import enforce_quota as _enforce_quota

    await _enforce_quota(org_id, "compute_units", amount=1.0)

    # ── Build the connector (same secret/network/RLS-gate path as /query) ────
    # Reuses _build_connector_for_plan so the capability-gated RLS refusal
    # (source_unsupported_rls 501) fires here too — we never estimate a
    # policy-bearing query against an unsecurable source.
    connector, _conn_kind, _net_cleanup = await _build_connector_for_plan(
        physical_plan,
        effective_datastore_id,
        org_id,
        _org_lookup_error,
        repo,
    )

    # ── Estimate (no execute / no cache / no meter / no pool forward) ─────────
    try:
        estimate = connector.estimate(physical_plan)
    finally:
        try:
            _net_cleanup()
        except Exception:  # noqa: BLE001 — cleanup must never mask the result/error.
            pass

    if estimate is None:
        # Connector cannot dry-run/EXPLAIN → "unsupported" (distinct from zero).
        return {
            "supported": False,
            "est_bytes_scanned": None,
            "est_rows": None,
            "mechanism": "unsupported",
            "exact": False,
            "connector_type": _conn_kind,
        }

    return {
        "supported": True,
        "est_bytes_scanned": estimate.est_bytes_scanned,
        "est_rows": estimate.est_rows,
        "mechanism": estimate.mechanism,
        "exact": estimate.exact,
        "connector_type": _conn_kind,
    }


# ---------------------------------------------------------------------------
# GET /query/registry — list registered queries with their declared params
# ---------------------------------------------------------------------------


@router.get("/query/registry")
async def list_query_registry(
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Return the registered queries visible to the caller.

    Auth mirrors the POST /query endpoint: requires a valid verified identity
    (first-party HS256 or embed RS256/ES256) with at least one read scope.

    Scoping (strict isolation — DECISION 3): the registry singleton is
    process-global, so the raw list spans every org.  The response is scoped
    to the caller:

    - first-party (kind='access'): entries whose persisted ``queries`` row
      belongs to the caller's org + active project (``X-Org-Id`` /
      ``X-Project-Id`` honoured, default project otherwise).  Slug-only
      registry entries with no persisted row are EXCLUDED — they exist for
      the embed allowlist, not first-party project browsing.
    - embed (kind='embed'): entries whose persisted row belongs to the
      token's org, PLUS slug-only allowlist entries (``demo_all``, host-
      registered slug ids, …).

    When org/project resolution is unavailable (no org membership, repo
    without a queries table, org-less embed token) the unfiltered registry is
    returned — the persistence-free demo path keeps working.

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
    entries = registry.all()

    # ── ORG/PROJECT SCOPING (DECISION 3) ─────────────────────────────────────
    row_ids: set[str] | None = None
    include_slug_only = identity.kind == "embed"
    try:
        repo = get_repo()
        if identity.kind == "embed":
            if identity.org:
                rows = await repo.list("queries", identity.org)
                row_ids = {str(r["id"]) for r in rows}
        else:
            from app.routes._org import (  # noqa: PLC0415
                resolve_org_id as _resolve_org_id,
                resolve_project_filter as _resolve_project_filter,
            )

            _org_id = await _resolve_org_id(identity.user_id, repo, request)
            _project_id = await _resolve_project_filter(_org_id, request)
            rows = await repo.list("queries", _org_id, _project_id)
            row_ids = {str(r["id"]) for r in rows}
    except Exception:  # noqa: BLE001 — scoping unavailable → unfiltered list.
        row_ids = None

    if row_ids is not None:
        entries = [
            rq
            for rq in entries
            if rq.id in row_ids
            or (include_slug_only and not _is_uuid_str(rq.id))
        ]

    queries = []
    for rq in entries:
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
        Optional stable URL-safe identifier.  When omitted the query is
        persisted into the org's ``queries`` table first (upserting by a slug
        derived from *name*) and the row uuid becomes the canonical id — the
        same identifier is used by ``/queries/{id}`` and the versioning
        endpoints (``/versions/query/{id}``).  When persistence is unavailable
        the name-slug (lower-cased, spaces→underscores, non-alnum stripped) is
        used as a memory-only fallback id.  When provided and a query with
        that id already exists it is overwritten (upsert behaviour); uuid ids
        upsert the matching ``queries`` row, non-uuid (slug) ids are
        registry-only.
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
    request: Request,
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

    # Legacy name-slug: persisted on the row (config.slug) so re-registering
    # the same name without an id upserts the same row, and used as the
    # memory-only fallback id when persistence is unavailable.
    slug = body.name.lower()
    slug = _re.sub(r"[\s\-]+", "_", slug)
    slug = _re.sub(r"[^a-z0-9_]", "", slug)
    slug = slug.strip("_") or "query"

    explicit_id = body.id.strip() if body.id and body.id.strip() else None

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

    # ── Canonical id + best-effort persistence ───────────────────────────────
    # The registry id and the persisted ``queries`` row id must be the SAME
    # identifier end-to-end: the versioning endpoints (/versions/query/{id}),
    # the resource routes (/queries/{id}), and the startup loader
    # (``load_persisted_queries`` re-registers rows under their row uuid) all
    # resolve a query by the row id.  Therefore:
    #
    #   - explicit uuid id → upsert the row with that exact id (idempotent);
    #   - explicit non-uuid (slug) id → registry-only registration (row PKs
    #     are uuids; this matches the historical Pg behaviour where the
    #     ``::uuid`` cast made persistence a silent no-op for slug ids — the
    #     embed-allowlist use case that depends on stable slug ids);
    #   - no id → persist FIRST (upserting by the name-slug stored in
    #     ``config.slug`` so re-saving the same name updates the same row) and
    #     adopt the row uuid as the registry id.  When persistence is
    #     unavailable, fall back to the legacy name-slug (memory-only).
    #
    # The persisted ``config`` carries {sql, name, params, datastore_id} —
    # exactly the shape ``ensure_persisted_query`` / ``load_persisted_queries``
    # expect — so the datastore binding is restored on the next boot.  The
    # whole block is wrapped in a broad try/except so the FakeDB test path and
    # any DB hiccup never fail the registration (the in-memory registry
    # mutation below is sufficient for the request to succeed).
    import uuid as _uuid

    config = {
        "sql": body.sql,
        "name": body.name,
        "slug": slug,
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

    def _is_uuid(value: str) -> bool:
        try:
            _uuid.UUID(value)
        except (ValueError, TypeError):
            return False
        return True

    query_id: str | None = explicit_id
    try:
        from app.routes._org import (  # noqa: PLC0415
            get_user_org as _get_user_org,
            resolve_project_id_for_create as _resolve_project_id_for_create,
        )

        repo = get_repo()
        org_id = await _get_user_org(identity.user_id, repo)
        # Active project (X-Project-Id / ?project_id=, else the org default):
        # persisted rows are project-scoped so the registry list can be too.
        project_id = await _resolve_project_id_for_create(org_id, request)

        if explicit_id is not None:
            # Explicit id: persist only when it can be a row primary key.
            if _is_uuid(explicit_id):
                existing = await repo.get("queries", org_id, explicit_id)
                if existing is not None:
                    await repo.update(
                        "queries",
                        org_id,
                        explicit_id,
                        {"name": body.name, "config": config},
                    )
                else:
                    await repo.create(
                        resource="queries",
                        org_id=org_id,
                        created_by=identity.user_id,
                        name=body.name,
                        config=config,
                        project_id=project_id,
                        id=explicit_id,
                    )
        else:
            # No id given: upsert by name-slug (within the active project),
            # then adopt the row uuid.
            existing = None
            for row in await repo.list("queries", org_id, project_id):
                if (row.get("config") or {}).get("slug") == slug:
                    existing = row
                    break
            if existing is not None:
                row_id = str(existing["id"])
                await repo.update(
                    "queries", org_id, row_id, {"name": body.name, "config": config}
                )
                query_id = row_id
            else:
                created = await repo.create(
                    resource="queries",
                    org_id=org_id,
                    created_by=identity.user_id,
                    name=body.name,
                    config=config,
                    project_id=project_id,
                )
                query_id = str(created["id"])
    except Exception:  # noqa: BLE001 — persistence is best-effort.
        query_id = explicit_id

    if not query_id:
        # Persistence unavailable and no explicit id — legacy slug fallback.
        query_id = slug

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
