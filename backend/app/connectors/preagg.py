"""Pre-aggregation suggester and rollup registry.

This module analyses the query log to identify high-frequency GROUP BY patterns
and suggests materialised rollup tables that would eliminate redundant aggregation.

Public API
----------
RollupSuggestion
    Dataclass representing a suggested pre-aggregation rollup.

suggest(log, min_hits=3) -> list[RollupSuggestion]
    Tally ``groupby_sig`` occurrences in *log* and emit one suggestion per
    pattern seen at least *min_hits* times, sorted by ``hits`` descending.

RollupRegistry
    In-memory dict mapping ``groupby_sig -> rollup_table_name``.

get_registry() -> RollupRegistry
    Return the process-wide singleton registry.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.connectors.query_log import QueryLog


# ---------------------------------------------------------------------------
# RollupSuggestion
# ---------------------------------------------------------------------------


@dataclass
class RollupSuggestion:
    """A suggested pre-aggregation materialisation.

    Attributes
    ----------
    base_table:
        The primary source table (first token of the ``groupby_sig``).
    dimensions:
        Sorted list of GROUP BY column expressions.
    measures:
        Sorted list of aggregate function expressions.
    hits:
        Number of times this GROUP BY pattern appears in the query log.
    est_bytes_saved:
        Sum of ``byte_size`` for all log entries that match this pattern.
        A rough proxy for I/O savings if the rollup were served from cache.
    sig:
        The full normalised ``groupby_sig`` string (for matching / registration).
    """

    base_table: str
    dimensions: list[str] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)
    hits: int = 0
    est_bytes_saved: int = 0
    sig: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation."""
        return {
            "base_table": self.base_table,
            "dimensions": self.dimensions,
            "measures": self.measures,
            "hits": self.hits,
            "est_bytes_saved": self.est_bytes_saved,
            "sig": self.sig,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sig(sig: str) -> tuple[str, list[str], list[str]]:
    """Parse a ``groupby_sig`` back into its components.

    Parameters
    ----------
    sig:
        A string of the form ``"<tables>|dims=<d1>,<d2>|aggs=<a1>,<a2>"``.

    Returns
    -------
    tuple[str, list[str], list[str]]
        ``(base_table, dimensions, measures)``
    """
    # Expected format: "tables|dims=...|aggs=..."
    parts = sig.split("|")
    base_table = parts[0] if parts else "unknown"

    dimensions: list[str] = []
    measures: list[str] = []

    for part in parts[1:]:
        if part.startswith("dims="):
            raw = part[len("dims="):]
            dimensions = [d for d in raw.split(",") if d]
        elif part.startswith("aggs="):
            raw = part[len("aggs="):]
            measures = [a for a in raw.split(",") if a]

    return base_table, dimensions, measures


# ---------------------------------------------------------------------------
# suggest()
# ---------------------------------------------------------------------------


def suggest(log: QueryLog, min_hits: int = 3) -> list[RollupSuggestion]:
    """Analyse *log* and return rollup suggestions for frequent GROUP BY patterns.

    Parameters
    ----------
    log:
        A ``QueryLog`` instance (or the singleton returned by
        ``get_query_log()``).
    min_hits:
        Minimum number of occurrences required to emit a suggestion.
        Default ``3``.

    Returns
    -------
    list[RollupSuggestion]
        One entry per distinct ``groupby_sig`` seen at least *min_hits* times,
        sorted by ``hits`` descending (most valuable first).
    """
    # Tally hits and byte_size per sig.
    hit_counts: Counter[str] = Counter()
    bytes_by_sig: dict[str, int] = {}

    for entry in log.entries():
        sig = entry.get("groupby_sig", "")
        if not sig:
            # No GROUP BY — skip.
            continue
        hit_counts[sig] += 1
        bytes_by_sig[sig] = bytes_by_sig.get(sig, 0) + entry.get("byte_size", 0)

    suggestions: list[RollupSuggestion] = []
    for sig, hits in hit_counts.items():
        if hits < min_hits:
            continue
        base_table, dimensions, measures = _parse_sig(sig)
        suggestions.append(
            RollupSuggestion(
                base_table=base_table,
                dimensions=dimensions,
                measures=measures,
                hits=hits,
                est_bytes_saved=bytes_by_sig.get(sig, 0),
                sig=sig,
            )
        )

    suggestions.sort(key=lambda s: s.hits, reverse=True)
    return suggestions


# ---------------------------------------------------------------------------
# RollupRegistry
# ---------------------------------------------------------------------------


class RollupRegistry:
    """In-memory registry mapping ``groupby_sig -> rollup_table_name``.

    Used by ``planner.route_to_rollup`` to look up whether a registered rollup
    table covers a given query pattern.
    """

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def register(self, sig: str, table: str) -> None:
        """Register a rollup table for a given ``groupby_sig``.

        Parameters
        ----------
        sig:
            The normalised GROUP BY signature (as produced by
            ``query_log.compute_groupby_sig``).
        table:
            The name of the materialised rollup table.
        """
        self._map[sig] = table

    def lookup(self, sig: str) -> str | None:
        """Return the rollup table name for *sig*, or ``None`` if unregistered.

        Parameters
        ----------
        sig:
            The normalised GROUP BY signature to look up.

        Returns
        -------
        str | None
            The rollup table name, or ``None``.
        """
        return self._map.get(sig)

    def registered(self) -> dict[str, str]:
        """Return a snapshot of all registered ``{sig: table}`` mappings."""
        return dict(self._map)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: RollupRegistry | None = None


def get_registry() -> RollupRegistry:
    """Return the process-wide ``RollupRegistry`` singleton."""
    global _registry
    if _registry is None:
        _registry = RollupRegistry()
    return _registry
