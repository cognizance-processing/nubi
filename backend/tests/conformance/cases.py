"""Conformance cases — frozen golden fixtures for the Nubi M1-C suite.

Each entry in ``CONFORMANCE_CASES`` is a dict with the following keys:

``id`` (str)
    Unique human-readable identifier.
``sql`` (str)
    The *logical* SQL passed to ``plan()`` (before planner rewrites).
``claims`` (dict)
    JWT/auth claims forwarded to ``plan()``.  Use ``{}`` for no RLS.
``expected_cache_key`` (str)
    The SHA-256 hex cache key that ``plan(sql, claims).cache_key`` MUST equal.
    Computed by running the real planner once against the seed data and frozen here.
    Any drift in the planner or cache-key algorithm will break this assertion.
``expected_rows`` (list[dict])
    The rows the executor MUST return, expressed as a list of plain Python dicts
    (``{col: value}``).  Order-normalised where the query does not guarantee
    ordering (the test sorts both sides before comparison).
``expected_schema`` (dict[str, str])
    Mapping of column name → Arrow type string (e.g. ``"int32"``, ``"string"``,
    ``"double"``, ``"int64"``).  Validated against the Arrow schema of the result.

Frozen values were computed by running:

    python -c "
    from app.connectors.planner import plan
    from app.connectors.duckdb_conn import DuckDBConnector
    import pyarrow as pa
    conn = DuckDBConnector()
    conn.register({'users': <seed>, 'orders': <seed>})
    p = plan(sql, claims)
    r = conn.execute(p)
    print(p.cache_key, r.to_pydict())
    "

against the seed data defined in ``conftest.py``.  Do NOT edit these values
manually — re-run the code and paste the output.

Security note (RLS case)
------------------------
Case ``rls_tenant_filter`` proves that the ``acme`` RLS policy filters out the
3 ``globex`` rows.  The test explicitly asserts the *absence* of those rows.
This is the multi-tenant security regression guard: if the planner ever stops
injecting the predicate, the ``globex`` rows will appear and the conformance
test will fail.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

ConformanceCase = dict[str, Any]

# ---------------------------------------------------------------------------
# Frozen conformance cases
# ---------------------------------------------------------------------------

CONFORMANCE_CASES: list[ConformanceCase] = [
    # ── Case 1: plain SELECT * ──────────────────────────────────────────────
    {
        "id": "plain_select_all",
        "sql": "SELECT * FROM users",
        "claims": {},
        # cache_key computed from: sql="SELECT * FROM users", params=[], rls={}
        "expected_cache_key": "7db28a41eb3874ee532599663409fcda81bb1fc85e28c15914c106859114b159",
        "expected_rows": [
            {"id": 1, "tenant_id": "acme",   "name": "Alice", "age": 30},
            {"id": 2, "tenant_id": "acme",   "name": "Bob",   "age": 25},
            {"id": 3, "tenant_id": "acme",   "name": "Carol", "age": 35},
            {"id": 4, "tenant_id": "globex", "name": "Dave",  "age": 28},
            {"id": 5, "tenant_id": "globex", "name": "Eve",   "age": 42},
            {"id": 6, "tenant_id": "globex", "name": "Frank", "age": 31},
        ],
        "expected_schema": {
            "id":        "int32",
            "tenant_id": "string",
            "name":      "string",
            "age":       "int32",
        },
    },

    # ── Case 2: column projection ────────────────────────────────────────────
    {
        "id": "projection_id_name",
        "sql": "SELECT id, name FROM users",
        "claims": {},
        # cache_key computed from: sql="SELECT id, name FROM users", params=[], rls={}
        "expected_cache_key": "2da34f05b16152c531c0a460dc7b0ec722affc90998d44f4fa663b134f487054",
        "expected_rows": [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 3, "name": "Carol"},
            {"id": 4, "name": "Dave"},
            {"id": 5, "name": "Eve"},
            {"id": 6, "name": "Frank"},
        ],
        "expected_schema": {
            "id":   "int32",
            "name": "string",
        },
    },

    # ── Case 3: RLS tenant filter ────────────────────────────────────────────
    #
    # The planner injects ``WHERE tenant_id = 'acme'`` via AST rewrite.
    # Result MUST contain exactly the 3 acme rows; globex rows MUST be absent.
    # This is the multi-tenant security regression guard (ROADMAP §3.1 rule 4).
    {
        "id": "rls_tenant_filter",
        "sql": "SELECT * FROM users",
        "claims": {"policies": {"tenant_id": "acme"}},
        # cache_key computed from:
        #   sql="SELECT * FROM users WHERE tenant_id = 'acme'",
        #   params=[], rls={"tenant_id": "acme"}
        "expected_cache_key": "44b22d6419424efb5772afa9ce2541f46b75131a391ae3df3a131822fb06c901",
        # Only the 3 acme rows — globex rows (ids 4,5,6) must be absent.
        "expected_rows": [
            {"id": 1, "tenant_id": "acme", "name": "Alice", "age": 30},
            {"id": 2, "tenant_id": "acme", "name": "Bob",   "age": 25},
            {"id": 3, "tenant_id": "acme", "name": "Carol", "age": 35},
        ],
        "expected_schema": {
            "id":        "int32",
            "tenant_id": "string",
            "name":      "string",
            "age":       "int32",
        },
    },

    # ── Case 4: aggregate GROUP BY ───────────────────────────────────────────
    {
        "id": "aggregate_group_by_tenant",
        "sql": (
            "SELECT tenant_id, COUNT(*) AS cnt, AVG(age) AS avg_age "
            "FROM users GROUP BY tenant_id"
        ),
        "claims": {},
        # cache_key computed from: sql=<above>, params=[], rls={}
        "expected_cache_key": "5c7377c930c27b264fe37b67b042dd3a7ffbfb7bc01afe912467292b2a72f4e2",
        # acme: ages 30+25+35 = 90 → avg 30.0; globex: 28+42+31 = 101 → avg ≈ 33.667
        "expected_rows": [
            {"tenant_id": "acme",   "cnt": 3, "avg_age": 30.0},
            {"tenant_id": "globex", "cnt": 3, "avg_age": 33.666666666666664},
        ],
        "expected_schema": {
            "tenant_id": "string",
            "cnt":       "int64",
            "avg_age":   "double",
        },
    },
]
