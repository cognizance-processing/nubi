"""SQL lineage extraction and graph building for the Nubi lineage index (M7-A).

Public API
----------
extract_lineage
    Parse a SQL SELECT and return the tables, columns, and output aliases it
    references.  Pure, deterministic, no network I/O.

build_graph
    Build an inverted lineage graph over a list of ``RegisteredQuery`` objects.
    Produces a ``LineageGraph`` with query-level detail plus table- and
    column-level inverted indexes.
"""

from app.lineage.extract import extract_lineage
from app.lineage.graph import LineageGraph, build_graph

__all__ = ["extract_lineage", "build_graph", "LineageGraph"]
