"""Query registry — server-side allowlist of registered queries (M3-SEC).

Only queries present in this registry may be executed by embed-kind identities.
First-party (kind='access') identities may still run arbitrary SELECT SQL.

Design
------
- ``RegisteredQuery`` is an immutable dataclass: id, sql, name, an optional
  ``required_scope``, and an optional ``params`` list of ``QueryParam`` objects.
  When ``required_scope`` is set the caller must carry that scope (or a wildcard
  that covers it) in addition to the base read scope gate.
- Named placeholders in registry SQL use ``{{name}}`` syntax (M13-A).  The
  planner resolves these to the connector's positional ``$1``, ``$2``, … before
  execution — values are NEVER string-concatenated into SQL.
- ``QueryRegistry`` is a plain dict wrapper with register / unregister / get /
  all operations.  It is NOT thread-safe for concurrent writes (registration
  happens at module import time, which is single-threaded in CPython).
- ``get_query_registry()`` returns the module-level singleton.  Seed queries are
  registered at import time so they are available from the first request.

Demo seed queries
-----------------
id="demo_all"
    ``SELECT * FROM demo``  — returns all demo rows; no extra scope required.
id="demo_active"
    ``SELECT * FROM demo WHERE active = true``  — only active rows; no extra
    scope required beyond the read gate.

Point-cloud seed queries (M5-B)
--------------------------------
Synthetic point-cloud data generated via DuckDB ``generate_series`` + ``random()``.
No seed table is required — ``generate_series`` is a DuckDB built-in.

Columns: id (int), x (double), y (double), category (int 0–4).

id="demo_points_10k"   — 10 000 points (fast preview / unit test target)
id="demo_points_100k"  — 100 000 points (standard GPU scatter demo)
id="demo_points_500k"  — 500 000 points (stress test / large-data demo)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# QueryParam — typed/named parameter descriptor (M13-A)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryParam:
    """Descriptor for a single typed/named query parameter.

    Attributes
    ----------
    name:
        Parameter name (must match a ``{{name}}`` placeholder in the query SQL).
    type:
        One of ``'text'``, ``'number'``, ``'date'``, ``'daterange'``,
        ``'select'``, or ``'multiselect'``.
    default:
        Default value to use when the caller does not supply this parameter.
        ``None`` means no default.
    required:
        When ``True`` the caller MUST supply a value (no default accepted).
        A missing required param with no default → HTTP 400.
    options_query_id:
        Optional id of another registered query whose results populate the
        select/multiselect option list (for UI rendering).  Not validated at
        the backend param-resolution layer — the frontend uses it.
    """

    name: str
    type: Literal["text", "number", "date", "daterange", "select", "multiselect"] = "text"
    default: object = None
    required: bool = False
    options_query_id: str | None = None


# ---------------------------------------------------------------------------
# OutputColumn — declared output-shape contract (A4)
# ---------------------------------------------------------------------------

# The PORTABLE output-type set: a tiny, connector-agnostic vocabulary that the
# query route normalises every Arrow field type down to before comparing.  Any
# Arrow type that does not map cleanly (lists, structs, maps, binary, …) falls
# back to ``"json"``.
PORTABLE_OUTPUT_TYPES: frozenset[str] = frozenset(
    {"text", "number", "bool", "date", "timestamp", "json"}
)


@dataclass(frozen=True)
class OutputColumn:
    """A single declared output column for the output-shape contract (A4).

    Attributes
    ----------
    name:
        Output column name (must match the result column at the same position).
    type:
        One of the PORTABLE output types: ``text | number | bool | date |
        timestamp | json``.  The route normalises the actual Arrow field type
        to this vocabulary before comparing.
    """

    name: str
    type: Literal["text", "number", "bool", "date", "timestamp", "json"] = "text"


# ---------------------------------------------------------------------------
# RegisteredQuery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisteredQuery:
    """A server-registered, immutable query.

    Attributes
    ----------
    id:
        Stable, URL-safe identifier (e.g. ``"demo_all"``).  Clients reference
        queries by this id; the server resolves it to the canonical SQL.
    sql:
        The canonical SELECT SQL that the server will execute.  This is the
        ONLY SQL that will run for a given id — any ``sql`` field in the request
        body is completely ignored for embed-kind callers.
        Named placeholders use ``{{name}}`` syntax; the planner resolves them
        to positional ``$1``/``$2``/… bindings before execution.
    name:
        Human-readable label (for introspection / admin UIs).
    required_scope:
        Optional additional scope that the caller must carry beyond the base
        read gate.  ``None`` means no extra scope is required.  When set the
        route handler calls ``has_scope(identity.scope, required_scope)`` before
        executing.  Example: ``"read:query:demo_active"`` could restrict this
        query to tokens explicitly granted that scope.
    params:
        Ordered list of :class:`QueryParam` descriptors for the named
        placeholders declared in *sql*.  Empty list means no named params.
        Backward-compatible: existing ``register(...)`` calls without *params*
        still work (defaults to ``[]``).
    datastore_id:
        Optional id of the datastore this query should execute against.  When
        set (and the request body does not override it with its own
        ``datastore_id``), the route handler resolves this datastore — org-
        scoped — and executes the query through the real connector path instead
        of the in-memory demo connector.  ``None`` means the query has no bound
        datastore and falls back to the demo connector when the request body
        also omits ``datastore_id``.
    """

    id: str
    sql: str
    name: str
    required_scope: str | None = None
    params: tuple[QueryParam, ...] = field(default_factory=tuple)
    datastore_id: str | None = None
    output_schema: tuple[OutputColumn, ...] | None = None
    strict_output_schema: bool = False

    def params_as_list(self) -> list[QueryParam]:
        """Return params as a plain list (convenience helper)."""
        return list(self.params)


class QueryRegistry:
    """Registry of server-registered queries (the embed-token allowlist).

    Usage
    -----
    ::

        registry = get_query_registry()
        registry.register("my_query", "SELECT id FROM users", "User IDs")
        rq = registry.get("my_query")  # -> RegisteredQuery | None
        all_queries = registry.all()   # -> list[RegisteredQuery]
    """

    def __init__(self) -> None:
        self._store: dict[str, RegisteredQuery] = {}

    def register(
        self,
        id: str,
        sql: str,
        name: str,
        required_scope: str | None = None,
        params: list[QueryParam] | None = None,
        datastore_id: str | None = None,
        output_schema: list[OutputColumn] | None = None,
        strict_output_schema: bool = False,
    ) -> RegisteredQuery:
        """Register a query and return the ``RegisteredQuery`` object.

        Overwrites any existing registration with the same *id* — this is
        intentional so that seed queries can be refreshed at startup.

        Parameters
        ----------
        id:
            Stable, URL-safe identifier.
        sql:
            The canonical SELECT SQL string.  Named placeholders use
            ``{{name}}`` syntax; the planner resolves them to positional
            ``$1``/``$2``/… bindings before execution.
        name:
            Human-readable label.
        required_scope:
            Optional additional scope required beyond the base read gate.
        params:
            Optional list of :class:`QueryParam` descriptors for the named
            placeholders in *sql*.  When ``None`` or omitted defaults to ``[]``
            (backward-compatible: existing callers without *params* still work).
        datastore_id:
            Optional id of the datastore this query is bound to.  When set the
            query executes against that (org-scoped) datastore unless the
            request body supplies its own ``datastore_id``.

        Returns
        -------
        RegisteredQuery
            The newly registered query object.
        """
        rq = RegisteredQuery(
            id=id,
            sql=sql,
            name=name,
            required_scope=required_scope,
            params=tuple(params) if params else (),
            datastore_id=datastore_id,
            output_schema=tuple(output_schema) if output_schema else None,
            strict_output_schema=strict_output_schema,
        )
        self._store[id] = rq
        return rq

    def get(self, id: str) -> RegisteredQuery | None:
        """Return the ``RegisteredQuery`` for *id*, or ``None`` if not found."""
        return self._store.get(id)

    def all(self) -> list[RegisteredQuery]:
        """Return all registered queries as a list (insertion order)."""
        return list(self._store.values())

    def unregister(self, id: str) -> None:
        """Remove a query from the registry (mainly useful in tests)."""
        self._store.pop(id, None)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: QueryRegistry | None = None


def reset_for_tests() -> None:
    """Reset the query registry singleton to its default seeded state.

    Clears any test-registered queries and re-seeds the built-in demo queries
    so every test starts from a known baseline.  This is intentionally a
    test-only helper — production code should never call it.
    """
    global _registry
    _registry = None
    get_query_registry()


def get_query_registry() -> QueryRegistry:
    """Return (or create) the module-level ``QueryRegistry`` singleton.

    The registry is seeded with demo queries on first call so that the embed
    flow works out of the box with no external configuration.

    Seed queries
    ------------
    ``demo_all``
        ``SELECT * FROM demo`` — all demo rows.
    ``demo_active``
        ``SELECT * FROM demo WHERE active = true`` — active rows only.

    Point-cloud queries (M5-B)
    --------------------------
    ``demo_points_10k``
        10 000 synthetic points (id, x, y, category).
    ``demo_points_100k``
        100 000 synthetic points — standard GPU scatter demo size.
    ``demo_points_500k``
        500 000 synthetic points — large-data / stress demo.
    """
    global _registry
    if _registry is None:
        _registry = QueryRegistry()
        # ── Seed demo queries ─────────────────────────────────────────────
        _registry.register(
            id="demo_all",
            sql="SELECT * FROM demo",
            name="Demo — all rows",
            required_scope=None,
        )
        _registry.register(
            id="demo_active",
            sql="SELECT * FROM demo WHERE active = true",
            name="Demo — active rows only",
            required_scope=None,
        )
        # ── Seed point-cloud queries (M5-B) ──────────────────────────────
        # DuckDB generate_series is a built-in; no seed table is required.
        # These queries run on the demo DuckDB connector path in routes/query.py.
        _registry.register(
            id="demo_points_10k",
            sql=(
                "SELECT i AS id, random() AS x, random() AS y, (i % 5) AS category"
                " FROM generate_series(1, 10000) AS t(i)"
            ),
            name="Point cloud — 10 000 points",
            required_scope=None,
        )
        _registry.register(
            id="demo_points_100k",
            sql=(
                "SELECT i AS id, random() AS x, random() AS y, (i % 5) AS category"
                " FROM generate_series(1, 100000) AS t(i)"
            ),
            name="Point cloud — 100 000 points",
            required_scope=None,
        )
        _registry.register(
            id="demo_points_500k",
            sql=(
                "SELECT i AS id, random() AS x, random() AS y, (i % 5) AS category"
                " FROM generate_series(1, 500000) AS t(i)"
            ),
            name="Point cloud — 500 000 points",
            required_scope=None,
        )
        # ── Demo query with region/variable binding (M14-C) ──────────────────
        # Used to demonstrate end-to-end variable routing: a filter widget sets
        # the `region` variable, which is passed as named_params.region to this
        # query.  The demo table has a `name` column; we treat name = region
        # value as the filter to keep the demo self-contained with no extra tables.
        # When region is NULL (no value supplied / default) the query returns all rows.
        # NOTE: {{region}} appears once → resolves to $1.  The `OR $1 IS NULL`
        # arm lets us use a single positional slot without confusing sqlglot's
        # dollar-quote tokeniser (which trips on `$1 = ''` patterns).
        _registry.register(
            id="demo_by_region",
            sql=(
                "SELECT * FROM demo WHERE (name = {{region}} OR {{region}} IS NULL)"
            ),
            name="Demo — filtered by region",
            required_scope=None,
            params=[
                QueryParam(
                    name="region",
                    type="text",
                    default=None,
                    required=False,
                ),
            ],
        )
    return _registry


# ---------------------------------------------------------------------------
# Persisted-query loader (DB → runtime registry)
# ---------------------------------------------------------------------------


def _params_from_config(raw: object) -> list[QueryParam]:
    """Build a list of :class:`QueryParam` from a persisted config value.

    The persisted form is a list of dicts (as written by the seeder), e.g.::

        [{"name": "region", "type": "select", "default": "north",
          "required": False, "options_query_id": None}]

    Unknown/missing keys fall back to sensible defaults.  Non-list / malformed
    input yields an empty list (best-effort — never raises).
    """
    if not isinstance(raw, list):
        return []
    out: list[QueryParam] = []
    for item in raw:
        if not isinstance(item, dict) or "name" not in item:
            continue
        try:
            out.append(
                QueryParam(
                    name=str(item["name"]),
                    type=item.get("type", "text"),
                    default=item.get("default"),
                    required=bool(item.get("required", False)),
                    options_query_id=item.get("options_query_id"),
                )
            )
        except Exception:
            # Skip a single malformed param rather than dropping the whole query.
            continue
    return out


def _schema_from_config(raw: object) -> tuple[OutputColumn, ...] | None:
    """Build the declared ``output_schema`` from a persisted config value (A4).

    The persisted form mirrors how ``params`` are persisted — a list of dicts::

        [{"name": "id", "type": "number"}, {"name": "label", "type": "text"}]

    Each ``type`` is coerced to the PORTABLE set
    (``text|number|bool|date|timestamp|json``); an unknown/missing type falls
    back to ``"text"``.  Returns ``None`` when the config carries no schema
    (so queries without a declared contract skip validation entirely) and a
    tuple of :class:`OutputColumn` otherwise.  Best-effort: never raises.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    out: list[OutputColumn] = []
    for item in raw:
        if not isinstance(item, dict) or "name" not in item:
            continue
        try:
            t = str(item.get("type") or "text").strip().lower()
            if t not in PORTABLE_OUTPUT_TYPES:
                t = "text"
            out.append(OutputColumn(name=str(item["name"]), type=t))  # type: ignore[arg-type]
        except Exception:
            continue
    # An explicit empty list is a declared (empty) contract → keep it as ().
    return tuple(out)


