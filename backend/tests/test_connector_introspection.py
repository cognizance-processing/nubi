"""Regression tests for cross-engine catalog introspection and org scoping.

These lock in four bugs that were all SILENT — none raised, each produced a
wrong-but-plausible response, so nothing failed loudly and nothing caught them:

1. ``information_schema`` result labels read case-sensitively. Correct on DuckDB
   and Postgres, wrong on MySQL / Snowflake / Oracle, which upper-case them. The
   lookup missed, ``zip()`` yielded nothing, and the API answered
   ``200 {"tables": []}`` — a healthy connector looked empty.
2. A total introspection failure swallowed into ``[]``, making an unreachable
   database indistinguishable from an empty one.
3. Table SQL left unqualified, so a table outside the connection's default
   schema could be listed but never opened, and a name present in several
   schemas silently resolved to the wrong one.
4. Blocking driver calls left on the event loop. In ``network_mode="bridge"``
   that loop also runs the tunnel serving the query, so blocking it deadlocks
   the request it is trying to answer.

Bugs 1-3 are covered by unit tests against the shared introspection module.
Bug 4 is a structural property, so it is enforced by an AST guard over the whole
``app`` package rather than by exercising a route.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pyarrow as pa
import pytest

from app.connectors.introspect import (
    introspect_tables,
    pick_col,
    resolve_table_ref,
)
from app.errors import AppError


# ---------------------------------------------------------------------------
# Fake connectors — one per engine "dialect" of result-label casing
# ---------------------------------------------------------------------------


class _StubConnector:
    """Answers information_schema / SHOW TABLES with caller-supplied tables."""

    def __init__(self, table: pa.Table | None, *, raises: Exception | None = None) -> None:
        self._table = table
        self._raises = raises
        self.calls: list[str] = []

    def execute(self, plan: Any) -> pa.Table:
        self.calls.append(plan.sql)
        if self._raises is not None:
            raise self._raises
        return self._table


def _upper_case_engine() -> _StubConnector:
    """MySQL/Snowflake/Oracle shape: UPPER-CASE result labels."""
    return _StubConnector(
        pa.table({"TABLE_SCHEMA": ["inventory", "USER_PORTAL"],
                  "TABLE_NAME": ["asset", "projects"]})
    )


def _lower_case_engine() -> _StubConnector:
    """DuckDB/Postgres shape: lower-case result labels."""
    return _StubConnector(
        pa.table({"table_schema": ["main", "main"], "table_name": ["a", "b"]})
    )


# ---------------------------------------------------------------------------
# 1. Case-insensitive label lookup
# ---------------------------------------------------------------------------


class TestResultLabelCasing:
    def test_upper_case_labels_still_yield_tables(self) -> None:
        """THE bug: MySQL upper-cases labels, so the table list came back empty."""
        tables = introspect_tables(_upper_case_engine())
        assert [t["name"] for t in tables] == ["asset", "projects"]
        assert [t["schema"] for t in tables] == ["inventory", "USER_PORTAL"]

    def test_lower_case_labels_unchanged(self) -> None:
        tables = introspect_tables(_lower_case_engine())
        assert [t["name"] for t in tables] == ["a", "b"]

    def test_pick_col_prefers_an_exact_match(self) -> None:
        """An exact hit wins, so a genuine case-sensitive collision is respected."""
        d = {"name": [1], "NAME": [2]}
        assert pick_col(d, "name") == [1]
        assert pick_col(d, "NAME") == [2]

    def test_pick_col_returns_empty_when_absent(self) -> None:
        assert pick_col({"other": [1]}, "table_name") == []

    def test_system_schemas_are_filtered_out(self) -> None:
        conn = _StubConnector(
            pa.table({
                "TABLE_SCHEMA": ["mysql", "performance_schema", "sys",
                                 "information_schema", "app_data"],
                "TABLE_NAME": ["user", "events", "config", "columns", "orders"],
            })
        )
        assert [t["name"] for t in introspect_tables(conn)] == ["orders"]


# ---------------------------------------------------------------------------
# 2. A failure must raise, never degrade to "no tables"
# ---------------------------------------------------------------------------


class TestFailureIsNotAnEmptyList:
    def test_unreachable_database_raises(self) -> None:
        """An empty list would render as 'this connector has no tables'."""
        conn = _StubConnector(None, raises=RuntimeError("server disconnected"))
        with pytest.raises(AppError) as exc:
            introspect_tables(conn)
        assert exc.value.status == 502

    def test_apperror_is_propagated_unchanged(self) -> None:
        original = AppError("query_error", "MySQL query failed: timed out", 500)
        conn = _StubConnector(None, raises=original)
        with pytest.raises(AppError) as exc:
            introspect_tables(conn)
        assert exc.value is original

    def test_genuinely_empty_catalog_is_still_empty(self) -> None:
        """A database that really has no user tables is NOT an error."""
        conn = _StubConnector(pa.table({"table_schema": [], "table_name": []}))
        # information_schema yields nothing → SHOW TABLES fallback also yields
        # nothing → an empty list, with no exception.
        assert introspect_tables(conn) == []


# ---------------------------------------------------------------------------
# 3. Schema-qualified table references
# ---------------------------------------------------------------------------


class TestResolveTableRef:
    TABLES = [
        {"schema": "inventory", "name": "asset"},
        {"schema": "USER_PORTAL", "name": "projects"},
        {"schema": "sales", "name": "orders"},
        {"schema": "analytics", "name": "orders"},
    ]

    def test_unique_name_is_qualified_with_its_own_schema(self) -> None:
        """Without this, a table outside the default schema 404s/500s on open."""
        assert resolve_table_ref("projects", self.TABLES) == "USER_PORTAL.projects"

    def test_explicit_schema_wins_for_an_ambiguous_name(self) -> None:
        assert resolve_table_ref("orders", self.TABLES, "sales") == "sales.orders"
        assert resolve_table_ref("orders", self.TABLES, "analytics") == "analytics.orders"

    def test_ambiguous_name_without_schema_stays_bare(self) -> None:
        """Falls back to the engine's default schema — the historical behaviour."""
        assert resolve_table_ref("orders", self.TABLES) == "orders"

    def test_unsafe_schema_is_never_interpolated(self) -> None:
        evil = [{"schema": 'x"; DROP TABLE users; --', "name": "t"}]
        assert resolve_table_ref("t", evil) == "t"

    def test_schema_not_matching_any_listed_table_is_ignored(self) -> None:
        assert resolve_table_ref("asset", self.TABLES, "nonexistent") == "inventory.asset"


