"""SECURITY regression: SQL injection via {{ params }} in flow SQL cells (B1).

Background
----------
``execute_task`` for ``kind == 'query'`` used to run ``_resolve_config`` over the
WHOLE config, which str-interpolated user-supplied ``{{ params.x }}`` /
``{{ inputs.* }}`` directly into the ``sql`` text (``str(val)``, no escaping).
That let an attacker pass ``params={"region": "x' UNION SELECT … --"}`` and inject
a UNION that the RLS predicate (added to the OUTER select only) never filters.

The fix binds every user-supplied value as a positional ``$N`` parameter (the same
guarantee the hardened ``/query`` endpoint provides) so the value is treated as
DATA, never parsed as SQL.

These tests prove:
  1. A malicious ``region`` value bound into a SQL cell does NOT smuggle extra
     rows in via a UNION — the result contains only legitimately-matching rows
     (here: zero, because the literal weird string matches nothing) and never the
     injected UNION's payload.
  2. A normal ``region`` value still filters correctly (the binding path did not
     break ordinary parameterised queries).
  3. The same protection holds for ``{{ inputs.* }}`` references.
  4. ``bind_sql_params`` rewrites user template refs into positional placeholders
     and never embeds the raw value in the SQL text.

We use the Python→SQL bridge (an upstream Python cell's ``rows`` become a DuckDB
table) so no live DB / registered query is required.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("ENV", "test")

from app.flows.executor import TaskContext, bind_sql_params, execute_task  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: a "secret" table reachable only via a UNION injection.
# ---------------------------------------------------------------------------

# Two upstream Python cells supply rows that become DuckDB tables:
#   - ``orders``  : the table the SQL cell legitimately reads.
#   - ``secrets`` : a separate (other-tenant) table the injection tries to leak.
_ORDERS_ROWS = [
    {"id": 1, "region": "north", "amount": 100},
    {"id": 2, "region": "south", "amount": 200},
    {"id": 3, "region": "north", "amount": 300},
]
_SECRETS_ROWS = [
    {"id": 99, "region": "TOPSECRET", "amount": 999999},
]


def _ctx(params: dict, inputs: dict | None = None) -> TaskContext:
    base_inputs = {
        "orders": {"rows": _ORDERS_ROWS, "row_count": len(_ORDERS_ROWS),
                   "columns": ["id", "region", "amount"]},
        "secrets": {"rows": _SECRETS_ROWS, "row_count": len(_SECRETS_ROWS),
                    "columns": ["id", "region", "amount"]},
    }
    if inputs:
        base_inputs.update(inputs)
    return TaskContext(flow_params=params, inputs=base_inputs)


def _query_task(sql: str) -> dict:
    return {"kind": "query", "config": {"sql": sql}, "timeout_s": 0}


# ---------------------------------------------------------------------------
# 1. Injection via {{ params.region }} must NOT smuggle in the UNION rows.
# ---------------------------------------------------------------------------


def test_param_union_injection_is_bound_not_executed():
    sql = "SELECT id, region, amount FROM orders WHERE region = {{ params.region }}"
    # Classic UNION injection payload.
    payload = "x' UNION SELECT id, region, amount FROM secrets --"

    outcome = execute_task(_query_task(sql), _ctx({"region": payload}), claims={})

    assert outcome["state"] == "success", outcome.get("error")
    rows = outcome["result"]["rows"]

    # The payload is bound as a single string literal — it matches no region, so
    # zero rows come back.  Crucially, the secret row is NEVER present.
    assert rows == [], f"injection leaked rows: {rows}"
    assert all(r.get("region") != "TOPSECRET" for r in rows)
    assert all(r.get("amount") != 999999 for r in rows)


# ---------------------------------------------------------------------------
# 2. A normal value still filters correctly (no regression).
# ---------------------------------------------------------------------------


def test_normal_param_value_still_filters():
    sql = "SELECT id, region, amount FROM orders WHERE region = {{ params.region }}"

    outcome = execute_task(_query_task(sql), _ctx({"region": "north"}), claims={})

    assert outcome["state"] == "success", outcome.get("error")
    rows = outcome["result"]["rows"]
    regions = sorted(r["region"] for r in rows)
    assert regions == ["north", "north"]
    assert {r["id"] for r in rows} == {1, 3}


# ---------------------------------------------------------------------------
# 3. The same protection applies to {{ inputs.* }} references.
# ---------------------------------------------------------------------------


def test_input_ref_injection_is_bound():
    sql = "SELECT id, region, amount FROM orders WHERE region = {{ inputs.upstream.region }}"
    payload = "x' UNION SELECT id, region, amount FROM secrets --"

    ctx = _ctx(
        params={},
        inputs={"upstream": {"region": payload}},
    )
    outcome = execute_task(_query_task(sql), ctx, claims={})

    assert outcome["state"] == "success", outcome.get("error")
    rows = outcome["result"]["rows"]
    assert rows == [], f"injection leaked rows: {rows}"
    assert all(r.get("region") != "TOPSECRET" for r in rows)


# ---------------------------------------------------------------------------
# 4. A normal numeric param binds with its native type.
# ---------------------------------------------------------------------------


def test_numeric_param_binds_with_type():
    sql = "SELECT id, region, amount FROM orders WHERE amount > {{ params.threshold }}"

    outcome = execute_task(_query_task(sql), _ctx({"threshold": 150}), claims={})

    assert outcome["state"] == "success", outcome.get("error")
    rows = outcome["result"]["rows"]
    assert {r["id"] for r in rows} == {2, 3}


# ---------------------------------------------------------------------------
# 5. bind_sql_params: unit-level proof the value is never in the SQL text.
# ---------------------------------------------------------------------------


def test_bind_sql_params_emits_placeholder_not_value():
    payload = "x' UNION SELECT 1 --"
    ctx = TaskContext(flow_params={"region": payload})
    rewritten, params = bind_sql_params(
        "SELECT * FROM t WHERE region = {{ params.region }}", ctx
    )

    # The dangerous text must be a bound param, NOT in the SQL.
    assert payload not in rewritten
    assert "UNION" not in rewritten.upper()
    assert "$1" in rewritten
    assert params == [payload]


def test_bind_sql_params_multiple_refs_are_each_bound():
    ctx = TaskContext(flow_params={"a": "alpha", "b": 7})
    rewritten, params = bind_sql_params(
        "SELECT * FROM t WHERE x = {{ params.a }} AND y = {{ params.b }}", ctx
    )
    assert "$1" in rewritten and "$2" in rewritten
    assert "alpha" not in rewritten
    assert params == ["alpha", 7]


def test_bind_sql_params_secret_resolved_inline():
    # Secrets are server-trusted and resolved inline (not bound) — historical
    # behaviour preserved.  No user param namespaces present → no positional bind.
    ctx = TaskContext(secrets={"TOKEN": "s3cr3t-value"})
    rewritten, params = bind_sql_params(
        "SELECT * FROM t WHERE token = '{{ secrets.TOKEN }}'", ctx
    )
    assert "s3cr3t-value" in rewritten
    assert params == []


def test_bind_sql_params_no_templates_is_noop():
    ctx = TaskContext(flow_params={})
    sql = "SELECT * FROM t WHERE region = 'north'"
    rewritten, params = bind_sql_params(sql, ctx)
    assert rewritten == sql
    assert params == []


# ---------------------------------------------------------------------------
# A5 slice 3 — {{ vars.* }} binds positionally in flow SQL cells
# ---------------------------------------------------------------------------


def test_flow_sql_binds_vars_namespace():
    """A flow SQL cell's {{ vars.key }} resolves to a BOUND positional param
    (never interpolated), using TaskContext.vars."""
    from app.flows.executor import TaskContext, bind_sql_params

    ctx = TaskContext(vars={"region": "eu-west", "n": 5})
    sql = "SELECT * FROM t WHERE region = {{ vars.region }} AND k > {{ vars.n }}"
    rewritten, params = bind_sql_params(sql, ctx)
    assert "eu-west" not in rewritten
    assert "$1" in rewritten and "$2" in rewritten
    assert params == ["eu-west", 5]   # native types preserved (str, int)


def test_flow_sql_vars_injection_payload_is_bound():
    from app.flows.executor import TaskContext, bind_sql_params

    payload = "x' UNION SELECT secret FROM other --"
    ctx = TaskContext(vars={"c": payload})
    rewritten, params = bind_sql_params("SELECT * FROM t WHERE c = {{ vars.c }}", ctx)
    assert payload not in rewritten
    assert params == [payload]
