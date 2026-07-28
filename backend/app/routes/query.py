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
   - CONNECTOR OVERRIDE (embed only): if the identity is kind='embed' AND the
     verified token carries a ``datastore`` claim (``identity.datastore``),
     that datastore id becomes the EFFECTIVE datastore for ALL queries in the
     request — overriding both ``body.datastore_id`` and the registered query's
     default binding. This is the id-based whole-dashboard connector-override
     embedding capability (feature-equivalent to the legacy whole-dashboard
     connector selection, but by id). The override id is resolved ORG-SCOPED
     against ``identity.org``; a cross-org / unknown id → AppError. There is no
     type-fallback path (id-only). First-party tokens are unaffected.
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
- CACHE ISOLATION: the exact-result cache key is derived by
  ``cache_key.scope_cache_key(plan.cache_key, org_id, effective_datastore_id)``
  — a SHA-256 re-hash of the plan content-hash with the org_id and datastore id
  appended.  This guarantees that two orgs with identical SQL AND policies={} (no
  RLS predicates) can never collide in the cache, preventing cross-tenant data
  leaks even when there are no row-level policies in play.

Demo dataset (local-parquet fallback)
--------------------------------------
When no ``datastore_id`` is provided (or the ``__demo__`` sentinel is given),
the endpoint runs against the static local-parquet lakehouse — the same
``seed_data/parquet/`` files used by the seeded demo connector in
``app/sample.py`` (D1 consolidation: single source, no in-memory build).

All 17 demo tables (retail sales, SaaS metrics, web analytics, finance ops)
plus the legacy 5-row ``demo`` table are available so that existing fixtures
keep working:

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

import asyncio
import logging
import os