# ---------------------------------------------------------------------------
# 4. Structural guard — no blocking driver calls on the event loop
# ---------------------------------------------------------------------------

# Sites that are safe to leave on the loop: each is a fresh in-process DuckDB
# with no network transport behind it, so there is no tunnel to starve. Adding
# to this list is a deliberate act — a connector that can resolve to
# network_mode="bridge" must NEVER appear here.
_LOOP_BLOCKING_ALLOWLIST = {
    ("app/connectors/resolve.py", "resolve_datastore_connector"),  # in-memory CREATE VIEW
    ("app/routes/query.py", "query"),                              # in-memory CREATE VIEW
    ("app/routes/compute.py", "compute_run"),                      # demo connector
    ("app/jobs/drift_sweep.py", "_fetch_live_columns"),            # fresh local DuckDB
}

_APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _blocking_execute_sites() -> list[tuple[str, int, str]]:
    """Return (relpath, lineno, func) for connector.execute() left on the loop."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover
            continue
        rel = path.relative_to(_APP_ROOT.parent).as_posix()
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            offloaded: set[int] = set()
            awaited: set[int] = set()
            nested: set[int] = set()
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and _call_name(n) == "to_thread":
                    offloaded.update(id(a) for a in n.args)
                if isinstance(n, ast.Await) and isinstance(n.value, ast.Call):
                    awaited.add(id(n.value))
                if isinstance(n, ast.FunctionDef):
                    nested.update(id(m) for m in ast.walk(n))
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Call) and _call_name(n) == "execute"):
                    continue
                if id(n) in offloaded or id(n) in awaited or id(n) in nested:
                    continue
                recv = getattr(n.func, "value", None)
                rname = getattr(recv, "id", None) or getattr(recv, "attr", None) or ""
                # Only connector-ish receivers; asyncpg/db handles are awaited.
                if any(k in rname.lower() for k in ("conn", "connector", "duckdb")):
                    found.append((rel, n.lineno, fn.name))
    return found


class TestNoBlockingConnectorCallsOnTheEventLoop:
    def test_no_unapproved_blocking_execute(self) -> None:
        offenders = [
            site for site in _blocking_execute_sites()
            if (site[0], site[2]) not in _LOOP_BLOCKING_ALLOWLIST
        ]
        assert not offenders, (
            "connector.execute() is blocking the event loop at:\n  "
            + "\n  ".join(f"{p}:{ln} in async {fn}()" for p, ln, fn in offenders)
            + "\n\nWrap it: `await asyncio.to_thread(connector.execute, plan)`.\n"
            "In network_mode='bridge' the event loop also runs the tunnel that "
            "serves this very query, so blocking it deadlocks the request."
        )

    def test_allowlist_has_no_stale_entries(self) -> None:
        """A fixed site must be removed from the allowlist, not left to rot."""
        actual = {(p, fn) for p, _, fn in _blocking_execute_sites()}
        stale = _LOOP_BLOCKING_ALLOWLIST - actual
        assert not stale, f"allowlist entries no longer blocking (remove them): {stale}"


# ---------------------------------------------------------------------------
# 5. Structural guard — request-bearing routes must honour X-Org-Id
# ---------------------------------------------------------------------------


class TestRoutesResolveOrgFromTheRequest:
    def test_no_route_with_a_request_uses_get_user_org(self) -> None:
        """``get_user_org`` ignores X-Org-Id and pins the user's FIRST org.

        Any handler that HAS a ``Request`` must use ``resolve_org_id`` instead,
        or the org switcher silently does nothing and the caller reads (or
        writes) another tenant's resources. ``get_user_org`` remains correct for
        genuinely request-free paths — login, background jobs.
        """
        offenders: list[str] = []
        for path in sorted((_APP_ROOT / "routes").rglob("*.py")):
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:  # pragma: no cover
                continue
            rel = path.relative_to(_APP_ROOT.parent).as_posix()
            if path.name == "_org.py":
                continue  # defines the helpers; its own fallback is the point
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.AsyncFunctionDef):
                    continue
                # An org-resolver wrapper legitimately falls back to
                # get_user_org when it has no request (e.g. ai._resolve_org_id).
                if fn.name.lstrip("_") == "resolve_org_id":
                    continue
                args = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
                if "request" not in args:
                    continue
                for n in ast.walk(fn):
                    if isinstance(n, ast.Call) and _call_name(n) == "get_user_org":
                        offenders.append(f"{rel}:{n.lineno} in async {fn.name}()")
        assert not offenders, (
            "these handlers take a Request but resolve the org with "
            "get_user_org, ignoring X-Org-Id:\n  " + "\n  ".join(offenders)
        )
