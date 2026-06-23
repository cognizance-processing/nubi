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

import os
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Registry size cap (env-overridable).
# ---------------------------------------------------------------------------

_DEFAULT_REGISTRY_MAX_ENTRIES = 1_000


def _registry_max_entries() -> int:
    """Return the max number of :class:`BuiltRollup` entries the registry keeps.

    Reads ``NUBI_ROLLUP_REGISTRY_MAX`` from the environment; falls back to
    :data:`_DEFAULT_REGISTRY_MAX_ENTRIES` (1000 entries).
    """
    raw = os.environ.get("NUBI_ROLLUP_REGISTRY_MAX", "")
    try:
        v = int(raw) if raw.strip() else _DEFAULT_REGISTRY_MAX_ENTRIES
        return max(1, v)  # always keep at least 1
    except ValueError:
        return _DEFAULT_REGISTRY_MAX_ENTRIES

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
    org_id:
        The organisation that owns this rollup.  Routing MUST only consider
        rollups whose ``org_id`` matches the query's org/identity to prevent
        cross-tenant misrouting (defense-in-depth on top of RLS).
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
    org_id: str | None = None
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

    Size cap / LRU eviction
    -----------------------
    The registry uses an :class:`~collections.OrderedDict` to track
    least-recently-used order.  When the number of entries exceeds
    ``max_entries`` the oldest (least-recently-used) entry is evicted so the
    registry cannot grow without bound in a long-running process.

    ``max_entries`` defaults to :func:`_registry_max_entries` (which reads
    ``NUBI_ROLLUP_REGISTRY_MAX`` from the environment, falling back to
    :data:`_DEFAULT_REGISTRY_MAX_ENTRIES` = 1000).
    """

    def __init__(self, max_entries: int | None = None) -> None:
        # OrderedDict preserves insertion order so we can evict the oldest entry
        # via popitem(last=False) in O(1).  Both _by_sig and _rollups use this
        # to stay bounded at max_entries.
        self._by_sig: OrderedDict[str, str] = OrderedDict()  # legacy: sig -> table name
        # OrderedDict gives O(1) move-to-end (LRU touch) and O(1) popitem(last=False)
        # (evict oldest).
        self._rollups: OrderedDict[str, BuiltRollup] = OrderedDict()
        self._max_entries: int = (
            max_entries if max_entries is not None else _registry_max_entries()
        )

    # ── Legacy sig API (kept for existing tests / exact-match path) ──────────

    def register(self, sig: str, table: str) -> None:
        """Legacy: register a rollup table name for an exact ``groupby_sig``.

        Bounded at ``self._max_entries``: when the dict is at capacity the
        oldest-inserted entry is evicted first (FIFO / insertion-order eviction
        via :class:`~collections.OrderedDict`).  This mirrors the LRU cap on
        ``_rollups`` so repeated ``register()`` calls cannot grow ``_by_sig``
        without bound.
        """
        if sig in self._by_sig:
            # Refresh position — move to end so it is not evicted soon.
            self._by_sig.move_to_end(sig)
        else:
            # Evict the oldest entry when at (or over) the cap.
            while len(self._by_sig) >= self._max_entries:
                self._by_sig.popitem(last=False)
        self._by_sig[sig] = table

    def lookup(self, sig: str) -> str | None:
        """Legacy: return the rollup table for an exact ``groupby_sig``."""
        return self._by_sig.get(sig)

    def registered(self) -> dict[str, str]:
        """Legacy: snapshot of ``{sig: table}`` mappings."""
        return dict(self._by_sig)

    # ── Structured rollup API (superset routing) ────────────────────────────

    def add_rollup(self, rollup: BuiltRollup) -> None:
        """Register a built rollup (also indexes its legacy sig if present).

        If the entry already exists it is refreshed (moved to most-recently-used
        position).  When the registry is at capacity the least-recently-used
        entry is evicted first (LRU eviction).
        """
        if rollup.rollup_id in self._rollups:
            # Refresh position — move to end (most-recently-used).
            self._rollups.move_to_end(rollup.rollup_id)
            self._rollups[rollup.rollup_id] = rollup
        else:
            # Evict the oldest entry if we are at (or over) the cap.
            while len(self._rollups) >= self._max_entries:
                evicted_id, evicted = self._rollups.popitem(last=False)
                # Clean up the legacy sig index for the evicted entry.
                # We must only remove the sig mapping when it still points to
                # the evicted rollup's table — two rollups may share a
                # rewrite_sig (e.g. rebuilt rollup), in which case the sig
                # already points to the newer table and must not be removed.
                if evicted.rewrite_sig and self._by_sig.get(evicted.rewrite_sig) == evicted.table:
                    del self._by_sig[evicted.rewrite_sig]
                # [HIGH disk leak] Evicting from memory must ALSO delete the
                # on-disk DuckDB file the rollup materialized into — otherwise
                # seed_data/rollups/<id>.duckdb accumulates forever and fills
                # the disk.  Only unlink a real file path (skip None / :memory:
                # / files shared by another live rollup at the same path).
                _unlink_rollup_database(evicted, keep=self._rollups)
            self._rollups[rollup.rollup_id] = rollup
        if rollup.rewrite_sig:
            # Use register() so _by_sig stays bounded at max_entries too.
            self.register(rollup.rewrite_sig, rollup.table)

    def candidates_for_table(
        self, table: str, *, org_id: str | None = None
    ) -> list[BuiltRollup]:
        """Return built rollups whose source table matches *table*.

        Parameters
        ----------
        table:
            Base fact table name (case-insensitive match).
        org_id:
            When provided, only rollups built for this org are returned.
            This is the primary tenant-isolation guard for routing: a rollup
            built for org A must never be considered when routing org B's query.
            When ``None`` only rollups that have no org tag are returned (i.e.
            unscoped / legacy rollups without an org_id), to avoid accidentally
            mixing tagged and untagged results.
        """
        t = table.lower()
        return [
            r for r in self._rollups.values()
            if r.source_table.lower() == t and r.org_id == org_id
        ]

    def all_rollups(self) -> list[BuiltRollup]:
        """Return all built rollups (insertion order)."""
        return list(self._rollups.values())

    def get_rollup(self, rollup_id: str) -> BuiltRollup | None:
        """Return the rollup by id and mark it as recently used."""
        r = self._rollups.get(rollup_id)
        if r is not None:
            self._rollups.move_to_end(rollup_id)
        return r

    def record_hit(self, rollup_id: str) -> None:
        """Increment the routed-query (HIT) counter for *rollup_id*.

        Also marks the rollup as recently used so frequently-hit rollups are
        less likely to be evicted.
        """
        r = self._rollups.get(rollup_id)
        if r is not None:
            r.hits += 1
            self._rollups.move_to_end(rollup_id)


# ---------------------------------------------------------------------------
# On-disk rollup file lifecycle helpers
# ---------------------------------------------------------------------------


def _unlink_rollup_database(
    rollup: BuiltRollup, *, keep: "OrderedDict[str, BuiltRollup] | None" = None
) -> None:
    """Best-effort delete the on-disk DuckDB file backing *rollup*.

    Called on LRU eviction so the materialized ``seed_data/rollups/<id>.duckdb``
    file does not outlive its registry entry (the [HIGH disk leak] fix).

    Guards:
    - ``database`` must be a real on-disk path (skip ``None`` / ``:memory:``).
    - The path must not still be referenced by another live rollup in *keep*
      (two rollups can share a path after shape-dedup reuse — never unlink a
      file a surviving rollup still reads from).

    Swallows :class:`OSError` (already gone / permission) — best-effort cleanup
    must never break eviction.
    """
    path = rollup.database
    if not path or path == ":memory:":
        return
    if keep is not None:
        for other in keep.values():
            if other.database == path:
                return  # another live rollup shares this file — keep it
    try:
        if os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass


def _rollup_shape_hash(
    table: str,
    dimensions: list[str],
    measures: list[str],
    rls_keys: list[str],
    org_id: str | None = None,
) -> str:
    """Stable hash of a rollup's materialized shape.

    Two builds with the same ``(table, sorted dims, sorted measures, sorted
    rls_keys, org_id)`` produce the SAME hash, so :func:`build_rollup` can reuse
    the existing rollup_id/path instead of minting a fresh UUID each time —
    eliminating scheduler/rebuild churn that would otherwise create a new
    on-disk file per build.
    """
    import hashlib  # noqa: PLC0415

    payload = "|".join(
        [
            table,
            ",".join(sorted(dimensions)),
            ",".join(sorted(measures)),
            ",".join(sorted(rls_keys)),
            org_id or "",
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"rollup_{table}_{digest}"


def sweep_orphan_rollups(registry: "RollupRegistry | None" = None) -> int:
    """Delete on-disk rollup files not referenced by any live registry entry.

    Globs ``seed_data/rollups/*.duckdb`` and unlinks every file whose absolute
    path is not the ``database`` of a rollup currently in *registry*.  This
    reclaims orphans left behind before the eviction-unlink fix (and any file
    leaked by a crash mid-build).  Best-effort: :class:`OSError` per-file is
    swallowed.  Returns the number of files removed.
    """
    import glob  # noqa: PLC0415

    registry = registry or get_registry()
    referenced = {
        os.path.abspath(r.database)
        for r in registry.all_rollups()
        if r.database and r.database != ":memory:"
    }
    rollups_dir = os.path.dirname(_rollup_database_path("_"))
    removed = 0
    for path in glob.glob(os.path.join(rollups_dir, "*.duckdb")):
        if os.path.abspath(path) in referenced:
            continue
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    return removed


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


# ---------------------------------------------------------------------------
# Metric-driven rollup builder
# ---------------------------------------------------------------------------


def build_rollup_for_metric(
    metric: Any,
    grains: list[str] | None = None,
    *,
    source_database: str | None = None,
    rollup_id: str | None = None,
    registry: "RollupRegistry | None" = None,
    register_query: bool = True,
    datastore_id: str | None = None,
    org_id: str | None = None,
) -> "BuiltRollup":
    """Materialize a rollup that covers a MetricDefinition's base measures.

    Produces a rollup whose shape is exactly what the metric compiler's
    ``__base`` CTE aggregates: base measures (additive components) +
    declared dimensions + time bucket column(s) at the requested grains +
    rls_keys.  This ensures the router's soundness check can route both
    flat queries and layered (derived / windowed) metric queries to the
    materialized rollup.

    Parameters
    ----------
    metric:
        A :class:`~app.metrics.models.MetricDefinition` instance.
    grains:
        Time-bucket grain columns to include as dimensions.  When the metric
        declares a ``time_dimension`` the column is included verbatim (the
        finest grain needed is the raw column, since the outer layer applies
        DATE_TRUNC on top).  Pass ``None`` to skip time columns.
    source_database:
        Absolute path to the DuckDB file holding the base fact table.
    rollup_id:
        Stable id for the rollup (defaults to a generated one).
    registry:
        Registry to record the rollup in (defaults to the singleton).
    register_query:
        When ``True`` (default) register a runtime ``SELECT * FROM <rollup>``
        query so reads resolve without a restart.
    datastore_id:
        Datastore the materialized rollup is served through.

    Returns
    -------
    BuiltRollup
        The materialization manifest, also recorded in the registry.

    Notes
    -----
    Derived measures are NOT materialized — they are recomputed from the
    base measure sums.  Only additive base measures (agg ∈ SUM/COUNT/MIN/MAX)
    are materialized; non-additive (AVG, percentile, approx_count_distinct)
    are silently skipped to keep the rollup conservative and re-routable.
    """
    # Resolve metric fields regardless of whether metric is a dataclass or dict.
    if isinstance(metric, dict):
        _base_table = metric.get("base_table") or metric.get("id", "unknown")
        _dims = [d["name"] if isinstance(d, dict) else d.name for d in metric.get("dimensions", [])]
        _measures_raw = metric.get("measures", [])
        _rls_keys = list(metric.get("rls_keys") or [])
        _time_col = None
        td = metric.get("time_dimension")
        if td:
            _time_col = td.get("column") if isinstance(td, dict) else getattr(td, "column", None)
    else:
        _base_table = metric.base_table or (metric.base_sql and "base") or "unknown"
        _dims = [d.name for d in metric.dimensions]
        _measures_raw = list(metric.measures())
        _rls_keys = list(metric.rls_keys or [])
        _time_col = metric.time_dimension.column if metric.time_dimension else None

    # ── Resolve additive base measures ───────────────────────────────────────
    # Only SUM/COUNT/MIN/MAX are re-aggregable (additive over the rollup grain).
    _ADDITIVE_AGGS = {"sum", "count", "min", "max"}
    measures_str: list[str] = []
    for m in _measures_raw:
        if isinstance(m, dict):
            agg = m.get("agg", "sum").lower()
            expr = m.get("expr", "*")
        else:
            agg = m.agg.lower()
            expr = m.expr
        if agg not in _ADDITIVE_AGGS:
            continue  # skip non-additive (count_distinct, approx_count_distinct, avg, percentile)
        measures_str.append(f"{agg}({expr})")

    if not measures_str:
        raise ValueError(
            f"Metric {getattr(metric, 'id', '?')!r} has no additive base measures "
            "that can be materialized into a rollup."
        )

    # ── Resolve dimensions ───────────────────────────────────────────────────
    dimensions = list(_dims)
    # Add the time column (raw, not date_trunced — outer layer applies trunc).
    if _time_col and grains is not None:
        if _time_col not in dimensions:
            dimensions.append(_time_col)

    candidate = RollupCandidate(
        table=_base_table,
        dimensions=sorted(dimensions),
        measures=sorted(measures_str),
    )

    return build_rollup(
        candidate,
        rls_keys=_rls_keys,
        source_database=source_database,
        rollup_id=rollup_id,
        registry=registry,
        register_query=register_query,
        datastore_id=datastore_id,
        org_id=org_id,
    )


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


_DEFAULT_ROLLUP_MAX_ROWS = 5_000_000


def _rollup_max_rows() -> int:
    """Return the maximum number of rows a materialised rollup may contain.

    Reads ``NUBI_ROLLUP_MAX_ROWS`` from the environment; falls back to
    :data:`_DEFAULT_ROLLUP_MAX_ROWS` (5 million rows).
    """
    import os  # noqa: PLC0415

    raw = os.environ.get("NUBI_ROLLUP_MAX_ROWS", "")
    try:
        return int(raw) if raw.strip() else _DEFAULT_ROLLUP_MAX_ROWS
    except ValueError:
        return _DEFAULT_ROLLUP_MAX_ROWS


def build_rollup(
    candidate: RollupCandidate | dict[str, Any],
    *,
    rls_keys: list[str] | None = None,
    source_database: str | None = None,
    rollup_id: str | None = None,
    registry: RollupRegistry | None = None,
    register_query: bool = True,
    datastore_id: str | None = None,
    org_id: str | None = None,
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
    org_id:
        Organisation that owns this rollup.  Stored on the :class:`BuiltRollup`
        so the registry can enforce org-scoped candidate filtering: a rollup
        built for org A will never be routed to org B's queries.

    Returns
    -------
    BuiltRollup
        The materialization manifest, also recorded in the registry.

    Raises
    ------
    AppError (``rollup_too_large``)
        When the aggregated rollup exceeds the ``NUBI_ROLLUP_MAX_ROWS`` limit
        (default: :data:`_DEFAULT_ROLLUP_MAX_ROWS`).  Prevents OOM on
        pathologically un-selective aggregations.
    """
    import os  # noqa: PLC0415

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
    registry = registry or get_registry()

    # ── Shape-hash DEDUP ──────────────────────────────────────────────────────
    # A rollup_id derived from the materialized shape (table, sorted dims,
    # sorted measures, sorted rls_keys, org) means repeated builds of the SAME
    # shape reuse one stable id/path instead of minting a fresh UUID — so the
    # scheduler / periodic rebuilds do not spawn a new on-disk DuckDB file each
    # run.  An explicit rollup_id (caller override) wins; otherwise we hash.
    if rollup_id is None:
        rollup_id = _rollup_shape_hash(table, dimensions, measures, rls_keys, org_id)
        # If a rollup with this exact shape is already registered, reuse it
        # outright — no rebuild, no new file.  (Scheduler churn elimination.)
        existing = registry.get_rollup(rollup_id)
        if existing is not None:
            return existing
    rollup_table = f"rollup_{table}"

    rollup_sql = build_rollup_sql(table, dimensions, measures, rls_keys)

    # ── Materialize: read base fact → aggregate → write rollup DuckDB file ────
    database = _rollup_database_path(rollup_id)
    os.makedirs(os.path.dirname(os.path.abspath(database)), exist_ok=True)

    max_rows = _rollup_max_rows()

    # Wrap rollup SQL with a LIMIT of max_rows+1 so DuckDB stops streaming at
    # the cap — this bounds the counting query too.
    capped_sql = (
        f"SELECT * FROM ({rollup_sql}) __rollup_check LIMIT {max_rows + 1}"
    )

    # ── Row-cap guard: COUNT first, materialize only when within the cap ──────
    # COUNT on the capped query is O(min(actual_rows, max_rows+1)) — DuckDB
    # stops at LIMIT so we never scan the full result for an over-cap rollup.
    count_sql = f"SELECT COUNT(*) FROM ({capped_sql}) __rollup_count"

    src = duckdb.connect(
        database=source_database or ":memory:",
        read_only=(source_database is not None),
    )
    try:
        row_count = src.execute(count_sql).fetchone()[0]
        if row_count > max_rows:
            raise AppError(
                "rollup_too_large",
                f"Rollup for {table!r} produced {row_count:,} rows which exceeds the "
                f"NUBI_ROLLUP_MAX_ROWS limit of {max_rows:,}. Reduce the rollup grain "
                "or raise the limit via the NUBI_ROLLUP_MAX_ROWS environment variable.",
                400,
            )
        # Within cap: materialize the (already-bounded) capped result.
        result = src.execute(capped_sql).arrow()
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
        org_id=org_id,
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
