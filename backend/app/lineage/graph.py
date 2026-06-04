"""Lineage graph builder over registered queries (M7-A).

Public API
----------
build_graph(queries) -> LineageGraph
    Run ``extract_lineage`` over every ``RegisteredQuery`` and produce an
    inverted index mapping tables and columns to the query IDs that reference
    them.

LineageGraph
    A dataclass holding the full graph.  Use ``for_query(id)`` to look up the
    lineage detail for a single query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.lineage.extract import extract_lineage
from app.queries.registry import RegisteredQuery


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class LineageGraph:
    """Lineage graph over a set of registered queries.

    Attributes
    ----------
    queries : dict[str, dict]
        Per-query lineage detail keyed by query id.
        Each value is a dict:
        ``{"sql": str, "name": str, "tables": [...], "columns": [...],
        "outputs": [...], "error"?: str}``.

    tables : dict[str, list[str]]
        Inverted index: real table name → sorted list of query IDs that
        reference that table.

    columns : dict[str, list[str]]
        Inverted index: ``"table.column"`` → sorted list of query IDs.
        Columns with ``table=None`` are keyed as ``"_.column"`` to keep the
        key always a plain string.
    """

    queries: dict[str, dict[str, Any]] = field(default_factory=dict)
    tables: dict[str, list[str]] = field(default_factory=dict)
    columns: dict[str, list[str]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def for_query(self, query_id: str) -> dict[str, Any] | None:
        """Return the lineage detail dict for *query_id*, or ``None`` if unknown.

        Parameters
        ----------
        query_id:
            The registered query identifier (e.g. ``"demo_all"``).

        Returns
        -------
        dict or None
            The same structure as ``self.queries[query_id]``, or ``None`` when
            the id is not present in the graph.
        """
        return self.queries.get(query_id)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_graph(queries: list[RegisteredQuery]) -> LineageGraph:
    """Build a ``LineageGraph`` over *queries*.

    For each query:
    1. Run ``extract_lineage`` on its SQL.
    2. Store per-query lineage in ``graph.queries``.
    3. Update the inverted table index (``graph.tables``).
    4. Update the inverted column index (``graph.columns``).

    Parameters
    ----------
    queries:
        List of ``RegisteredQuery`` objects (from ``get_query_registry().all()``
        or a subset thereof).

    Returns
    -------
    LineageGraph
        A fully populated lineage graph.  The graph is computed synchronously
        and is safe to cache in memory for the lifetime of the application.

    Notes
    -----
    - Queries that fail to parse are included in ``graph.queries`` with an
      ``"error"`` key; they do NOT contribute to the table/column indexes so
      the graph remains accurate for the parseable queries.
    - The lists in ``tables`` and ``columns`` are sorted for deterministic
      output.
    """
    graph = LineageGraph()

    for rq in queries:
        lineage = extract_lineage(rq.sql)

        # Store per-query detail.
        detail: dict[str, Any] = {
            "sql": rq.sql,
            "name": rq.name,
            "tables": lineage["tables"],
            "columns": lineage["columns"],
            "outputs": lineage["outputs"],
        }
        if "error" in lineage:
            detail["error"] = lineage["error"]

        graph.queries[rq.id] = detail

        # Only update indexes for successfully parsed queries.
        if "error" in lineage:
            continue

        # ── Table inverted index ──────────────────────────────────────────
        for table_name in lineage["tables"]:
            graph.tables.setdefault(table_name, [])
            if rq.id not in graph.tables[table_name]:
                graph.tables[table_name].append(rq.id)

        # ── Column inverted index ─────────────────────────────────────────
        for col_ref in lineage["columns"]:
            t = col_ref.get("table") or "_"
            c = col_ref.get("column") or ""
            key = f"{t}.{c}"
            graph.columns.setdefault(key, [])
            if rq.id not in graph.columns[key]:
                graph.columns[key].append(rq.id)

    # Sort all lists in the inverted indexes for deterministic output.
    for v in graph.tables.values():
        v.sort()
    for v in graph.columns.values():
        v.sort()

    return graph
