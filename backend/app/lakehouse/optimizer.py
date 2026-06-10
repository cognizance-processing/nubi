"""The managed-lakehouse optimizer skeleton (MANAGED_LAKEHOUSE.md §1 & §4).

One self-managing optimizer owns the mapping from the *logical* tables you query
to the *physical* structures it maintains (layout, materializations, rewrite).
It is automatic by default (posture **C+A**) and customizable per table via
``nubi.toml``.

The optimizer is **application logic on DuckDB, not a new engine** (§1).  Pre-agg
splits into three parts and only the third is per-connector:

1. **Rewrite/routing** — :func:`app.connectors.planner.route_to_rollup_shape`
   (sqlglot, connector-agnostic).
2. **Materialization** — always lands in the lakehouse (Parquet in R2, queried
   by DuckDB).
3. **Refresh** — the only per-connector bit; run the aggregate via
   ``connector.execute()``.

So this module is a thin orchestrator over machinery that already exists:

================  =========================================================
phase             existing core machinery
================  =========================================================
observe           :func:`app.connectors.preagg.mine` over the query log
decide            rank candidates × :class:`QueryEstimate` (``Connector.estimate``)
build             :func:`app.connectors.preagg.build_rollup` (TODO: wire R2)
maintain          incremental refresh via the connector (TODO)
rewrite           :func:`app.connectors.planner.route_to_rollup_shape`
================  =========================================================

What is REAL here today
-----------------------
* :meth:`Optimizer.observe` — mine the log into candidates (delegates).
* :meth:`Optimizer.decide` — a working, thresholded ranking by
  ``frequency × estimated-bytes-saved`` that emits an :class:`OptimizerPlan`.
* :meth:`Optimizer.detect_layout` — auto-detect a time partition key + cluster
  keys from candidate dimensions/filters and the query log.
* :meth:`Optimizer.rewrite` — a pass-through hook into ``route_to_rollup_shape``.

What is marked TODO (deeper bits)
---------------------------------
* :meth:`Optimizer.build` — materialize to **R2 Parquet** (today ``build_rollup``
  writes a local DuckDB file).
* :meth:`Optimizer.maintain` — incremental refresh (``WHERE ts > watermark``) +
  lambda freshness (serve-stale, async refresh).
* Sketch-based measures (HLL / t-digest) for non-additive grains.
* Partition pruning inside the rewrite (extend ``route_to_rollup_shape``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from app.config.nubi_toml import OptimizeTableConfig, ProjectConfig
from app.connectors.preagg import (
    RollupCandidate,
    get_registry,
    mine,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.connectors.plan import PhysicalPlan, QueryEstimate
    from app.connectors.preagg import RollupRegistry
    from app.connectors.query_log import QueryLog


# ===========================================================================
# Defaults / thresholds (posture C+A)
# ===========================================================================

#: Minimum ``score`` (= frequency × est-bytes-saved) for ``decide`` to propose a
#: rollup for auto-build.  Below this the cold/ad-hoc tail is left to pushdown
#: (§2: "only the hot tail gets rolled up").  Conservative default; tunable.
DEFAULT_BUILD_THRESHOLD: int = 1

#: Heuristic name fragments that mark a column as a probable time/partition key.
#: Auto partition picks a *single* time column (§4 day/month partitioning).
_TIME_NAME_HINTS: tuple[str, ...] = (
    "ts",
    "time",
    "timestamp",
    "date",
    "datetime",
    "created",
    "updated",
    "occurred",
    "event_time",
    "_at",
    "day",
    "month",
    "year",
)

#: Max cluster keys to auto-pick (§4 "high-selectivity filter columns").  More
#: than a handful of cluster keys stops helping; keep it small.
_MAX_CLUSTER_KEYS: int = 4


# ===========================================================================
# Plan value objects
# ===========================================================================


@dataclass(frozen=True)
class LayoutHint:
    """Auto-detected (or overridden) physical layout for one base table (§4).

    Attributes
    ----------
    table:
        The base fact table the layout applies to.
    partition_by:
        The chosen time partition column (``None`` when no time column was
        detected and none was declared).  The optimizer picks day/month
        granularity from this column.
    cluster_by:
        Ordered high-selectivity filter columns to cluster (sort) by.
    source:
        ``"override"`` when taken from ``nubi.toml``, ``"auto"`` when detected,
        ``"mixed"`` when partition came from one and cluster from the other.
    """

    table: str
    partition_by: str | None = None
    cluster_by: tuple[str, ...] = ()
    source: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "partition_by": self.partition_by,
            "cluster_by": list(self.cluster_by),
            "source": self.source,
        }


@dataclass(frozen=True)
class PlannedRollup:
    """A rollup the optimizer has decided to build, with its decision rationale.

    Attributes
    ----------
    candidate:
        The mined :class:`RollupCandidate` (table/dimensions/measures/filters).
    layout:
        The :class:`LayoutHint` (partition/cluster) the materialization should
        adopt.
    score:
        Decision score = ``frequency × estimated-bytes-saved`` (see
        :meth:`Optimizer.decide`).
    est_bytes_saved:
        Estimated bytes a single covered query avoids by reading the rollup
        instead of the base table (from ``Connector.estimate`` when available,
        else the log's scanned-bytes proxy).
    auto_build:
        ``True`` when the score cleared the build threshold AND the table's
        ``auto_optimize`` is on — i.e. the optimizer will build it without a
        human.  ``False`` rollups are *suggested* but not auto-built.
    reason:
        Human-readable rationale (observability).
    """

    candidate: RollupCandidate
    layout: LayoutHint
    score: int = 0
    est_bytes_saved: int = 0
    auto_build: bool = False
    reason: str = ""

    @property
    def table(self) -> str:
        return self.candidate.table

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "layout": self.layout.to_dict(),
            "score": self.score,
            "est_bytes_saved": self.est_bytes_saved,
            "auto_build": self.auto_build,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OptimizerPlan:
    """The output of :meth:`Optimizer.decide`: what to build, ranked.

    Attributes
    ----------
    rollups:
        Ranked :class:`PlannedRollup`s (highest score first).  Those with
        ``auto_build=True`` are the ones the optimizer will materialize now.
    threshold:
        The build threshold applied (for observability).
    """

    rollups: list[PlannedRollup] = field(default_factory=list)
    threshold: int = DEFAULT_BUILD_THRESHOLD

    @property
    def to_build(self) -> list[PlannedRollup]:
        """The subset that cleared the threshold and is auto-build-eligible."""
        return [r for r in self.rollups if r.auto_build]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollups": [r.to_dict() for r in self.rollups],
            "threshold": self.threshold,
            "to_build": [r.to_dict() for r in self.to_build],
        }


# ===========================================================================
# Layout auto-detection helpers
# ===========================================================================


def _looks_like_time_column(name: str) -> bool:
    """Heuristic: does *name* look like a time/partition column?

    Pure name-based today (no schema introspection).  ``detect_layout`` prefers
    a declared ``partition_by`` and only falls back to this heuristic.
    """
    n = name.strip().lower()
    if not n:
        return False
    for hint in _TIME_NAME_HINTS:
        # ``_at`` should match as a suffix; the rest as substrings/tokens.
        if hint.startswith("_"):
            if n.endswith(hint):
                return True
        elif re.search(rf"(^|_){re.escape(hint)}(_|$)", n) or hint in n:
            return True
    return False


def detect_partition_key(
    columns: Iterable[str], *, declared: str | None = None
) -> str | None:
    """Pick a single time partition column (§4).

    A declared key (from ``nubi.toml``) always wins.  Otherwise the first
    column that *looks like* a time column is chosen.  ``None`` when nothing
    qualifies (the table is then left unpartitioned).
    """
    if declared:
        return declared
    for col in columns:
        if _looks_like_time_column(col):
            return col
    return None


def detect_cluster_keys(
    filter_columns: Iterable[str],
    *,
    declared: Iterable[str] = (),
    exclude: Iterable[str] = (),
    limit: int = _MAX_CLUSTER_KEYS,
) -> tuple[str, ...]:
    """Pick high-selectivity cluster keys from observed WHERE columns (§4).

    Declared cluster keys (``nubi.toml``) take precedence and are kept in their
    declared order.  Remaining slots are filled from *filter_columns* (the
    columns queries actually filter on — a selectivity proxy), excluding the
    partition key and anything already declared.

    TODO: rank by *measured* selectivity (NDV / row estimates) instead of mere
    presence; integrate column stats from the Parquet layout.
    """
    excl = {c.lower() for c in exclude}
    out: list[str] = []
    seen: set[str] = set()

    for col in declared:
        key = col.lower()
        if key in excl or key in seen:
            continue
        out.append(col)
        seen.add(key)

    for col in filter_columns:
        if len(out) >= limit:
            break
        key = col.lower()
        if key in excl or key in seen:
            continue
        out.append(col)
        seen.add(key)

    return tuple(out[:limit])


# ===========================================================================
# Optimizer
# ===========================================================================


class Optimizer:
    """The self-managing managed-lakehouse optimizer (§1/§4).

    Lifecycle: ``observe(log) → decide(candidates, estimates) → build(plan) →
    maintain()``, with :meth:`rewrite` applied per-query at read time.

    The optimizer is automatic by default; per-table behaviour is governed by a
    :class:`ProjectConfig` (parsed ``nubi.toml``).  When no config is supplied an
    all-defaults config is used (auto-optimize on), so the optimizer works
    out-of-the-box and ``nubi.toml`` only ever *overrides*.

    Parameters
    ----------
    config:
        Per-project overrides.  Defaults to an all-defaults
        :class:`ProjectConfig`.
    registry:
        The :class:`RollupRegistry` to build into / route against.  Defaults to
        the process-wide singleton (``get_registry()``), so the optimizer and
        the live router share state.
    build_threshold:
        Minimum decision score for auto-build.
    """

    def __init__(
        self,
        config: ProjectConfig | None = None,
        *,
        registry: "RollupRegistry | None" = None,
        build_threshold: int = DEFAULT_BUILD_THRESHOLD,
    ) -> None:
        self.config = config or ProjectConfig()
        self.registry = registry or get_registry()
        self.build_threshold = build_threshold

    # ── OBSERVE ─────────────────────────────────────────────────────────────

    def observe(
        self, query_log: "QueryLog", *, min_hits: int = 3
    ) -> list[RollupCandidate]:
        """Mine *query_log* into ranked rollup candidates (§2 "observe").

        Delegates to the existing source-agnostic miner
        (:func:`app.connectors.preagg.mine`); the miner already clusters
        compatible aggregation shapes and ranks them by
        ``frequency × scanned-bytes``.  Returned candidates are the input to
        :meth:`decide`.
        """
        return mine(query_log, min_hits=min_hits)

    # ── DECIDE ──────────────────────────────────────────────────────────────

    def decide(
        self,
        candidates: list[RollupCandidate],
        estimates: dict[str, "QueryEstimate"] | None = None,
        *,
        threshold: int | None = None,
    ) -> OptimizerPlan:
        """Rank candidates and decide which to auto-build (§2 "decide", §4).

        Ranking key (the real, working bit): ``frequency × estimated-bytes-saved``
        where

        * ``frequency`` = ``candidate.sample_count`` (log hits), and
        * ``estimated-bytes-saved`` = the base-query scan cost we avoid by
          reading the rollup.  When a :class:`QueryEstimate` is supplied for the
          candidate (keyed by ``candidate.cluster_key`` or ``candidate.table``)
          and carries ``est_bytes_scanned``, that authoritative figure is used
          — for a warehouse this is the real $ a base query costs, so we build
          rollups exactly where pushdown is expensive (§2).  Otherwise we fall
          back to the miner's ``est_bytes`` (summed scanned bytes from the log).

        A candidate is marked ``auto_build`` when its score clears *threshold*
        **and** the table's ``auto_optimize`` is on in the project config
        (posture C+A: automatic, but the per-table master switch can pin it).
        Everything else is *suggested* (returned, not built) — the cold/ad-hoc
        tail stays on pushdown.

        TODO (deeper): cost-model the *maintenance* cost (refresh frequency ×
        refresh scan) against savings; dedupe near-identical grains; respect a
        global byte/$ budget.
        """
        thr = self.build_threshold if threshold is None else threshold
        estimates = estimates or {}

        planned: list[PlannedRollup] = []
        for cand in candidates:
            est = estimates.get(cand.cluster_key) or estimates.get(cand.table)
            est_bytes_saved = self._bytes_saved_for(cand, est)
            frequency = max(cand.sample_count, 0)
            score = frequency * est_bytes_saved

            table_cfg = self.config.for_table(cand.table)
            layout = self.detect_layout(cand, table_cfg)

            auto = score >= thr and table_cfg.auto_optimize_enabled
            reason = self._decision_reason(
                score=score,
                threshold=thr,
                auto=auto,
                table_cfg=table_cfg,
                used_estimate=est is not None,
            )
            planned.append(
                PlannedRollup(
                    candidate=cand,
                    layout=layout,
                    score=score,
                    est_bytes_saved=est_bytes_saved,
                    auto_build=auto,
                    reason=reason,
                )
            )

        # Rank by score; tie-break on frequency so a busy-but-cheap pattern
        # still sorts ahead of a never-seen one (mirrors the miner).
        planned.sort(
            key=lambda p: (p.score, p.candidate.sample_count), reverse=True
        )
        return OptimizerPlan(rollups=planned, threshold=thr)

    @staticmethod
    def _bytes_saved_for(
        candidate: RollupCandidate, estimate: "QueryEstimate | None"
    ) -> int:
        """Estimated bytes one covered query avoids by reading the rollup.

        Prefer the authoritative ``Connector.estimate`` figure (exact for a
        BigQuery dry-run) when present; otherwise use the miner's log-derived
        ``est_bytes`` proxy.
        """
        if estimate is not None and getattr(estimate, "est_bytes_scanned", None):
            return int(estimate.est_bytes_scanned)
        return int(candidate.est_bytes)

    @staticmethod
    def _decision_reason(
        *,
        score: int,
        threshold: int,
        auto: bool,
        table_cfg: OptimizeTableConfig,
        used_estimate: bool,
    ) -> str:
        src = "Connector.estimate" if used_estimate else "log scan-bytes"
        if auto:
            return (
                f"auto-build: score {score} >= threshold {threshold} "
                f"(via {src}); auto_optimize on"
            )
        if not table_cfg.auto_optimize_enabled:
            return (
                f"suggested only: auto_optimize off for {table_cfg.table!r} "
                f"(score {score}, via {src})"
            )
        return (
            f"suggested only: score {score} < threshold {threshold} "
            f"(via {src}); cold/ad-hoc tail stays on pushdown"
        )

    # ── LAYOUT (auto partition / cluster, §4) ───────────────────────────────

    def detect_layout(
        self,
        candidate: RollupCandidate,
        table_cfg: OptimizeTableConfig | None = None,
    ) -> LayoutHint:
        """Auto-detect partition + cluster keys for *candidate* (§4).

        * **Partition** — a single time column.  A declared ``partition_by``
          (``nubi.toml``) wins; otherwise the first dimension/filter that looks
          like a time column.
        * **Cluster** — high-selectivity filter columns.  Declared
          ``cluster_by`` wins (in order); remaining slots filled from the
          candidate's observed WHERE columns, excluding the partition key.

        The ``source`` field records where each half came from for
        observability.
        """
        if table_cfg is None:
            table_cfg = self.config.for_table(candidate.table)

        # Columns to consider for the time partition: dimensions first (a
        # time grain is usually grouped on), then filter columns.
        candidate_cols = list(candidate.dimensions) + list(candidate.filters)
        partition = detect_partition_key(
            candidate_cols, declared=table_cfg.partition_by
        )

        cluster = detect_cluster_keys(
            candidate.filters,
            declared=table_cfg.cluster_by,
            exclude=(partition,) if partition else (),
        )

        part_overridden = bool(table_cfg.partition_by)
        cluster_overridden = bool(table_cfg.cluster_by)
        if part_overridden and cluster_overridden:
            source = "override"
        elif part_overridden or cluster_overridden:
            source = "mixed"
        else:
            source = "auto"

        return LayoutHint(
            table=candidate.table,
            partition_by=partition,
            cluster_by=cluster,
            source=source,
        )

    # ── BUILD ───────────────────────────────────────────────────────────────

    def build(
        self, plan: OptimizerPlan, *, rls_keys_by_table: dict[str, list[str]] | None = None
    ) -> list[Any]:
        """Materialize the auto-build rollups in *plan*.

        TODO (deeper): this is the §1.2 "materialization always lands in the
        lakehouse" step.  Today :func:`app.connectors.preagg.build_rollup` writes
        a **local DuckDB** file; the managed-lakehouse target is **Parquet in
        R2** (sorted by the partition key, clustered by the cluster keys, zstd,
        column stats) so httpfs range-requests prune to MBs (§3).

        Until the R2 write path lands, this method is a deliberate no-op stub
        that returns an empty list rather than silently building local files
        with the wrong physical layout.  The decision plan it consumes is fully
        formed, so wiring the builder is a localized change.

        ``rls_keys_by_table`` carries the RLS-key columns that MUST stay in each
        rollup's grain (§ invariants) — passed straight through to the builder
        once wired so per-tenant filtering survives the rewrite.
        """
        # Intentionally not calling build_rollup yet — see docstring (wrong
        # physical target).  Returning [] keeps callers safe and tests honest.
        _ = (plan, rls_keys_by_table)
        return []

    # ── MAINTAIN ────────────────────────────────────────────────────────────

    def maintain(self) -> None:
        """Incremental refresh hook for built rollups (§2 "maintain", §3).

        TODO (deeper): for each built rollup, run an incremental refresh through
        the source connector (``WHERE ts > watermark`` — ``MaterializedConfig``
        already carries incremental/watermark) and apply **lambda freshness**:
        serve the stale rollup immediately and refresh asynchronously so
        dashboards never block.  Honour each table's ``freshness`` window from
        ``nubi.toml``.

        This is the ONLY per-connector part of pre-agg (§1.3); everything else
        is connector-agnostic.  Stubbed until the refresh scheduler integration
        lands.
        """
        # No-op until the incremental refresh path is wired.  Listed here so the
        # lifecycle surface is complete and callers can schedule it.
        return None

    # ── REWRITE (read-time hook into route_to_rollup_shape) ─────────────────

    def rewrite(self, plan: "PhysicalPlan") -> "RollupRouteResultLike":
        """Route *plan* to a built rollup when SOUND (§1.1 hook).

        Thin pass-through to the existing, connector-agnostic
        :func:`app.connectors.planner.route_to_rollup_shape`, which performs the
        only sound rewrite (group-by ⊆ rollup dims, every measure re-aggregable,
        every filter column present, RLS preserved).  Uncovered queries fall
        back unchanged to pushdown (§2 "rollup-or-pushdown fallback").

        Kept as a method on the optimizer so callers have a single object that
        owns observe/decide/maintain/rewrite, and so a future partition-pruning
        extension (§4 "extend route_to_rollup_shape to prune partitions") has an
        obvious home.

        TODO (deeper): after routing, prune partitions using the
        :class:`LayoutHint` partition key so a filtered query reads only the
        relevant day/month files.
        """
        # Imported lazily to avoid a heavy sqlglot import at module load and to
        # keep the dependency direction one-way (planner does not import us).
        from app.connectors.planner import (  # noqa: PLC0415
            route_to_rollup_shape,
        )

        result = route_to_rollup_shape(plan, self.registry)
        if result.routed and result.rollup_id:
            # Mirror the live router's HIT accounting so optimizer-driven reads
            # show up in rollup usage stats.
            self.registry.record_hit(result.rollup_id)
        return result


# ``route_to_rollup_shape`` returns a ``RollupRouteResult``; we only depend on
# its ``.routed`` / ``.rollup_id`` / ``.plan`` attributes, so we type the return
# of :meth:`Optimizer.rewrite` structurally to avoid importing the planner at
# module import time.
if TYPE_CHECKING:  # pragma: no cover
    from app.connectors.planner import RollupRouteResult as RollupRouteResultLike
else:  # pragma: no cover
    RollupRouteResultLike = Any
