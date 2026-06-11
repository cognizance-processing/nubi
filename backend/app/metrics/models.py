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

    def dimension(self, name: str) -> Dimension | None:
        """Return the allowed :class:`Dimension` named *name*, or ``None``."""
        for d in self.dimensions:
            if d.name == name:
                return d
        return None

    def measures(self) -> tuple[Measure, ...]:
        """The primary measure followed by any extra measures."""
        return (self.measure, *self.extra_measures)

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
class MetricQuery:
    """A request against a metric: group by *dimensions* at *time_grain*, filtered.

    ``dimensions`` must be a subset of the metric's allowed dimensions;
    ``time_grain`` requires the metric to declare a ``time_dimension`` and must be
    one of its allowed grains; ``filters`` reference allowed dims / the time col.
    ``order_by`` entries are ``(field, "asc"|"desc")``.
    """

    metric_id: str
    dimensions: tuple[str, ...] = ()
    time_grain: TimeGrain | None = None
    filters: tuple[MetricFilter, ...] = ()
    order_by: tuple[tuple[str, str], ...] = ()
    limit: int | None = None

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
        return cls(
            metric_id=str(data["metric_id"]),
            dimensions=tuple(str(d) for d in (data.get("dimensions") or ())),
            time_grain=data.get("time_grain"),
            filters=filters,
            order_by=order_by,
            limit=data.get("limit"),
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
    "DimType",
    "FilterOp",
    "ALL_TIME_GRAINS",
    "Measure",
    "Dimension",
    "TimeDimension",
    "MetricDefinition",
    "MetricFilter",
    "MetricQuery",
    "MetricError",
    "field",
]
