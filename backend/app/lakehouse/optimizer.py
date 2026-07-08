"""The pre-aggregation optimizer skeleton.

One self-managing optimizer owns the mapping from the *logical* tables you query
to the *physical* rewrite it can prove sound.  It is automatic by default and
customizable per table via ``nubi.toml``.

The optimizer is **application logic on DuckDB, not a new engine**.  Pre-agg
splits into three parts and only the third is per-connector:

1. **Rewrite/routing** — :func:`app.connectors.planner.route_to_rollup_shape`
   (sqlglot, connector-agnostic).
2. **Materialization** — computed ONCE, in-process (pushdown / local DuckDB),
   as a compact rollup artifact — NOT a durable, Nubi-hosted warehouse dataset.
   Repeat reads are served by the content-hashed edge cache
   (:mod:`app.connectors.cache`), same as any other query result.
3. **Refresh** — the only per-connector bit; run the aggregate via
   ``connector.execute()``.

So this module is a thin orchestrator over machinery that already exists:

================  =========================================================
phase             existing core machinery
================  =========================================================
observe           :func:`app.connectors.preagg.mine` over the query log
decide            rank candidates × :class:`QueryEstimate` (``Connector.estimate``)
build             :func:`app.connectors.preagg.build_rollup` (local DuckDB, in-process)
maintain          full rebuild via ``build_rollup``; incremental refresh future
rewrite           :func:`app.connectors.planner.route_to_rollup_shape`
================  =========================================================

What is REAL here today
-----------------------
* :meth:`Optimizer.observe` — mine the log into candidates (delegates).
* :meth:`Optimizer.decide` — a working, thresholded ranking by
  ``frequency × estimated-bytes-saved`` that emits an :class:`OptimizerPlan`.
* :meth:`Optimizer.detect_layout` — auto-detect a time partition key + cluster
  keys from candidate dimensions/filters and the query log.
* :meth:`Optimizer.build` — materialize auto-build rollups via
  :func:`app.connectors.preagg.build_rollup`; returns list of
  :class:`~app.connectors.preagg.BuiltRollup`.
* :meth:`Optimizer.maintain` — full-rebuild refresh of all registered rollups.
* :meth:`Optimizer.rewrite` — a pass-through hook into ``route_to_rollup_shape``.

What is REAL here today (all phases)
-------------------------------------
* :meth:`Optimizer.build` — calls :func:`app.connectors.preagg.build_rollup` for
  each auto-build rollup in the plan; writes a local DuckDB file used purely as
  an in-process acceleration structure for THIS deployment (not a hosted /
  billed storage product).  Returns a list of
  :class:`~app.connectors.preagg.BuiltRollup`.
* :meth:`Optimizer.maintain` — full rebuild of every registered rollup (minimum
  viable; always correct).  Returns a summary dict.  Future upgrade: incremental
  refresh (``WHERE ts > watermark`` via ``MaterializedConfig``) + lambda freshness.

What is marked TODO (deeper bits)
---------------------------------
* TODO(warehouse-side materialization): today's rollup is a local,
  process-local DuckDB file — an acceleration structure, not a durable
  Nubi-hosted dataset. A future connector-native option (materializing the
  rollup INTO a customer's own warehouse via pushdown, e.g. a Snowflake/
  BigQuery table THEY own) is an optional seam here, not a Nubi storage
  product.
* Incremental refresh in :meth:`Optimizer.maintain` (documented inside the method).
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
    """The self-managing pre-aggregation optimizer.

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
        self,
        plan: OptimizerPlan,
        *,
        rls_keys_by_table: dict[str, list[str]] | None = None,
        source_database: str | None = None,
        org_id: str | None = None,
    ) -> list[Any]:
        """Materialize the auto-build rollups in *plan*.

        For each :class:`PlannedRollup` in ``plan.to_build`` (those that cleared
        the auto-build threshold), this method:

        1. Reconstructs a :class:`~app.connectors.preagg.RollupCandidate` from
           the ``PlannedRollup``.
        2. Calls :func:`app.connectors.preagg.build_rollup` to materialize the
           aggregate into a **local DuckDB** file (the current write target).
        3. Registers the resulting :class:`~app.connectors.preagg.BuiltRollup`
           in the shared registry so the router picks it up immediately.

        Returns a list of :class:`~app.connectors.preagg.BuiltRollup` objects —
        one per successfully built rollup.  Failures per-rollup are logged and
        skipped so one bad candidate cannot block the rest.

        TODO(warehouse-side materialization) — optional future seam
        -------------------------------------------------------------
        ``build_rollup`` writes a **local DuckDB** file: a process-local
        acceleration structure for THIS deployment, not a durable, Nubi-hosted
        storage product.  Repeat reads of the same rollup shape are served by
        the content-hashed edge cache, same as any other query.  A possible
        future upgrade is a connector-native materialization that writes the
        rollup INTO a customer's OWN warehouse (pushdown — e.g. a table they
        own in Snowflake/BigQuery) rather than any Nubi-operated storage;
        the rest of the orchestration would stay identical.

        Parameters
        ----------
        plan:
            The :class:`OptimizerPlan` from :meth:`decide` — only the
            ``to_build`` subset (``auto_build=True``) is materialized.
        rls_keys_by_table:
            Per-table RLS-key columns that MUST be kept in each rollup's grain
            (§ invariants — per-tenant filtering via ``WHERE <key> = <claim>``
            survives the rewrite only when the key is physically in the rollup).
            When ``None`` no extra RLS keys are injected (conservative; the
            rollup is still correct but lacks the key for per-tenant filtering
            via the rollup path, so such queries fall back to pushdown).
        source_database:
            Absolute path to the DuckDB file holding the base fact tables.
            ``None`` is in-memory (suitable for tests).
        org_id:
            Organisation that owns these rollups.  Stored on each
            :class:`~app.connectors.preagg.BuiltRollup` so the registry enforces
            org-scoped candidate filtering: a rollup built for org A is never
            routed to org B's queries.
        """
        import logging  # noqa: PLC0415

        from app.connectors.preagg import RollupCandidate, build_rollup  # noqa: PLC0415

        log = logging.getLogger(__name__)
        rls_keys_by_table = rls_keys_by_table or {}
        built: list[Any] = []

        for planned in plan.to_build:
            candidate = RollupCandidate(
                table=planned.candidate.table,
                dimensions=list(planned.candidate.dimensions),
                measures=list(planned.candidate.measures),
                filters=list(planned.candidate.filters),
                score=planned.candidate.score,
                sample_count=planned.candidate.sample_count,
                est_bytes=planned.candidate.est_bytes,
                cluster_key=planned.candidate.cluster_key,
            )
            rls_keys = list(rls_keys_by_table.get(planned.table, []))
            try:
                rollup = build_rollup(
                    candidate,
                    rls_keys=rls_keys,
                    source_database=source_database,
                    registry=self.registry,
                    register_query=True,
                    org_id=org_id,
                )
                built.append(rollup)
                log.info(
                    "optimizer.build: materialized rollup %s for %s (score=%d, auto=%s)",
                    rollup.rollup_id,
                    planned.table,
                    planned.score,
                    planned.auto_build,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "optimizer.build: failed to materialize rollup for %s: %s",
                    planned.candidate.cluster_key,
                    exc,
                )

        return built

    # ── MAINTAIN ────────────────────────────────────────────────────────────

    def maintain(
        self,
        *,
        source_database: str | None = None,
    ) -> dict[str, Any]:
        """Refresh all registered rollups (§2 "maintain", §3).

        Iterates every :class:`~app.connectors.preagg.BuiltRollup` currently in
        the registry and re-materializes it from its source table.  This is the
        minimum viable **full rebuild** strategy — correct for all rollup types
        and sufficient until incremental refresh lands.

        Incremental refresh (future upgrade)
        -------------------------------------
        The spec describes an incremental path: ``WHERE ts > watermark`` using
        the ``MaterializedConfig`` incremental/watermark field, with **lambda
        freshness** (serve stale, refresh async).  That path is the ONLY
        per-connector part of pre-agg (§1.3).  Once the refresh scheduler
        integration lands, add:

        1. Check each rollup's ``MaterializedConfig.incremental`` flag.
        2. When ``True``, run ``SELECT ... WHERE <time_col> > watermark`` and
           UNION/UPSERT into the existing rollup file.
        3. Advance the watermark on success.
        4. Otherwise (``False`` or no watermark), do a full rebuild (current
           behaviour).

        Parameters
        ----------
        source_database:
            Absolute path to the DuckDB file holding the base fact tables.
            ``None`` means in-memory (test usage).

        Returns
        -------
        dict
            ``{refreshed: int, failed: int, skipped: int, errors: list[str]}``
        """
        import logging  # noqa: PLC0415

        from app.connectors.preagg import RollupCandidate, build_rollup  # noqa: PLC0415

        log = logging.getLogger(__name__)
        refreshed = 0
        failed = 0
        skipped = 0
        errors: list[str] = []

        for rollup in self.registry.all_rollups():
            # Reconstruct a minimal candidate from the registered rollup's shape.
            candidate = RollupCandidate(
                table=rollup.source_table,
                dimensions=list(rollup.dimensions),
                measures=list(rollup.measures),
            )
            db = source_database or rollup.database
            if db is None:
                log.debug(
                    "optimizer.maintain: skipping %s (no source_database)",
                    rollup.rollup_id,
                )
                skipped += 1
                continue
            try:
                build_rollup(
                    candidate,
                    rls_keys=list(rollup.rls_keys),
                    source_database=db,
                    rollup_id=rollup.rollup_id,  # keep the same id → overwrites in place
                    registry=self.registry,
                    register_query=False,  # already registered
                    org_id=rollup.org_id,
                )
                refreshed += 1
                log.info(
                    "optimizer.maintain: refreshed rollup %s for table %s",
                    rollup.rollup_id,
                    rollup.source_table,
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"Failed to refresh rollup {rollup.rollup_id!r}: {exc}"
                log.warning("optimizer.maintain: %s", msg)
                errors.append(msg)
                failed += 1

        return {
            "refreshed": refreshed,
            "failed": failed,
            "skipped": skipped,
            "errors": errors,
        }

    # ── REWRITE (read-time hook into route_to_rollup_shape) ─────────────────

    def rewrite(
        self, plan: "PhysicalPlan", *, org_id: str | None = None
    ) -> "RollupRouteResultLike":
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

        Parameters
        ----------
        plan:
            The ``PhysicalPlan`` to (conditionally) rewrite.
        org_id:
            The caller's resolved org.  TENANT-ISOLATION: forwarded to
            ``route_to_rollup_shape`` so only rollups built for this org are
            candidates.  When ``None`` the router refuses to route (no rollup
            is ever served to an unscoped caller).

        TODO (deeper): after routing, prune partitions using the
        :class:`LayoutHint` partition key so a filtered query reads only the
        relevant day/month files.
        """
        # Imported lazily to avoid a heavy sqlglot import at module load and to
        # keep the dependency direction one-way (planner does not import us).
        from app.connectors.planner import (  # noqa: PLC0415
            route_to_rollup_shape,
        )

        result = route_to_rollup_shape(plan, self.registry, org_id=org_id)
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