import pyarrow as pa
import sqlglot
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.deps import verified_identity
from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL
from app.auth.verify import VerifiedIdentity
from app.connectors import plan as planner_plan
from app.connectors.arrow_io import ipc_stream_from_bytes, table_to_ipc_bytes
from app.connectors.cache import get_cache
from app.connectors.cache_key import compute_base_scan_key, scope_cache_key
from app.connectors.dialects import DEFAULT_DIALECT, dialect_for
from app.connectors.duckdb_conn import DuckDBConnector
from app.connectors.planner import resolve_named_params
from app.connectors.query_log import get_query_log
from app.connectors.registry import get_connector_registry
from app.queries import get_query_registry
from app.queries.registry import (
    QueryParam,
    RegisteredQuery,
    ensure_persisted_query,
    resolve_registered_query,
)
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

    D1 consolidation: backed by the static local-parquet lakehouse files
    (``seed_data/parquet/``) — the same parquet source used by the seeded
    demo connector in ``app/sample.py``.  The 17 demo-dataset tables plus the
    legacy 5-row ``demo`` table are all available so existing fixtures keep
    working.  Cached after first call.
    """
    global _demo_connector
    if _demo_connector is None:
        _demo_connector = _build_demo_connector()
    return _demo_connector


def _build_demo_connector() -> DuckDBConnector:
    """Build a DuckDBConnector backed by the static local-parquet lakehouse.

    D1 consolidation: the 17 demo tables are served from the pre-generated
    parquet files under ``seed_data/parquet/`` — the same files that
    ``local_parquet_datastore_config()`` and ``app/sample.py`` use — rather
    than being built in-memory via ``build_all_flat()``.  This makes the
    no-datastore fallback share the same single source as every seeded demo
    connector.

    The legacy 5-row ``demo`` table is still registered as a plain Arrow
    table (backward-compatible — existing tests and fixtures rely on
    ``SELECT * FROM demo`` returning 5 rows).
    """
    import duckdb  # noqa: PLC0415

    from app.connectors.duckdb_conn import harden_connection  # noqa: PLC0415
    from app.demo_bundle import (  # noqa: PLC0415
        LOCAL_PARQUET_DIR,
        export_demo_parquet_local,
        local_parquet_datastore_config,
    )

    # Ensure the parquet files exist (idempotent; fast on repeat calls).
    export_demo_parquet_local()
    cfg = local_parquet_datastore_config()

    # Build a fresh :memory: DuckDB connection with the parquet-backed views.
    mem_conn = duckdb.connect(database=":memory:")
    view_sql: str = cfg.get("view_sql") or ""
    for stmt in view_sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                mem_conn.execute(stmt)
            except Exception:  # noqa: BLE001 — skip malformed stmts
                pass

    # Harden AFTER the views are created (they need external access to read
    # their backing parquet at CREATE VIEW time) but BEFORE any tenant SQL
    # runs: this is the default connector for every user's Queries workspace,
    # so without this it lets arbitrary authenticated users read host files
    # via read_csv_auto('/etc/passwd') etc. Views are read lazily, so allow
    # only the parquet export directory they point at.
    harden_connection(
        mem_conn,
        disable_external_access=True,
        allowed_directories=[str(LOCAL_PARQUET_DIR)],
    )

    # Legacy 5-row ``demo`` table — kept for backward compatibility with
    # existing tests and fixtures that query ``SELECT * FROM demo``.
    mem_conn.register(
        "demo",
        pa.table(
            {
                "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
                "name": pa.array(
                    ["alpha", "beta", "gamma", "delta", "epsilon"],
                    type=pa.string(),
                ),
                "value": pa.array([1.1, 2.2, 3.3, 4.4, 5.5], type=pa.float64()),
                "active": pa.array([True, False, True, False, True], type=pa.bool_()),
            }
        ),
    )

    return DuckDBConnector(mem_conn)


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


async def _resolve_effective_datastore_id(
    body: "QueryIn",
    registered,
    identity: VerifiedIdentity,
) -> str | None:
    """Resolve the EFFECTIVE datastore id for a request (org-scoped).

    Precedence: ``body.datastore_id`` → registered query binding, then the
    embed connector-override claim (id-based, whole-dashboard). The ``__demo__``
    sentinel collapses to ``None`` (the built-in DuckDB demo dataset).

    This runs BEFORE planning so the planner can pick the target connector's
    native sqlglot dialect for SQL generation.
    """
    from app.errors import AppError as _AppError

    effective_datastore_id = body.datastore_id or (
        registered.datastore_id if registered is not None else None
    )

    # ── EMBED CONNECTOR OVERRIDE (id-based, whole-dashboard) ─────────────────
    # This is the "connector override" embedding capability: feature-equivalent
    # to the legacy whole-dashboard connector selection, but selected BY ID.
    # When an embed token carries a host-signed ``datastore`` claim
    # (``identity.datastore``), that id becomes the EFFECTIVE datastore for ALL
    # queries in the request — it overrides both ``body.datastore_id`` and the
    # registered query's default binding. This lets a host point an embedded
    # dashboard at a per-tenant datastore without re-registering its queries.
    #
    # SECURITY (org-scope): the claim can ONLY target a datastore that lives in
    # the token's own org (``identity.org``). We resolve it org-scoped via the
    # repo here; a missing row — which includes any cross-org id, since the repo
    # lookup is scoped to ``identity.org`` — raises ``datastore_not_found`` (404)
    # before planning continues. There is NO connector_type / type-fallback path:
    # this is id-only. First-party (kind='access') tokens never carry this claim
    # and are therefore unaffected. RLS still comes only from the token.
    if identity.kind == "embed" and identity.datastore:
        if not identity.org:
            raise _AppError(
                "datastore_not_found",
                "Embed connector-override claim cannot be resolved without an "
                "org in the token.",
                404,
            )
        _override_id = str(identity.datastore)
        _override_ds = await get_repo().get(
            "datastores", identity.org, _override_id
        )
        if _override_ds is None:
            # Org-scoped lookup miss → either truly absent OR cross-org. Either
            # way the embed token may not route at this datastore.
            raise _AppError(
                "datastore_not_found",
                f"Connector-override datastore {_override_id!r} not found in this org.",
                404,
            )
        effective_datastore_id = _override_id

    from app.routes.connectors import DEMO_CONNECTOR_ID as _DEMO_CONNECTOR_ID

    if effective_datastore_id == _DEMO_CONNECTOR_ID:
        effective_datastore_id = None

    return effective_datastore_id


async def _resolve_target_dialect(
    effective_datastore_id: str | None,
    identity: VerifiedIdentity,
    request: "Request | None" = None,
) -> str:
    """Resolve the target sqlglot dialect for SQL generation.

    For a BYO connector (a resolved ``effective_datastore_id``) the datastore's
    ``connector_type`` is mapped to its native sqlglot dialect via the shared
    ``dialect_for`` helper, so warehouse-native SQL (e.g. ``TRY_CAST`` /
    ``SAFE_CAST``) survives to the engine.

    For the no-datastore / demo / lake fallback path — or if the datastore /
    org cannot be resolved — the historical default (``"postgres"``) is kept so
    existing planner behaviour is unchanged. Fail-safe: any lookup error falls
    back to the default dialect rather than breaking the query path.
    """
    if effective_datastore_id is None:
        return DEFAULT_DIALECT
    try:
        org_id, _ = await _resolve_caller_org(identity, get_repo(), request)
        if not org_id:
            return DEFAULT_DIALECT
        ds = await get_repo().get("datastores", org_id, effective_datastore_id)
        if ds is None:
            return DEFAULT_DIALECT
        cfg: dict = dict(ds.get("config") or {})
        ctype = cfg.get("connector_type") or cfg.get("type")
        return dialect_for(ctype)
    except Exception:  # noqa: BLE001 — fail-safe to the historical default.
        return DEFAULT_DIALECT


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
    # SECURITY (CRITICAL 1): resolve the caller's own org BEFORE any query_id
    # lookup so registry resolution can be org-scoped end-to-end — closes the
    # cross-tenant query-registry hijack where ``registry.get(body.query_id)``
    # (a process-global dict keyed only by id) or the old unscoped
    # ``ensure_persisted_query`` DB read could return/load ANOTHER org's
    # persisted query for a caller-supplied query_id. See
    # ``app.queries.registry.resolve_registered_query``.
    _allowlist_org_id, _ = await _resolve_caller_org(identity, get_repo(), request)

    if identity.kind == "embed":
        if not body.query_id:
            raise _AppError(
                "query_not_registered",
                "Embed tokens must reference a registered query via query_id; "
                "raw SQL is not permitted.",
                403,
            )
        registered = await resolve_registered_query(body.query_id, _allowlist_org_id)
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
            registered = await resolve_registered_query(body.query_id, _allowlist_org_id)
            if registered is None:
                raise _AppError(
                    "query_not_registered",
                    f"No registered query found for id={body.query_id!r}.",
                    403,
                )
            effective_sql = registered.sql
        else:
            # ── AUTHORING SCOPE GATE ──────────────────────────────────────────
            # Raw SQL execution (no registered query_id) requires author:sql.
            # Fail closed: absent scope → 403, regardless of any other claims.
            # This is the first-party branch (embed tokens are already blocked
            # above by the M3-SEC allowlist gate before reaching here).
            if not has_scope(_scopes, SCOPE_AUTHOR_SQL):
                raise _AppError(
                    "insufficient_scope",
                    "Token does not carry the required scope: author:sql — "
                    "raw SQL execution is not permitted without this scope.",
                    403,
                )
            registered = None
            effective_sql = body.sql

    # ── SECURITY: RLS policies from the VERIFIED identity only ───────────────
    claims = {"policies": identity.policies}

    # ── E.2: Hierarchical RLS expansion (org-scoped, fail-closed) ────────────
    # Expand any scalar policy value that has registered children in the
    # access_hierarchy table (e.g. region="Gauteng" → store_ids [1,2,3]).
    # Security contract:
    #   - org_id comes from the VERIFIED token (identity.org) or a trusted DB
    #     lookup — never from the request body.
    #   - Fail-closed: if expansion raises, keep the ORIGINAL (narrower) policy
    #     dict so we never widen access on error.
    #   - Zero cost when no hierarchy is configured: NullHierarchyResolver
    #     (the default) returns [] immediately and expand_policy returns the
    #     scalar unchanged — no DB call is made.
    #   - List/range-dict policies pass through unexpanded (already explicit).
    if identity.policies:
        try:
            from app.connectors.planner import expand_rls_policies as _expand_rls
            # Resolve org_id from the token (embed) or DB (first-party).
            # Same helper used below for rollup routing — no second lookup.
            _expand_org_id: str | None = (
                identity.org
                if identity.kind == "embed"
                else (await _resolve_caller_org(identity, get_repo(), request))[0]
            )
            if _expand_org_id:
                _expanded_policies = await _expand_rls(
                    dict(identity.policies), _expand_org_id
                )
                claims = {"policies": _expanded_policies}
            # If org is None (unscoped/demo), skip expansion — no hierarchy data.
        except Exception:  # noqa: BLE001 — fail-closed: keep original policy.
            pass

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

    # ── Target datastore + dialect resolution (BEFORE planning) ──────────────
    # Resolve the effective datastore id and its native sqlglot dialect BEFORE
    # planning so the planner emits warehouse-native SQL for BYO connectors —
    # most importantly, safe-cast forms (``TRY_CAST`` / ``SAFE_CAST``) survive
    # to the target engine instead of being downgraded to a plain ``CAST`` by a
    # hardcoded ``postgres`` dialect (mirrors the flow query path).  The
    # no-datastore / demo / lake fallback keeps the historical ``postgres``
    # default so existing planner behaviour is preserved.
    effective_datastore_id = await _resolve_effective_datastore_id(
        body, registered, identity
    )
    target_dialect = await _resolve_target_dialect(effective_datastore_id, identity, request)

    # ── Plan (RLS predicates injected at the AST level) ──────────────────────
    # [LOW event-loop] planner_plan() is pure-Python (sqlglot parse + RLS AST
    # rewrite); offload to a worker thread so the event loop is never blocked.
    # parse_sql_cached's lru_cache is GIL-protected (thread-safe).
    physical_plan = await asyncio.to_thread(
        planner_plan,
        sql=effective_sql,
        claims=claims,
        params=effective_params,
        dialect=target_dialect,
    )

    # ── Conservative rollup routing (RLS preserved through the rewrite) ──────
    # SECURITY/TENANT-ISOLATION: resolve the caller's org and pass it into the
    # router so ONLY this org's rollups are candidates.  Without org-scoping a
    # rollup built for another org (or an untagged demo rollup built by a
    # different org under org_id=None) could be reused cross-tenant.  When the
    # caller has no org (org_id=None) the router refuses to route at all.
    try:
        from app.connectors.planner import route_to_rollup_shape as _route_rollup
        from app.connectors.preagg import get_registry as _get_rollup_registry

        _rollup_org_id, _ = await _resolve_caller_org(identity, get_repo(), request)
        _route = _route_rollup(
            physical_plan, _get_rollup_registry(), org_id=_rollup_org_id
        )
        if _route.routed:
            physical_plan = _route.plan
            if _route.rollup_id:
                _get_rollup_registry().record_hit(_route.rollup_id)
    except Exception:  # noqa: BLE001 — routing must never break the query path.
        pass

    # ``effective_datastore_id`` was already resolved above (BEFORE planning)
    # so the planner could emit warehouse-native SQL in the target dialect.
    return _ResolvedPlan(
        physical_plan, registered, effective_datastore_id, effective_sql
    )


async def _resolve_caller_org(
    identity: VerifiedIdentity, repo, request: "Request | None" = None
) -> tuple[str | None, Exception | None]:
    """Resolve the caller's org id for attribution/quota (shared path).

    Embed tokens carry the org in the token claim; first-party tokens require a
    DB lookup. Returns ``(org_id, lookup_error)`` — a non-None error is only
    surfaced by the caller on the datastore path (the demo path tolerates a
    no-org caller).

    BUG FIX — pass *request* whenever you have it. Without it this resolves the
    user's DEFAULT (first) org and ignores ``X-Org-Id``, so a member of several
    orgs who switched workspaces got ``query_not_registered`` (403) for every
    registered query in the org they were actually looking at — the query is
    owned by org B, the gate checked org A. Boards in any non-default org
    therefore failed wholesale, which the old SAMPLE_TABLE fallback disguised as
    "sample data" instead of surfacing.

    ``resolve_org_id`` verifies membership before honouring the header (403 if
    the user is not a member), so this is strictly more correct scoping, not a
    weaker gate.
    """
    if identity.kind == "embed" and identity.org:
        return identity.org, None

    if request is not None:
        from app.routes._org import resolve_org_id as _resolve_org_id

        try:
            return await _resolve_org_id(identity.user_id, repo, request), None
        except Exception as exc:  # noqa: BLE001 — demo path tolerates no-org callers
            return None, exc

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
    and the capability-gated RLS refusal. The metering lives in the /query
    handler only — estimate never meters.

    Returns ``(connector, conn_kind, net_cleanup)``. ``net_cleanup`` tears down
    any ephemeral bridge tunnel and MUST be called by the caller in a finally.
    """
    # The demo connector stays here, not in the shared resolver: it is seeded
    # alongside its demo datasets in this module, and importing app.routes from
    # app.connectors would invert the layering.
    if effective_datastore_id is None:
        return _get_demo_connector(), "demo", (lambda: None)

    if org_id is None and org_lookup_error is not None:
        raise org_lookup_error

    # Delegated to the shared resolver — this block used to live here and was
    # copied (divergently) into app/dashboards/collect.py. See
    # app/connectors/resolve.py for why there must only be one of these.
    from app.connectors.resolve import resolve_datastore_connector  # noqa: PLC0415

    return await resolve_datastore_connector(
        physical_plan, effective_datastore_id, org_id, repo
    )


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

    # ── 2b. Org attribution (must precede the scoped cache lookup) ───────────
    # Resolve the caller's org BEFORE the cache lookup so we can scope the cache
    # key by (org_id, effective_datastore_id).  This is required to prevent
    # cross-tenant cache collisions: two tenants with policies={} running the
    # same SQL would otherwise produce an identical plan cache_key and one org
    # could be served the other's cached result bytes.
    #
    # Embed tokens carry the org in the token claim; first-party tokens require
    # a DB lookup.  Demo-path callers without an org membership keep working
    # (org_id=None → scoped under the empty-string sentinel, quota allows,
    # metering logs a warning); the datastore path re-raises the original
    # lookup error below to preserve its error contract.
    from app.routes.resources import get_user_org as _get_user_org

    repo = get_repo()
    # Header-aware (see _resolve_caller_org): this org_id scopes the cache key,
    # quota attribution AND the datastore lookup below, so resolving the user's
    # DEFAULT org here would make every registered query in a switched-into org
    # fail with datastore_not_found even once the allowlist gate had passed.
    org_id, _org_lookup_error = await _resolve_caller_org(identity, repo, request)

    # ── 2. Cache lookup (org+datastore-scoped key) ────────────────────────────
    # SECURITY: use scope_cache_key to ensure two orgs with policies={} running
    # the same SQL do NOT share a cache entry.  The scoped key is derived from
    # sha256(plan_cache_key + ':' + org_id + ':' + effective_datastore_id).
    # Same org + same datastore + same plan still hits the cache (deterministic).
    cache = get_cache()
    _scoped_cache_key = scope_cache_key(
        physical_plan.cache_key, org_id, effective_datastore_id
    )
    cached_bytes = cache.get(_scoped_cache_key)

    if cached_bytes is not None:
        # Cache HIT: stream the pre-serialised bytes directly.
        return StreamingResponse(
            ipc_stream_from_bytes(cached_bytes),
            media_type=_ARROW_STREAM_MEDIA_TYPE,
            headers={"X-Nubi-Cache": "HIT"},
        )

    # NOTE: Base-scan fusion (BET 2b) is DISABLED.
    # The base_scan_key helpers exist but are NOT used here: the original
    # implementation stored fully-aggregated bytes under the coarser key and
    # returned them to a different query with a different GROUP BY — wrong
    # schema and wrong values (data corruption).  A correct implementation
    # must cache the pre-GROUP-BY raw scan, which requires a plan-split that
    # is out of scope.  See cache_key.py module docstring for full rationale.
    _base_scan_key = compute_base_scan_key(
        physical_plan.sql,
        list(physical_plan.params),
        dict(physical_plan.rls_claims),
    )
    # _base_scan_key is computed (for future use / observability) but NOT
    # used to serve or store results.

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

    from app.features import enforce_quota as _enforce_quota

    # INVARIANT: embed tokens and first-party viewer-role users are NEVER metered.
    # Only first-party callers with a writer/admin/member/owner role trigger quota.
    _skip_metering: bool = identity.kind == "embed"
    if not _skip_metering and identity.user_id and org_id:
        from app.auth.roles import get_org_role as _get_org_role

        _caller_role = await _get_org_role(identity.user_id, org_id, repo)
        if _caller_role == "viewer":
            _skip_metering = True
    if not _skip_metering:
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
                # Execute view_sql if present (e.g. the local-parquet demo/sample
                # datastore config registers ``CREATE VIEW <t> AS
                # read_parquet('<local path>')`` — see
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

                # The only view_sql source for a ":memory:" duckdb datastore
                # is the local-parquet demo/sample config, which points at
                # LOCAL_PARQUET_DIR — allow-list just that directory rather
                # than leaving external access (incl. local-file reads)
                # wide open.
                _harden(
                    _mem_conn,
                    disable_external_access=True,
                    allowed_directories=[str(LOCAL_PARQUET_DIR)],
                )
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
        try:
            # Run off the event loop (M22-B): connector.execute() is a
            # synchronous, blocking call for most drivers (PyMySQL,
            # connectorx, psycopg2, ...). In network_mode='bridge' the same
            # event loop also has to run the bridge's async TCP proxy/reader
            # loop to service THIS connection — a blocking call here starves
            # that plumbing and the connection can never complete (deadlock:
            # "timed out waiting for connection"). asyncio.to_thread keeps the
            # loop free for direct-mode queries too; same pattern already used
            # for planning above (_resolve_request_plan → asyncio.to_thread).
            arrow_table = await asyncio.to_thread(connector.execute, physical_plan)
        except _AppError:
            raise
        except Exception as _exec_exc:  # noqa: BLE001 — classify engine errors
            # A malformed user query (unknown column/table, parse/binder/catalog
            # error) is a 400 client error, not a 500. Genuine infra failures
            # (connection/IO) re-raise → 500.
            _en = type(_exec_exc).__name__.lower()
            _em = str(_exec_exc).lower()
            if (
                any(k in _en for k in ("binder", "catalog", "parser", "syntax", "conversion", "invalidinput"))
                or any(k in _em for k in (
                    "binder error", "catalog error", "parser error", "syntax error",
                    "referenced column", "does not exist", "not found in from clause",
                ))
            ):
                raise _AppError(
                    "query_error",
                    f"Query could not be executed: {str(_exec_exc)[:300]}",
                    400,
                ) from _exec_exc
            raise

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
    except Exception as _query_exc:  # noqa: BLE001 — re-raised below; emit first.
        # ── Outbound webhook: query_failed (additive, best-effort) ───────────
        # Emit before re-raising so the host product learns about the failure.
        # Emitting NEVER changes the error returned to the caller.
        try:
            from app.webhooks.events import emit_query_failed  # noqa: PLC0415

            _err_code = getattr(_query_exc, "code", None) or type(_query_exc).__name__
            emit_query_failed(
                org_id,
                error_code=str(_err_code),
                message=str(_query_exc),
                datastore_id=effective_datastore_id,
                query_id=getattr(registered, "id", None),
            )
        except Exception:  # noqa: BLE001 — webhooks must never mask the query error.
            pass
        raise
    finally:
        try:
            _net_cleanup()
        except Exception:  # noqa: BLE001 — cleanup must never mask the query result/error.
            pass

    # ── 5b. Meter the execution (billing: compute_units) ─────────────────────
    # One event per cache MISS — hits cost no compute and are not metered.
    # units = compute-seconds (reconcile sums these into compute_units).
    # Best-effort: metering must never break the query path.
    #
    # SCALABILITY (HIGH): the response is NO LONGER blocked on the metering
    # INSERTs. Previously each usage event was ``await``-ed inline, adding two
    # serialised ``INSERT INTO usage_events`` round-trips to user-visible query
    # latency AND gating ``cache.put`` behind them. We now schedule the writes
    # fire-and-forget via ``record_usage_safe`` (it ``create_task``s on the
    # running loop and swallows any exception, so the request can never crash on
    # a metering failure). The event fields (kind, units, org, tier, …) are
    # IDENTICAL to before — only the timing relative to the response changes.
    # INVARIANT: embed tokens and first-party viewer-role users are NEVER
    # metered — neither the pre-flight quota gate (above) NOR the post-execution
    # usage_events writes here. Without this guard, viewer/embed CACHE MISSES
    # would still INSERT compute + query_scan rows that reconcile sums into
    # billable overage. ``_skip_metering`` was already computed for the gate.
    _elapsed_ms = int((_time.perf_counter() - _t0) * 1000)
    if not _skip_metering:
        try:
            from app.compute.metering import record_usage_safe as _record_usage_safe

            # Fire-and-forget: not awaited, so the response is not delayed by the
            # compute INSERT. Identical fields to the previous awaited call.
            _record_usage_safe(
                kind="compute",
                user_id=str(identity.user_id or "embed"),
                org_id=org_id,
                units=_elapsed_ms / 1000.0,
                tier=_conn_kind,
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
    # INVARIANT (see compute block above): viewer/embed callers are NEVER
    # metered — skip the query_scan write too, not just the quota gate.
    if not _skip_metering:
        try:
            from app.compute.metering import record_usage_safe as _record_usage_scan_safe

            # SCALABILITY (LOW): use the already-serialised Arrow IPC length
            # (``full_bytes``, computed above for the response/cache) as the
            # scanned-bytes proxy instead of ``arrow_table.get_total_buffer_size()``,
            # which would re-walk/materialise the whole Arrow table on the hot path
            # purely to produce a metering number. ``len(full_bytes)`` was already
            # the fallback here, so no new work is done. It remains an UNDER-counting
            # proxy (wide scans that aggregate down to a small result are
            # under-counted) pending W4-D/W4-F real lakehouse byte counters.
            _scanned_bytes = len(full_bytes)

            # Fire-and-forget: not awaited, so the response (and ``cache.put`` below)
            # is not delayed by the query_scan INSERT. Identical fields to before.
            _record_usage_scan_safe(
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
    # Tag the entry so the explicit /cache/invalidate endpoint can flush a
    # tenant's (and a datastore's) cached results. ``effective_datastore_id`` is
    # None on the demo path, so the datastore tag is added only when present.
    _cache_tags = [f"org:{org_id}"]
    if effective_datastore_id is not None:
        _cache_tags.append(f"datastore:{effective_datastore_id}")
    # SECURITY: use _scoped_cache_key (org+datastore-scoped) — never the raw
    # plan key — so two orgs with identical SQL but policies={} cannot share
    # a cache entry.  _scoped_cache_key was computed before the cache.get above.
    cache.put(_scoped_cache_key, full_bytes, tags=_cache_tags)

    # NOTE: Base-scan fusion write (BET 2b) is DISABLED.
    # put_base_scan is intentionally NOT called here — storing aggregated result
    # bytes under the coarser base_scan_key would corrupt responses for sibling
    # queries with a different GROUP BY.  See cache_key.py for full rationale.

    # ── 6b. Log the query for pre-agg mining (best-effort; never breaks query) ─
    try:
        get_query_log().record(
            _resolved.effective_sql, physical_plan.cache_key, byte_size=len(full_bytes)
        )
    except Exception:
        pass

    # ── 6c. Outbound webhook: query_executed (audit / POPIA log) ─────────────
    # Metadata-only — NO raw rows, NO SQL literals, NO filter values.
    # Fire-and-forget via emit_query_executed (which calls emit_event / dispatch_event).
    # A failure here NEVER reaches the caller.
    try:
        from app.webhooks.events import emit_query_executed  # noqa: PLC0415

        emit_query_executed(
            org_id,
            query_id=getattr(registered, "id", None),
            subject=str(identity.user_id or "embed"),
            datasource_id=effective_datastore_id,
            row_count=arrow_table.num_rows if arrow_table is not None else None,
        )
    except Exception:  # noqa: BLE001 — webhooks must never break the query path.
        pass

    # ── 6d. Schema-drift detection (best-effort, fire-and-forget) ─────────────
    # Extract column metadata from the Arrow result and schedule drift detection
    # in the background.  Never blocks or breaks the query path.
    # We only probe when there is an org AND a registered/named dataset key so
    # we have a stable identity to snapshot against.
    _drift_dataset_key = getattr(registered, "id", None) or (
        effective_datastore_id or None
    )
    if org_id and _drift_dataset_key and arrow_table is not None:
        try:
            from app.health.schema_drift import detect_schema_drift as _detect_drift  # noqa: PLC0415

            _live_cols = [
                {"name": arrow_table.schema.field(i).name,
                 "type": str(arrow_table.schema.field(i).type)}
                for i in range(arrow_table.schema.num_fields)
            ]
            asyncio.ensure_future(
                _detect_drift(str(org_id), str(_drift_dataset_key), _live_cols)
            )
        except Exception:  # noqa: BLE001 — drift detection must never break the query
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
    result cache, or meters usage — it is a pure dry-run (parse + plan + EXPLAIN)
    the front-end uses to gate the run.

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
    org_id, _org_lookup_error = await _resolve_caller_org(identity, repo, request)

    from app.features import enforce_quota as _enforce_quota

    # INVARIANT: embed tokens and first-party viewer-role users are NEVER metered.
    _skip_metering_est: bool = identity.kind == "embed"
    if not _skip_metering_est and identity.user_id and org_id:
        from app.auth.roles import get_org_role as _get_org_role_est

        _caller_role_est = await _get_org_role_est(identity.user_id, org_id, repo)
        if _caller_role_est == "viewer":
            _skip_metering_est = True

    # SECURITY/cost: an estimate is a dry-run (EXPLAIN), far cheaper than an
    # execution, and the UI may auto-refresh it on every keystroke. Charge a
    # SMALL fraction of a compute unit (not a full one) so estimates cannot be
    # abused to exhaust the org's execution quota — and so the charge matches
    # this route's "consumes a small dry-run budget" contract above.
    _ESTIMATE_QUOTA_UNITS = 0.05
    if not _skip_metering_est:
        await _enforce_quota(org_id, "compute_units", amount=_ESTIMATE_QUOTA_UNITS)

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
        # Same event-loop-starvation hazard as /query's execute() call above —
        # estimate() opens a connection too, and in network_mode='bridge' that
        # connection needs this same loop free to run the tunnel proxy.
        estimate = await asyncio.to_thread(connector.estimate, physical_plan)
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
# Registry visibility scoping (shared by the list + validate endpoints)
# ---------------------------------------------------------------------------


async def _visible_registry_row_ids(
    request: Request, identity: VerifiedIdentity
) -> set[str] | None:
    """Return the persisted query row ids the caller may see, or ``None``.

    ``None`` means "no scoping available" (persistence-free demo path) and the
    caller should not filter.  Built-in/seed queries (``RegisteredQuery.system``,
    e.g. ``demo_*``) are global and stay visible regardless of this set.

    Shared by ``GET /query/registry`` and ``POST /query/registry/validate`` so
    the two can never drift into different visibility rules — a validate call
    must not become a side channel that confirms the existence of another org's
    query ids.

    Fail-closed: any scoping error yields an EMPTY set, so non-system queries
    are never leaked cross-org.
    """
    try:
        repo = get_repo()
        if identity.kind == "embed":
            if identity.org:
                rows = await repo.list("queries", identity.org)
                return {str(r["id"]) for r in rows}
            return None
        from app.routes._org import (  # noqa: PLC0415
            resolve_org_id as _resolve_org_id,
            resolve_project_filter as _resolve_project_filter,
        )

        _org_id = await _resolve_org_id(identity.user_id, repo, request)
        _project_id = await _resolve_project_filter(_org_id, request)
        rows = await repo.list("queries", _org_id, _project_id)
        return {str(r["id"]) for r in rows}
    except Exception:  # noqa: BLE001 — scoping unavailable → fail closed (empty).
        return set()


# ---------------------------------------------------------------------------
# POST /query/registry/validate — batch "will this query even run?" check
# ---------------------------------------------------------------------------
# The query library lists every registered query, and a query whose stored SQL
# no longer parses fails only once the user opens it and presses Run.  Migrated
# estates can carry a lot of those (a legacy filter variable that rendered to
# the empty string leaves `WHERE d BETWEEN  AND x`), so the list needs to say
# up front which entries cannot run.
#
# This mirrors the POST /query pre-execution pipeline exactly — declared params
# rendered with their defaults, {{ vars.* }} resolved, parsed in the datastore's
# native dialect — so a green badge here means the same thing as a successful
# Run.  It is a batch endpoint on purpose: per-row calls would exhaust the
# 'query' rate-limit bucket that `/api/v1/query/*` shares.

_REGISTRY_VALIDATE_MAX_IDS = 250


class RegistryValidateIn(BaseModel):
    """Request body for POST /query/registry/validate."""

    ids: list[str] = Field(default_factory=list)


def _parse_check(sql: str, dialect: str) -> str | None:
    """Return ``None`` when *sql* parses, else a human-readable reason."""
    from app.connectors.planner import _humanise_parse_error  # noqa: PLC0415
    from app.connectors.sql_parse import parse_sql_cached  # noqa: PLC0415

    try:
        parse_sql_cached(sql, dialect=dialect)
        return None
    except sqlglot.errors.SqlglotError as exc:
        return _humanise_parse_error(exc)
    except Exception as exc:  # noqa: BLE001 — any parser failure is "won't run"
        return str(exc).splitlines()[0][:300] or "The SQL could not be parsed."


@router.post("/query/registry/validate")
async def validate_registered_queries(
    body: RegistryValidateIn,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Report which of the given registered queries can actually be planned.

    Body: ``{"ids": ["<query_id>", ...]}`` — capped at
    ``_REGISTRY_VALIDATE_MAX_IDS`` per call so one request stays cheap; the
    caller pages through a long library.

    Returns ``{"results": {"<id>": {"valid": true}
                                  | {"valid": false, "error": "..."}}}``.
    Ids the caller cannot see are simply absent from ``results`` — the endpoint
    never reveals whether an invisible id exists.
    """
    from app.errors import AppError as _AppError

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

    ids = list(dict.fromkeys(body.ids or []))[:_REGISTRY_VALIDATE_MAX_IDS]
    if not ids:
        return {"results": {}}

    registry = get_query_registry()
    row_ids = await _visible_registry_row_ids(request, identity)

    # {{ vars.* }} namespace — loaded once, not per query.
    template_vars: dict[str, object] = {}
    try:
        org_id, _ = await _resolve_caller_org(identity, get_repo(), request)
        if org_id:
            template_vars = await _load_query_vars(
                org_id, request.headers.get("X-Project-Id") or None
            )
    except Exception:  # noqa: BLE001 — best-effort, mirrors the query path.
        template_vars = {}

    dialect_cache: dict[str | None, str] = {}
    results: dict[str, dict] = {}

    for qid in ids:
        rq = registry.get(qid)
        if rq is None:
            continue
        if row_ids is not None and not rq.system and rq.id not in row_ids:
            continue  # invisible to this caller — omit rather than confirm

        # 1. Render exactly as POST /query would: declared params take their
        #    defaults, and {{ vars.* }} resolves from the org namespace.
        sql = rq.sql or ""
        try:
            if rq.params:
                resolved: dict[str, object] = {
                    p.name: p.default for p in rq.params
                }
                resolved["vars"] = template_vars
                effective_sql, _ = resolve_named_params(sql, resolved)
            elif "{{" in sql:
                effective_sql, _ = resolve_named_params(sql, {"vars": template_vars})
            else:
                effective_sql = sql
        except KeyError as exc:
            results[qid] = {
                "valid": False,
                "error": f"Template references an undefined variable: {exc}.",
            }
            continue
        except Exception as exc:  # noqa: BLE001 — a bad template is "won't run"
            results[qid] = {
                "valid": False,
                "error": f"The query template could not be rendered: {exc}"[:300],
            }
            continue

        if not effective_sql.strip():
            results[qid] = {"valid": False, "error": "The query has no SQL."}
            continue

        # 2. Parse in the datastore's native dialect, as the planner will.
        if rq.datastore_id not in dialect_cache:
            dialect_cache[rq.datastore_id] = await _resolve_target_dialect(
                rq.datastore_id, identity, request
            )
        dialect = dialect_cache[rq.datastore_id]

        # sqlglot parsing is CPU-bound; keep it off the event loop.
        error = await asyncio.to_thread(_parse_check, effective_sql, dialect)
        results[qid] = {"valid": True} if error is None else {
            "valid": False,
            "error": error,
        }

    return {"results": results}


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

    row_ids = await _visible_registry_row_ids(request, identity)
    if row_ids is not None:
        entries = [rq for rq in entries if rq.system or rq.id in row_ids]

    queries = []
    for rq in entries:
        queries.append(
            {
                "id": rq.id,
                "name": rq.name,
                "sql": rq.sql,
                "metric": rq.metric,
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


@router.get("/query/registry/{query_id}")
async def get_registered_query(
    query_id: str,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Return a single registered query by id, for deep-linking into the editor.

    The LIST endpoint above deliberately excludes slug-only registry entries
    (non-uuid ids with no persisted ``queries`` row) because those exist for the
    embed allowlist, not for first-party project browsing. That makes such
    queries -- e.g. every migrated board's ``q_xxxxxxxx`` -- impossible to open
    in the query editor even though the boards referencing them are readable.

    This endpoint closes that gap without widening the browse policy: a caller
    must already know the id (they got it from a board they can read), and
    resolution goes through ``resolve_registered_query``, which is org-scoped
    and returns ``None`` for another org's persisted query. So slug queries
    become OPENABLE BY ID while staying OUT OF THE LIST.

    Returns the same entry shape as GET /query/registry.

    Raises
    ------
    AppError("query_not_found", 404)
        If no query with *query_id* is visible to the caller's org.
    """
    from app.errors import AppError as _AppError

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

    # Must honour X-Org-Id exactly like GET /query/registry does —
    # _resolve_caller_org returns the user's DEFAULT org and would 404 for a
    # caller viewing any other org they belong to.
    org_id = None
    try:
        from app.routes._org import resolve_org_id as _resolve_org_id  # noqa: PLC0415

        org_id = await _resolve_org_id(identity.user_id, get_repo(), request)
    except Exception:  # noqa: BLE001 — embed/org-less callers fall through below
        if identity.kind == "embed" and identity.org:
            org_id = identity.org

    rq = await resolve_registered_query(query_id, org_id)
    if rq is None:
        raise _AppError(
            "query_not_found",
            f"No registered query found for id={query_id!r}.",
            404,
        )

    return {
        "id": rq.id,
        "name": rq.name,
        "sql": rq.sql,
        "metric": rq.metric,
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


@router.get("/query/registry/{query_id}/widgets")
async def list_query_widget_usages(
    query_id: str,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Return every widget, across every board in the caller's active
    project, that references *query_id* (via ``query_id`` or, for filter
    widgets, ``options_query_id``).

    Widgets are not a queryable DB resource on their own -- they live nested
    inside each board's ``config.spec.widgets`` array (the ``widgets`` table
    is an unused generic resource stub). Board counts per project are small,
    so this scans ``repo.list("boards", ...)`` in Python rather than adding a
    jsonb path query or any denormalized index -- same org/project scoping as
    GET /query/registry above.

    Returns
    -------
    dict
        ``{"widgets": [{board_id, board_name, widget_id, widget_type}]}``.
    """
    from app.errors import AppError as _AppError

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

    usages: list[dict] = []
    try:
        from app.routes._org import (  # noqa: PLC0415
            resolve_org_id as _resolve_org_id,
            resolve_project_filter as _resolve_project_filter,
        )

        repo = get_repo()
        _org_id = await _resolve_org_id(identity.user_id, repo, request)
        _project_id = await _resolve_project_filter(_org_id, request)
        boards = await repo.list("boards", _org_id, _project_id)
    except Exception:  # noqa: BLE001 — scoping unavailable → empty, not an error.
        boards = []

    for board in boards:
        widgets = (board.get("config") or {}).get("spec", {}).get("widgets", [])
        for w in widgets:
            if w.get("query_id") == query_id or w.get("options_query_id") == query_id:
                usages.append(
                    {
                        "board_id": board["id"],
                        "board_name": board.get("name", ""),
                        "widget_id": w.get("id", ""),
                        "widget_type": w.get("type", ""),
                    }
                )

    return {"widgets": usages}


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
    # Optional "expose as metric" block (query/metric unification) — validated +
    # persisted into config.metric so the metric registry picks this query up as
    # a governed metric (docs/query-metric-unification.md).
    metric: dict | None = None


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

    from app.errors import AppError as _AppError  # noqa: PLC0415
    from app.routes._org import require_not_embed as _require_not_embed  # noqa: PLC0415

    # Only first-party (kind='access') identities may write to the registry.
    _require_not_embed(identity, "register queries")

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

    # "Expose as metric" (query/metric unification): validate the optional metric
    # block against the same rules the /metrics write path uses, then carry it in
    # config.metric so the metric registry loads this query as a governed metric.
    # An absent/empty block leaves the query a plain query.
    if body.metric:
        from app.routes.metrics import validate_query_metric_block  # noqa: PLC0415

        validate_query_metric_block({**config, "metric": body.metric})
        config["metric"] = body.metric

    def _is_uuid(value: str) -> bool:
        try:
            _uuid.UUID(value)
        except (ValueError, TypeError):
            return False
        return True

    query_id: str | None = explicit_id
    # SECURITY (CRITICAL 1): stamped onto the in-memory registration below as
    # ``owner_org_id`` so resolve_registered_query() can enforce ownership on
    # every future ``registry.get()`` hit for this id — without this, a freshly
    # saved query stayed "unowned" (globally readable via query_id) in the
    # process-global registry until the next full reload. Stays None when
    # persistence/org-resolution fails below (matches the historical
    # best-effort, memory-only fallback — no behaviour regression).
    org_id: str | None = None
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
        metric=config.get("metric"),
        owner_org_id=org_id,
    )

    return {
        "id": rq.id,
        "name": rq.name,
        "sql": rq.sql,
        "required_scope": rq.required_scope,
        "datastore_id": rq.datastore_id,
        "metric": rq.metric,
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
