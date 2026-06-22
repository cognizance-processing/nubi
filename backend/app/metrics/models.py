"""Metric definition + query contract (the authoritative shared types).

These immutable dataclasses are the contract that the registry (registry.py),
the compiler (compile.py), the routes (routes/metrics.py), and the agent-facing
surfaces (routes/ai.py, MCP) all consume. They mirror the style of
``app/queries/registry.py`` (frozen dataclasses, portable type vocab).

Serialization: ``to_dict``/``from_dict`` round-trip a definition to/from JSONB
for the ``metrics`` table (migration 0008). Keep these in sync with that schema.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# ── Vocabularies ────────────────────────────────────────────────────────────

AggFunc = Literal["sum", "count", "count_distinct", "min", "max", "avg"]
MeasureType = Literal["additive", "semi_additive", "non_additive"]
TimeGrain = Literal["hour", "day", "week", "month", "quarter", "year"]
DimType = Literal["text", "number", "bool", "date", "timestamp"]
FilterOp = Literal["=", "!=", "<", "<=", ">", ">=", "in", "not_in"]

# Time-intelligence transforms requestable per query (require a time_grain).
#   prior_period / pop_abs / pop_pct  — vs the immediately preceding bucket
#   prior_year / yoy_abs / yoy_pct    — vs the same bucket one year earlier
#   ytd / qtd / mtd                   — running total within year / quarter / month
#   rolling_sum / rolling_avg         — trailing window of ``periods`` buckets
TimeCompareKind = Literal[
    "prior_period",
    "pop_abs",
    "pop_pct",
    "prior_year",
    "yoy_abs",
    "yoy_pct",
    "ytd",
    "qtd",
    "mtd",
    "rolling_sum",
    "rolling_avg",
]

# How many prior buckets one calendar year spans, per grain — used to offset the
# LAG window for year-over-year comparisons on an evenly-bucketed time series.
YEAR_LAG_BY_GRAIN: dict[str, int] = {
    "month": 12,
    "quarter": 4,
    "week": 52,
    "day": 365,
    "year": 1,
    "hour": 8760,
}

ALL_TIME_GRAINS: tuple[TimeGrain, ...] = (
    "hour",
    "day",
    "week",
    "month",
    "quarter",
    "year",
)


# ── Definition pieces ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Measure:
    """The quantity a metric measures, e.g. ``revenue = SUM(amount)``.

    Attributes
    ----------
    name:   output column name (e.g. ``"revenue"``).
    agg:    aggregation function applied to ``expr``.
    expr:   the column or SQL expression aggregated. For ``count`` use ``"*"``.
    type:   additivity — ``additive`` re-aggregates by plain SUM across any grain;
            ``semi_additive`` (e.g. balances) and ``non_additive`` (e.g. distinct
            counts, percentiles) need sketches/care when composing grains.
    format: optional display hint (``"currency"``, ``"percent"``, ``"number"``).
    """

    name: str
    agg: AggFunc = "sum"
    expr: str = "*"
    type: MeasureType = "additive"
    format: str | None = None


@dataclass(frozen=True)
class DerivedMeasure:
    """A measure computed from OTHER measures, e.g. ``margin = profit / revenue``.

    A derived measure is post-aggregation arithmetic over the metric's base
    measures (the primary measure + ``extra_measures``), referenced by name. It
    is NOT aggregated itself, so it composes correctly over any grain a rollup
    serves (the base measures re-aggregate; the ratio is recomputed from them).

    Attributes
    ----------
    name:    output column name (e.g. ``"margin"``).
    formula: an arithmetic expression over base-measure names and numeric
             literals using ``+ - * / ( )`` only, e.g. ``"profit / revenue"`` or
             ``"(revenue - cost) / revenue"``. Division is guarded with
             ``NULLIF(<denominator>, 0)`` by the compiler. Governance rejects any
             identifier that is not a declared base measure.
    format:  optional display hint (``"percent"``, ``"currency"``, ``"number"``).
    """

    name: str
    formula: str = ""
    format: str | None = None


@dataclass(frozen=True)
class Dimension:
    """An ALLOWED grouping column. A metric query may only group by declared dims.

    Attributes
    ----------
    name: the dimension name a caller references (and the output column name).
    expr: optional SQL expression; defaults to ``name`` (a bare column).
    type: portable type of the dimension's values.
    """

    name: str
    expr: str | None = None
    type: DimType = "text"

    def sql_expr(self) -> str:
        """The SQL expression for this dimension (``expr`` or the bare column)."""
        return self.expr if self.expr else self.name


@dataclass(frozen=True)
class TimeDimension:
    """The metric's time column + the grains it can be bucketed to.

    Attributes
    ----------
    column:        the timestamp/date column to bucket.
    grains:        the allowed ``date_trunc`` grains.
    default_grain: grain used when a query omits ``time_grain``.
    """

    column: str
    grains: tuple[TimeGrain, ...] = ALL_TIME_GRAINS
    default_grain: TimeGrain = "day"


@dataclass(frozen=True)
class MetricDefinition:
    """A governed metric definition, compiled to SQL on demand.

    Exactly ONE source must be set: ``base_table`` (a physical table) OR
    ``base_sql`` (a trusted SELECT used as a subquery). ``default_filters`` are
    author-governed WHERE fragments inlined verbatim (trusted — never user input);
    user-supplied filtering goes through :class:`MetricFilter` as bound params.
    ``rls_keys`` MUST remain in the grain so the planner's RLS predicate lands on
    a real column.
    """

    id: str
    name: str
    measure: Measure
    base_table: str | None = None
    base_sql: str | None = None
    datastore_id: str | None = None
    dimensions: tuple[Dimension, ...] = ()
    time_dimension: TimeDimension | None = None
    default_filters: tuple[str, ...] = ()
    rls_keys: tuple[str, ...] = ()
    description: str = ""
    owner: str | None = None
    required_scope: str | None = None
    # Additional measures requestable at the same grain (v1 callers usually use
    # the single primary ``measure``; this leaves room without a schema change).
    extra_measures: tuple[Measure, ...] = ()
    # Ratio / derived measures computed from the base measures (post-aggregation).
    derived_measures: tuple[DerivedMeasure, ...] = ()

    def dimension(self, name: str) -> Dimension | None:
        """Return the allowed :class:`Dimension` named *name*, or ``None``."""
        for d in self.dimensions:
            if d.name == name:
                return d
        return None

    def measures(self) -> tuple[Measure, ...]:
        """The primary measure followed by any extra measures."""
        return (self.measure, *self.extra_measures)

    def measure_names(self) -> tuple[str, ...]:
        """Names of all base measures (primary + extras) — the derived/compare
        vocabulary."""
        return tuple(m.name for m in self.measures())

    def derived_measure(self, name: str) -> DerivedMeasure | None:
        """Return the :class:`DerivedMeasure` named *name*, or ``None``."""
        for dm in self.derived_measures:
            if dm.name == name:
                return dm
        return None

    # ── serialization (JSONB <-> definition) ────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form for the ``metrics.definition`` JSONB column."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricDefinition:
        """Rebuild a definition from its serialized JSONB form."""
        measure = _measure_from(data.get("measure") or {})
        dims = tuple(_dimension_from(d) for d in (data.get("dimensions") or ()))
        td_raw = data.get("time_dimension")
        time_dim = _time_dimension_from(td_raw) if td_raw else None
        extra = tuple(_measure_from(m) for m in (data.get("extra_measures") or ()))
        derived = tuple(
            _derived_measure_from(m) for m in (data.get("derived_measures") or ())
        )
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            measure=measure,
            base_table=data.get("base_table"),
            base_sql=data.get("base_sql"),
            datastore_id=data.get("datastore_id"),
            dimensions=dims,
            time_dimension=time_dim,
            default_filters=tuple(data.get("default_filters") or ()),
            rls_keys=tuple(data.get("rls_keys") or ()),
            description=str(data.get("description") or ""),
            owner=data.get("owner"),
            required_scope=data.get("required_scope"),
            extra_measures=extra,
            derived_measures=derived,
        )


# ── Query (request) ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricFilter:
    """A user-supplied filter on an ALLOWED dimension or the time column.

    ``value`` is bound as a query parameter (never concatenated into SQL). For
    ``in``/``not_in`` the value is a list.
    """

    field: str
    op: FilterOp = "="
    value: Any = None


@dataclass(frozen=True)
class TimeComparison:
    """A requested time-intelligence transform of a base measure.

    Requires the query to carry a ``time_grain`` (so there is an ordered time
    series to window over). Computed as window functions over the base time
    series, partitioned by the query's non-time dimensions.

    Attributes
    ----------
    measure: the base measure to transform (must be a declared base measure).
    kind:    the transform (see :data:`TimeCompareKind`).
    periods: trailing window size for ``rolling_*`` / offset for ``prior_period``
             (defaults to 1; ignored by ``yoy_*`` / ``ytd`` / ``qtd`` / ``mtd``).
    name:    output column name (defaults to ``"<measure>_<kind>"``).
    """

    measure: str
    kind: TimeCompareKind
    periods: int = 1
    name: str | None = None

    def out_name(self) -> str:
        return self.name or f"{self.measure}_{self.kind}"


@dataclass(frozen=True)
class TopN:
    """A dynamic top-N restriction: keep the *n* top *dimension* members by *measure*.

    Ranking is by the named measure (a base or derived measure) over the whole
    result. When ``other`` is set, members beyond the top *n* are collapsed into a
    single bucket labelled ``other_label`` (the base measures are re-aggregated;
    derived measures recomputed from them). With a ``time_grain`` the top-*n*
    members are chosen by their grand total and their full time series is kept.

    Attributes
    ----------
    dimension:   the dimension to rank + restrict (must be a queried dimension).
    measure:     the measure to rank by (base or derived; defaults to primary).
    n:           how many members to keep (dynamic — supplied per query).
    order:       ``"desc"`` (top) or ``"asc"`` (bottom).
    other:       roll the remaining members into one ``other_label`` bucket.
    other_label: the label for the rolled-up bucket (default ``"Other"``).
    """

    dimension: str
    n: int
    measure: str | None = None
    order: str = "desc"
    other: bool = False
    other_label: str = "Other"


@dataclass(frozen=True)
class MetricQuery:
    """A request against a metric: group by *dimensions* at *time_grain*, filtered.

    ``dimensions`` must be a subset of the metric's allowed dimensions;
    ``time_grain`` requires the metric to declare a ``time_dimension`` and must be
    one of its allowed grains; ``filters`` reference allowed dims / the time col.
    ``order_by`` entries are ``(field, "asc"|"desc")``. ``time_comparisons`` and
    ``top_n`` add the time-intelligence / dynamic-top-N transforms (compiled as an
    outer layer over the base aggregation so pre-agg rollups still serve them).
    """

    metric_id: str
    dimensions: tuple[str, ...] = ()
    time_grain: TimeGrain | None = None
    filters: tuple[MetricFilter, ...] = ()
    order_by: tuple[tuple[str, str], ...] = ()
    limit: int | None = None
    time_comparisons: tuple[TimeComparison, ...] = ()
    top_n: TopN | None = None

    def has_transforms(self) -> bool:
        """True when the query needs the outer transform layer (derived measures
        are definition-level and handled separately)."""
        return bool(self.time_comparisons) or self.top_n is not None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricQuery:
        """Build a MetricQuery from a request body dict (tolerant of lists)."""
        filters = tuple(
            MetricFilter(
                field=str(f["field"]),
                op=f.get("op", "="),
                value=f.get("value"),
            )
            for f in (data.get("filters") or ())
        )
        order_by = tuple(
            (str(o[0]), str(o[1]).lower()) if isinstance(o, (list, tuple))
            else (str(o.get("field")), str(o.get("dir", "asc")).lower())
            for o in (data.get("order_by") or ())
        )
        comparisons = tuple(
            TimeComparison(
                measure=str(c["measure"]),
                kind=c["kind"],
                periods=int(c.get("periods", 1) or 1),
                name=c.get("name"),
            )
            for c in (data.get("time_comparisons") or ())
        )
        top_n_raw = data.get("top_n")
        top_n = (
            TopN(
                dimension=str(top_n_raw["dimension"]),
                n=int(top_n_raw["n"]),
                measure=top_n_raw.get("measure"),
                order=str(top_n_raw.get("order", "desc")).lower(),
                other=bool(top_n_raw.get("other", False)),
                other_label=str(top_n_raw.get("other_label", "Other")),
            )
            if isinstance(top_n_raw, dict)
            else None
        )
        return cls(
            metric_id=str(data["metric_id"]),
            dimensions=tuple(str(d) for d in (data.get("dimensions") or ())),
            time_grain=data.get("time_grain"),
            filters=filters,
            order_by=order_by,
            limit=data.get("limit"),
            time_comparisons=comparisons,
            top_n=top_n,
        )


# ── helpers ─────────────────────────────────────────────────────────────────


def _measure_from(d: dict[str, Any]) -> Measure:
    return Measure(
        name=str(d.get("name") or "value"),
        agg=d.get("agg", "sum"),
        expr=str(d.get("expr") or "*"),
        type=d.get("type", "additive"),
        format=d.get("format"),
    )


def _derived_measure_from(d: dict[str, Any]) -> DerivedMeasure:
    return DerivedMeasure(
        name=str(d.get("name") or "derived"),
        formula=str(d.get("formula") or ""),
        format=d.get("format"),
    )


def _dimension_from(d: dict[str, Any]) -> Dimension:
    return Dimension(
        name=str(d["name"]),
        expr=d.get("expr"),
        type=d.get("type", "text"),
    )


def _time_dimension_from(d: dict[str, Any]) -> TimeDimension:
    grains = tuple(d.get("grains") or ALL_TIME_GRAINS)
    return TimeDimension(
        column=str(d["column"]),
        grains=grains,  # type: ignore[arg-type]
        default_grain=d.get("default_grain", "day"),
    )


class MetricError(Exception):
    """Raised when a metric definition or query violates the governance contract.

    Carries a machine ``code`` and a human ``message`` so routes can map it to a
    4xx with a structured body (mirrors the dashboard validate errors).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Re-export field for downstream convenience (some callers build tuples).
__all__ = [
    "AggFunc",
    "MeasureType",
    "TimeGrain",
    "TimeCompareKind",
    "YEAR_LAG_BY_GRAIN",
    "DimType",
    "FilterOp",
    "ALL_TIME_GRAINS",
    "Measure",
    "DerivedMeasure",
    "Dimension",
    "TimeDimension",
    "MetricDefinition",
    "MetricFilter",
    "TimeComparison",
    "TopN",
    "MetricQuery",
    "MetricError",
    "field",
]
