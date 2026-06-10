"""Inferred dependency helpers for the Flows engine (SQLMesh-style).

For a ``query`` task with raw ``config['sql']``, dependencies are *inferred* by
parsing the SQL and matching referenced table identifiers to **sibling task
keys** in the same flow.  This mirrors SQLMesh / dbt ref-detection: a SQL cell
that does ``SELECT * FROM other_cell`` implicitly depends on ``other_cell``
without the author drawing an explicit edge.

Public API
----------
referenced_table_names(sql, dialect=None) -> set[str]
    Best-effort parse of *sql* with sqlglot; returns the set of base (unqualified)
    table identifiers referenced via FROM / JOIN / subqueries / CTE bodies.
    CTE-defined names (a ``WITH`` alias) are excluded — they are local aliases,
    not sibling deps.  Returns an empty set on any parse failure (never raises).

effective_needs(task, all_task_keys) -> list[str]
    union(explicit ``task['needs']``, inferred sibling refs).  Deterministic
    order: explicit needs first (original order, de-duplicated), then inferred
    extras sorted.  Self-references are excluded.  Inferred refs are filtered to
    *all_task_keys* so they can never reference an undeclared key.

Design notes
------------
- Inferred refs are NEVER persisted into ``task['needs']``.  Callers compute
  them on demand (run-order, preview, render) so the canonical FlowSpec stays
  minimal and codegen stays stable.
- A ``query`` task that uses ``query_id`` (not ``sql``) contributes no inferred
  refs (there is no parseable SQL on the task).
- Only ``query`` tasks contribute inferred refs; every other kind degrades to
  explicit-needs-only.
"""

from __future__ import annotations

from typing import Any, Iterable


def referenced_table_names(sql: str, dialect: str | None = None) -> set[str]:
    """Return the set of base table identifiers referenced in *sql*.

    Parses *sql* with sqlglot (best-effort) and collects the ``.name`` of every
    ``exp.Table`` node (the unqualified base name), subtracting any CTE alias
    names defined via ``WITH``.  Degrades to an empty set on any exception so the
    caller falls back to explicit-needs-only ordering.

    Parameters
    ----------
    sql:
        Raw SQL string (the cell's ``config['sql']``).
    dialect:
        Optional sqlglot dialect to read with (e.g. the cell's
        ``config['source_dialect']``).  ``None`` uses sqlglot's permissive
        default parser, which is sufficient for identifier extraction.

    Returns
    -------
    set[str]
        Unqualified table identifiers, excluding CTE-defined names.  Empty on
        parse failure or empty/blank input.
    """
    if not sql or not str(sql).strip():
        return set()

    try:
        import sqlglot  # noqa: PLC0415
        from sqlglot import exp  # noqa: PLC0415

        parsed = sqlglot.parse_one(sql, read=dialect)
        if parsed is None:
            return set()

        # CTE-defined names are local aliases, not sibling deps — subtract them.
        cte_names: set[str] = set()
        for cte in parsed.find_all(exp.CTE):
            alias = cte.alias
            if alias:
                cte_names.add(alias)

        tables: set[str] = set()
        for tbl in parsed.find_all(exp.Table):
            name = tbl.name
            if name and name not in cte_names:
                tables.add(name)

        return tables
    except Exception:  # noqa: BLE001 — best-effort; degrade to explicit-only.
        return set()


def effective_needs(task: dict[str, Any], all_task_keys: Iterable[str]) -> list[str]:
    """Return union(explicit needs, inferred sibling refs) for *task*.

    The result is the effective dependency list used to drive run-order / DAG
    readiness.  Order is deterministic:

    1. Explicit ``task['needs']`` in their original order (de-duplicated).
    2. Inferred sibling refs not already present, sorted alphabetically.

    Inferred refs are only computed for ``query`` tasks with a parseable
    ``config['sql']``.  They are filtered to *all_task_keys* (so an undeclared
    table reference such as ``demo`` is ignored) and exclude the task's own key
    (a self-reference is never a dependency).

    Parameters
    ----------
    task:
        A task spec dict (``key``, ``kind``, ``needs``, ``config``).
    all_task_keys:
        The set/iterable of sibling task keys in the same flow.

    Returns
    -------
    list[str]
        Effective dependency keys.
    """
    key_set: set[str] = set(all_task_keys)
    self_key = task.get("key")

    # ── Explicit needs (preserve order, de-dup) ──────────────────────────────
    ordered: list[str] = []
    seen: set[str] = set()
    for dep in task.get("needs") or []:
        if dep not in seen:
            seen.add(dep)
            ordered.append(dep)

    # ── Inferred sibling refs (query tasks with parseable SQL only) ──────────
    if task.get("kind") == "query":
        config = task.get("config") or {}
        sql = config.get("sql")
        if sql:
            dialect = config.get("source_dialect")
            refs = referenced_table_names(sql, dialect)
            inferred = sorted(
                r for r in refs
                if r in key_set and r != self_key and r not in seen
            )
            for r in inferred:
                seen.add(r)
                ordered.append(r)

    return ordered