async def load_persisted_queries() -> int:
    """Load queries from the ``queries`` table into the runtime registry.

    Each row's config is expected to carry ``{"sql", "datastore_id", "params",
    "name"}`` (as written by the seeder).  The row ``id`` becomes the registered
    query id and the row ``name`` (or ``config.name``) becomes the label.

    This is best-effort: any failure to reach the DB or parse a row is logged
    as a warning and never propagated, so it can be wired into startup without
    risking the app failing to boot when the DB/table is unavailable.

    Returns
    -------
    int
        The number of queries successfully registered.
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        # Lazy import so importing the registry module never requires the DB
        # layer (keeps unit tests / import-time seeding side-effect free).
        from app.db import fetch

        rows = await fetch("SELECT id, name, config FROM queries")
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash startup.
        logger.warning("load_persisted_queries: could not read queries table: %s", exc)
        return 0

    registry = get_query_registry()
    loaded = 0
    for row in rows:
        try:
            cfg = row["config"]
            if isinstance(cfg, str):
                import json

                cfg = json.loads(cfg)
            if not isinstance(cfg, dict):
                continue

            sql = cfg.get("sql")
            if not sql:
                # A query row with no SQL is not executable — skip it.
                continue

            datastore_id = cfg.get("datastore_id")
            _schema = _schema_from_config(cfg.get("output_schema"))
            registry.register(
                id=str(row["id"]),
                sql=str(sql),
                name=str(cfg.get("name") or row["name"] or row["id"]),
                params=_params_from_config(cfg.get("params")),
                datastore_id=str(datastore_id) if datastore_id is not None else None,
                output_schema=list(_schema) if _schema is not None else None,
                strict_output_schema=bool(cfg.get("strict_output_schema", False)),
            )
            loaded += 1
        except Exception as exc:  # noqa: BLE001 — skip one bad row, keep going.
            logger.warning(
                "load_persisted_queries: skipping malformed query row: %s", exc
            )
            continue

    if loaded:
        logger.info("load_persisted_queries: registered %d persisted queries", loaded)
    return loaded


async def ensure_persisted_query(query_id: str):
    """Lazily load a single persisted query into the registry on a cache miss.

    The runtime registry is populated at startup, so queries seeded/registered
    while the server is running are invisible until restart.  The query route
    calls this on a ``registry.get()`` miss to load just that row from the DB,
    making freshly-seeded queries resolve without a restart.

    Best-effort: returns the ``RegisteredQuery`` if found+loaded, else ``None``.
    """
    import logging

    logger = logging.getLogger(__name__)
    registry = get_query_registry()
    try:
        from app.db import fetchrow

        row = await fetchrow(
            "SELECT id, name, config FROM queries WHERE id = $1::uuid", query_id
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the request.
        logger.warning("ensure_persisted_query(%s): DB read failed: %s", query_id, exc)
        return None
    if row is None:
        return None
    try:
        cfg = row["config"]
        if isinstance(cfg, str):
            import json

            cfg = json.loads(cfg)
        if not isinstance(cfg, dict) or not cfg.get("sql"):
            return None
        datastore_id = cfg.get("datastore_id")
        _schema = _schema_from_config(cfg.get("output_schema"))
        registry.register(
            id=str(row["id"]),
            sql=str(cfg["sql"]),
            name=str(cfg.get("name") or row["name"] or row["id"]),
            params=_params_from_config(cfg.get("params")),
            datastore_id=str(datastore_id) if datastore_id is not None else None,
            output_schema=list(_schema) if _schema is not None else None,
            strict_output_schema=bool(cfg.get("strict_output_schema", False)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_persisted_query(%s): register failed: %s", query_id, exc)
        return None
    return registry.get(query_id)
