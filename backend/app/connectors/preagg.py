"""Auto pre-aggregations — mine the query log, materialize rollups, route.

This is the "Cube weapon" from ROADMAP §4: instead of running the same GROUP BY
over the raw fact table on every dashboard view, we

1. **mine** the query log for high-value aggregation shapes (the :func:`mine`
   miner / :func:`suggest` legacy wrapper),
2. **build** a materialized rollup table for a chosen shape
   (:func:`build_rollup`, reusing the DuckDB write path from
   ``app/flows/materialize.py`` and PRESERVING RLS-key columns), and
3. **route** matching incoming queries to the rollup when — and only when — the
   rewrite is provably sound (handled by
   ``app/connectors/planner.route_to_rollup``, which consults the
   :class:`RollupRegistry` populated here).

Honest about limits
-------------------
This is *suggest + build + conservative-route*, not a cost-based optimizer.  The
router only rewrites when it can prove soundness from the parsed shape (same
base table, query group-by ⊆ rollup dims, every measure derivable, every filter
column present in the rollup).  Anything it cannot prove sound is left untouched.

Public API
----------
mine(log, *, min_hits=3) -> list[RollupCandidate]
    The miner.  Cluster compatible aggregation shapes from *log* and rank them
    by ``frequency × scanned-bytes``.

suggest(log, min_hits=3) -> list[RollupSuggestion]
    Legacy sig-based suggester (kept for backwards-compatibility with M2-C).

build_rollup(candidate, *, rls_keys, ...) -> BuiltRollup
    The builder.  Materialize ``SELECT <dims>,<aggs> FROM <table> GROUP BY
    <dims>`` into a DuckDB rollup table, preserving RLS-key columns, register a
    datastore + runtime query, and record the rollup in the registry.

RollupRegistry / get_registry()
    Registry of built rollups keyed by base table, consulted by the router.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from app.connectors.query_log import (
    QueryLog,
    QueryShape,
    _measure_str,
    extract_shape,
)


# ===========================================================================
# 1. MINER
# ===========================================================================


@dataclass(frozen=True)
class RollupCandidate:
    """A ranked pre-aggregation candidate mined from the query log.

    Attributes
    ----------
    table:
        The base fact table the rollup would aggregate.
    dimensions:
        Union of all GROUP BY columns seen across the clustered shapes.  A
        rollup grouped on this superset can serve any member query whose
        group-by is a subset.
    measures:
        Sorted list of ``func(col)`` measure strings the rollup must compute.
    filters:
        Columns seen in WHERE clauses of clustered queries.  Surfaced so the
        builder knows which columns to KEEP in the rollup (alongside RLS keys)
        so post-rollup predicates still apply.
    score:
        Rank key = ``sample_count × est_bytes`` (frequency × scanned-bytes).
    sample_count:
        Number of log entries that contributed to this candidate.
    est_bytes:
        Sum of ``byte_size`` over the contributing entries (scan-cost proxy).
    cluster_key:
        Internal stable key = ``"<table>|<sorted dims>"`` used to merge
        compatible shapes.
    """

    table: str
    dimensions: list[str] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    score: int = 0
    sample_count: int = 0
    est_bytes: int = 0
    cluster_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cluster_key(shape: QueryShape) -> str:
    """Stable cluster key: same base table + same dimension set → one rollup.

    Queries that differ only in their *measures* or *filters* but share the
    base table and dimension set are merged into one candidate (the rollup's
    measure list becomes the union, so all member queries are servable).
    """
    return f"{shape.base_table}|{','.join(shape.dimensions)}"


def mine(log: QueryLog, *, min_hits: int = 3) -> list[RollupCandidate]:
    """Mine *log* for ranked pre-aggregation candidates.

    Algorithm
    ---------
    1. Parse each logged SQL with :func:`extract_shape`.  Skip non-aggregating
       and non-routable shapes (joins, derived grains, expression measures) —
       we only ever propose rollups we could actually route to.
    2. Cluster by ``(base_table, dimension-set)`` so queries that differ only in
       measures/filters share one rollup.
    3. For each cluster, take the union of measures and filter columns, sum the
       sample count and scanned bytes, and emit a :class:`RollupCandidate` when
       ``sample_count >= min_hits``.
    4. Rank by ``score = sample_count × est_bytes`` (frequency × scanned-bytes),
       descending.

    Returns
    -------
    list[RollupCandidate]
        Ranked candidates (highest score first).
    """
    counts: Counter[str] = Counter()
    bytes_by: dict[str, int] = {}
    table_by: dict[str, str] = {}
    dims_by: dict[str, list[str]] = {}
    measures_by: dict[str, set[str]] = {}
    filters_by: dict[str, set[str]] = {}

    for entry in log.entries():
        shape = extract_shape(entry.get("sql", ""))
        if shape is None or not shape.routable or shape.base_table is None:
            continue
        key = _cluster_key(shape)
        counts[key] += 1
        bytes_by[key] = bytes_by.get(key, 0) + int(entry.get("byte_size", 0))
        table_by[key] = shape.base_table
        dims_by[key] = list(shape.dimensions)
        measures_by.setdefault(key, set()).update(
            _measure_str(f, c) for (f, c) in shape.measures
        )
        filters_by.setdefault(key, set()).update(shape.filter_columns)

    candidates: list[RollupCandidate] = []
    for key, hits in counts.items():
        if hits < min_hits:
            continue
        est_bytes = bytes_by.get(key, 0)
        candidates.append(
            RollupCandidate(
                table=table_by[key],
                dimensions=sorted(dims_by[key]),
                measures=sorted(measures_by.get(key, set())),
                filters=sorted(filters_by.get(key, set())),
                score=hits * est_bytes,
                sample_count=hits,
                est_bytes=est_bytes,
                cluster_key=key,
            )
        )

    # Rank by score; tie-break on sample_count so a busy-but-tiny pattern still
    # ranks ahead of a never-seen one when byte_size is unknown (== 0).
    candidates.sort(key=lambda c: (c.score, c.sample_count), reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Legacy sig-based suggester (M2-C) — kept for backwards-compatibility.
# ---------------------------------------------------------------------------


@dataclass
class RollupSuggestion:
    """A legacy sig-based pre-aggregation suggestion (see :func:`suggest`)."""

    base_table: str
    dimensions: list[str] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)
    hits: int = 0
    est_bytes_saved: int = 0
    sig: str = ""

    def to_dict(self) -> dict:
        return {
            "base_table": self.base_table,
            "dimensions": self.dimensions,
            "measures": self.measures,
            "hits": self.hits,
            "est_bytes_saved": self.est_bytes_saved,
            "sig": self.sig,
        }


def _parse_sig(sig: str) -> tuple[str, list[str], list[str]]:
    """Parse a ``groupby_sig`` back into ``(base_table, dimensions, measures)``."""
    parts = sig.split("|")
    base_table = parts[0] if parts else "unknown"
    dimensions: list[str] = []
    measures: list[str] = []
    for part in parts[1:]:
        if part.startswith("dims="):
            dimensions = [d for d in part[len("dims="):].split(",") if d]
        elif part.startswith("aggs="):
            measures = [a for a in part[len("aggs="):].split(",") if a]
    return base_table, dimensions, measures


def suggest(log: QueryLog, min_hits: int = 3) -> list[RollupSuggestion]:
    """Legacy: tally ``groupby_sig`` occurrences and emit suggestions.

    Retained for backwards-compatibility with the M2-C tests and the original
    sig-based exact-match router.  New code should prefer :func:`mine`.
    """
    hit_counts: Counter[str] = Counter()
    bytes_by_sig: dict[str, int] = {}
    for entry in log.entries():
        sig = entry.get("groupby_sig", "")
        if not sig:
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


# ===========================================================================
# 3. ROLLUP REGISTRY  (consulted by planner.route_to_rollup)
# ===========================================================================


@dataclass
class BuiltRollup:
    """A materialized rollup table and the source shape it covers.

    Attributes
    ----------
    rollup_id:
        Stable id for the rollup (also the registered ``query_id``).
    table:
        The rollup table name inside its DuckDB file.
    source_table:
        The base fact table the rollup was built from.
    dimensions:
        GROUP BY columns the rollup is grouped on (the routable superset).
    measures:
        ``func(col)`` measure strings materialized in the rollup.
    rls_keys:
        RLS-key columns preserved in the rollup so read-time predicate
        injection (``WHERE <key> = <claim>``) still works.
    database / datastore_id / query_id:
        Wiring for the read path (materialized dataset served like any other).
    rewrite_sig (legacy):
        The exact ``groupby_sig`` this rollup answers, for the M2-C exact-match
        path; superset routing uses the structured fields above instead.
    hits:
        Count of incoming queries routed to this rollup (logged HITs).
    """

    rollup_id: str
    table: str
    source_table: str
    dimensions: list[str] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)
    rls_keys: list[str] = field(default_factory=list)
    database: str | None = None
    datastore_id: str | None = None
    query_id: str | None = None
    rewrite_sig: str = ""
    hits: int = 0

    @property
    def measure_cols(self) -> set[str]:
        """Set of source columns each measure reads (``"*"`` for COUNT(*))."""
        cols: set[str] = set()
        for m in self.measures:
            inside = m[m.find("(") + 1 : m.rfind(")")] if "(" in m else ""
            cols.add(inside)
        return cols

    @property
    def measure_funcs(self) -> set[tuple[str, str]]:
        """Set of ``(func, col)`` pairs the rollup materializes."""
        out: set[tuple[str, str]] = set()
        for m in self.measures:
            if "(" in m:
                func = m[: m.find("(")]
                col = m[m.find("(") + 1 : m.rfind(")")]
                out.add((func, col))
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class RollupRegistry:
    """Registry of built rollups, consulted by the router.

    Two lookup paths are supported:

    - :meth:`lookup` — legacy exact ``groupby_sig`` match (M2-C compatibility).
    - :meth:`candidates_for_table` — structured superset routing: return all
      built rollups for a base table so the router can pick a sound one.
    """

    def __init__(self) -> None:
        self._by_sig: dict[str, str] = {}  # legacy: sig -> table name
        self._rollups: dict[str, BuiltRollup] = {}  # rollup_id -> BuiltRollup

    # ── Legacy sig API (kept for existing tests / exact-match path) ──────────

    def register(self, sig: str, table: str) -> None:
        """Legacy: register a rollup table name for an exact ``groupby_sig``."""
        self._by_sig[sig] = table

    def lookup(self, sig: str) -> str | None:
        """Legacy: return the rollup table for an exact ``groupby_sig``."""
        return self._by_sig.get(sig)

    def registered(self) -> dict[str, str]:
        """Legacy: snapshot of ``{sig: table}`` mappings."""
        return dict(self._by_sig)

    # ── Structured rollup API (superset routing) ────────────────────────────

    def add_rollup(self, rollup: BuiltRollup) -> None:
        """Register a built rollup (also indexes its legacy sig if present)."""
        self._rollups[rollup.rollup_id] = rollup
        if rollup.rewrite_sig:
            self._by_sig[rollup.rewrite_sig] = rollup.table

    def candidates_for_table(self, table: str) -> list[BuiltRollup]:
        """Return all built rollups whose source table matches *table*."""
        t = table.lower()
        return [r for r in self._rollups.values() if r.source_table.lower() == t]

    def all_rollups(self) -> list[BuiltRollup]:
        """Return all built rollups (insertion order)."""
        return list(self._rollups.values())

    def get_rollup(self, rollup_id: str) -> BuiltRollup | None:
        return self._rollups.get(rollup_id)

    def record_hit(self, rollup_id: str) -> None:
        """Increment the routed-query (HIT) counter for *rollup_id*."""
        r = self._rollups.get(rollup_id)
        if r is not None:
            r.hits += 1


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: RollupRegistry | None = None


def get_registry() -> RollupRegistry:
    """Return the process-wide :class:`RollupRegistry` singleton."""
    global _registry
    if _registry is None:
        _registry = RollupRegistry()
    return _registry


def reset_for_tests() -> None:
    """Reset the rollup registry singleton (test-only)."""
    global _registry
    _registry = RollupRegistry()


# ===========================================================================
# 2. BUILDER
# ===========================================================================


def _rollup_database_path(rollup_id: str) -> str:
    """On-disk DuckDB target for a rollup: ``seed_data/rollups/<id>.duckdb``."""
    import os  # noqa: PLC0415

    here = os.path.dirname(os.path.abspath(__file__))
    backend = os.path.dirname(os.path.dirname(here))
    return os.path.join(backend, "seed_data", "rollups", f"{rollup_id}.duckdb")


def _measure_select_sql(measure: str) -> str:
    """Render a ``func(col)`` measure string into a SELECT expression with an
    alias, e.g. ``sum(amount)`` → ``SUM(amount) AS sum_amount``.

    ``count(*)`` → ``COUNT(*) AS count_all``.
    """
    func = measure[: measure.find("(")].upper()
    col = measure[measure.find("(") + 1 : measure.rfind(")")]
    if col == "*":
        return f'{func}(*) AS "{func.lower()}_all"'
    alias = f"{func.lower()}_{col}"
    return f'{func}("{col}") AS "{alias}"'


def build_rollup_sql(
    table: str,
    dimensions: list[str],
    measures: list[str],
    rls_keys: list[str],
) -> str:
    """Build the rollup materialization SQL.

    ``SELECT <rls_keys>, <dims>, <agg measures> FROM <table> GROUP BY <rls_keys>,
    <dims>``.

    RLS-key columns are added to BOTH the SELECT and the GROUP BY so the
    materialized table keeps a row per (rls_key, dims) combination — the planner
    can then inject ``WHERE <rls_key> = <claim>`` at READ time and still get a
    correct per-tenant aggregate.  (Pre-aggregating across the RLS key would be
    unsound — totals would mix tenants.)
    """
    # Group key = RLS keys first, then dimensions (deduped, order-stable).
    group_cols: list[str] = []
    for c in list(rls_keys) + list(dimensions):
        if c not in group_cols:
            group_cols.append(c)

    select_parts = [f'"{c}"' for c in group_cols]
    select_parts += [_measure_select_sql(m) for m in measures]
    select_sql = ", ".join(select_parts)
    group_sql = ", ".join(f'"{c}"' for c in group_cols) if group_cols else None

    sql = f'SELECT {select_sql} FROM "{table}"'
    if group_sql:
        sql += f" GROUP BY {group_sql}"
    return sql


def build_rollup(
    candidate: RollupCandidate | dict[str, Any],
    *,
    rls_keys: list[str] | None = None,
    source_database: str | None = None,
    rollup_id: str | None = None,
    registry: RollupRegistry | None = None,
    register_query: bool = True,
    datastore_id: str | None = None,
) -> BuiltRollup:
    """Materialize a rollup table for *candidate* and register it.

    Reuses the DuckDB write path established by ``app/flows/materialize.py``:
    aggregate the base fact into a fresh DuckDB file, verify RLS keys survived,
    then expose it via a registered ``SELECT * FROM <rollup>`` runtime query.

    Parameters
    ----------
    candidate:
        A :class:`RollupCandidate` (or its ``to_dict()``) naming the base table,
        dimensions and measures to materialize.
    rls_keys:
        Columns that MUST be kept (and grouped on) so read-time RLS predicate
        injection works on the rollup.  Verified post-build; a dropped key
        raises.
    source_database:
        Absolute path to the DuckDB file holding the base fact table.  When
        ``None`` the base table is expected to be resolvable in a fresh
        in-memory DuckDB (used by tests that register an Arrow table first via
        the returned connection — see ``build_rollup_from_arrow``).
    rollup_id:
        Stable id for the rollup (defaults to a generated one).
    registry:
        Registry to record the rollup in (defaults to the singleton).
    register_query:
        When ``True`` (default) register a runtime ``SELECT * FROM <rollup>``
        query so reads resolve without a restart.

    Returns
    -------
    BuiltRollup
        The materialization manifest, also recorded in the registry.
    """
    import os  # noqa: PLC0415
    import uuid  # noqa: PLC0415

    import duckdb  # noqa: PLC0415

    from app.errors import AppError  # noqa: PLC0415

    if isinstance(candidate, dict):
        table = candidate["table"]
        dimensions = list(candidate.get("dimensions") or [])
        measures = list(candidate.get("measures") or [])
    else:
        table = candidate.table
        dimensions = list(candidate.dimensions)
        measures = list(candidate.measures)

    rls_keys = list(rls_keys or [])
    rollup_id = rollup_id or f"rollup_{table}_{uuid.uuid4().hex[:8]}"
    rollup_table = f"rollup_{table}"
    registry = registry or get_registry()

    rollup_sql = build_rollup_sql(table, dimensions, measures, rls_keys)

    # ── Materialize: read base fact → aggregate → write rollup DuckDB file ────
    database = _rollup_database_path(rollup_id)
    os.makedirs(os.path.dirname(os.path.abspath(database)), exist_ok=True)

    src = duckdb.connect(database=source_database or ":memory:", read_only=False)
    try:
        rel = src.execute(rollup_sql)
        result = rel.arrow()
        if hasattr(result, "read_all"):
            result = result.read_all()
    finally:
        src.close()

    columns = list(result.schema.names)
    missing = [k for k in rls_keys if k not in columns]
    if missing:
        raise AppError(
            "rls_key_dropped",
            f"Rollup for {table!r} dropped declared rls_keys {missing!r}; the "
            "rollup must keep them so the planner can inject WHERE <key> = "
            f"<claim> at read time. Rollup columns: {columns!r}.",
            400,
        )

    out = duckdb.connect(database=database)
    try:
        out.register("_rollup_src", result)
        out.execute(f'DROP TABLE IF EXISTS "{rollup_table}"')
        out.execute(f'CREATE TABLE "{rollup_table}" AS SELECT * FROM _rollup_src')
        out.unregister("_rollup_src")
    finally:
        out.close()

    built = BuiltRollup(
        rollup_id=rollup_id,
        table=rollup_table,
        source_table=table,
        dimensions=sorted(dimensions),
        measures=sorted(measures),
        rls_keys=rls_keys,
        database=database,
        datastore_id=datastore_id,
        query_id=rollup_id,
    )
    registry.add_rollup(built)

    if register_query:
        try:
            from app.queries.registry import get_query_registry  # noqa: PLC0415

            get_query_registry().register(
                id=rollup_id,
                sql=f'SELECT * FROM "{rollup_table}"',
                name=f"Rollup — {table}",
                datastore_id=datastore_id,
            )
        except Exception:
            # Best-effort runtime registration; the materialized file is the
            # source of truth and is already written.
            pass

    return built
