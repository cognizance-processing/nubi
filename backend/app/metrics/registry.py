"""Metric registry — in-process catalogue of governed metric definitions.

This mirrors ``app/queries/registry.py`` verbatim in shape: a plain dict-backed
singleton with ``register`` / ``unregister`` / ``get`` / ``all``, a module-level
``get_metric_registry()`` accessor, a ``reset_for_tests()`` helper, and a
DB-backed loader that hydrates the registry at startup.

Source of metrics
-----------------
Metrics are SOURCED from queries-with-``config.metric`` (the query/metric
unification): a ``queries`` row whose ``config`` carries a ``metric`` block IS a
governed metric, keyed by ``config.metric.slug``. ``load_metrics_from_queries``
is the startup loader; ``ensure_persisted_metric`` resolves one such query by
slug on a registry miss. The legacy ``metrics``-table loader
(``load_persisted_metrics``, migration 0008) is retained but deprecated —
migration 0012 moves each ``metrics`` row into a query-with-``config.metric``,
preserving the slug so consumers keep resolving the same id.

Where the query registry stores ``RegisteredQuery`` rows, this registry stores
:class:`app.metrics.models.MetricDefinition` objects directly — the definition is
already an immutable, serialisable dataclass with ``to_dict`` / ``from_dict``, so
there is no wrapper type.

Persistence
-----------
The ``metrics`` table columns are ``id, org_id, project_id, created_by, slug,
name, definition jsonb`` (UNIQUE(org_id, slug)). The serialized form in
``definition`` is exactly ``MetricDefinition.to_dict()``. ``load_persisted_metrics``
reads every row and re-registers it under the row ``id`` (so the registry id and
the persisted-row id are the same identifier, exactly like the query path).

Seed metric
-----------
``demo_revenue`` — ``SUM(value)`` from the built-in 5-row ``demo`` table, grouped
by ``name``. Compiles + runs against the demo DuckDB connector with no external
configuration, so the metric routes work out of the box (and tests have a metric
to exercise end-to-end).
"""

from __future__ import annotations

import logging

from app.metrics.models import (
    Dimension,
    Measure,
    MetricDefinition,
    TimeDimension,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MetricRegistry
# ---------------------------------------------------------------------------


class MetricRegistry:
    """Registry of governed metric definitions (mirror of ``QueryRegistry``).

    A plain dict wrapper, keyed by metric id. NOT thread-safe for concurrent
    writes — registration happens at import time / single-threaded request
    handlers (same contract as the query registry).
    """

    def __init__(self) -> None:
        self._store: dict[str, MetricDefinition] = {}

    def register(self, metric: MetricDefinition) -> MetricDefinition:
        """Register (or overwrite) *metric* by its ``id`` and return it.

        Overwrites any existing registration with the same id — intentional so
        seed/persisted metrics can be refreshed and ``PUT /metrics/{id}``
        re-registers.
        """
        self._store[metric.id] = metric
        return metric

    def get(self, id: str) -> MetricDefinition | None:
        """Return the :class:`MetricDefinition` for *id*, or ``None``."""
        return self._store.get(id)

    def all(self) -> list[MetricDefinition]:
        """Return all registered metrics as a list (insertion order)."""
        return list(self._store.values())

    def unregister(self, id: str) -> None:
        """Remove a metric from the registry (no-op if absent)."""
        self._store.pop(id, None)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: MetricRegistry | None = None

#: Ids of the in-code seed metrics (no backing query row). The ``/metrics`` list
#: route always keeps these visible regardless of org scoping, since they belong
#: to no tenant. Keep in sync with :func:`_seed_demo_metrics`.
SEED_METRIC_IDS: frozenset[str] = frozenset({"demo_revenue"})


def _seed_demo_metrics(registry: MetricRegistry) -> None:
    """Seed the built-in demo metric so the routes work out of the box.

    ``demo_revenue`` aggregates ``SUM(value)`` from the 5-row ``demo`` table the
    demo connector registers (id, name, value, active), grouped by ``name``.
    """
    registry.register(
        MetricDefinition(
            id="demo_revenue",
            name="Demo revenue",
            measure=Measure(name="revenue", agg="sum", expr="value", type="additive"),
            base_table="demo",
            dimensions=(
                Dimension(name="name", type="text"),
                Dimension(name="active", type="bool"),
            ),
            time_dimension=None,
            description="Total demo value (SUM of value) by name — built-in demo metric.",
        )
    )


def get_metric_registry() -> MetricRegistry:
    """Return (or create) the module-level :class:`MetricRegistry` singleton.

    Seeded with the demo metric on first call so the metric flow works with no
    external configuration.
    """
    global _registry
    if _registry is None:
        _registry = MetricRegistry()
        _seed_demo_metrics(_registry)
    return _registry


def reset_for_tests() -> None:
    """Reset the metric registry singleton to its default seeded state.

    Test-only helper (mirrors ``app.queries.registry.reset_for_tests``).
    """
    global _registry
    _registry = None
    get_metric_registry()


# ---------------------------------------------------------------------------
# Persisted-metric loader (DB → runtime registry)
# ---------------------------------------------------------------------------


def _definition_from_row(row: object) -> MetricDefinition | None:
    """Build a :class:`MetricDefinition` from a ``metrics`` row (best-effort).

    The row carries ``id``, ``slug``, ``name`` and the serialized
    ``definition`` JSONB. We register the metric under the row ``id`` (the
    canonical identifier callers reference), folding the row's ``name`` in when
    the serialized definition omits it.
    """
    import json

    definition = row["definition"]  # type: ignore[index]
    if isinstance(definition, str):
        definition = json.loads(definition)
    if not isinstance(definition, dict):
        return None
    data = dict(definition)
    # The canonical id is the row id (so registry id == persisted id, like the
    # query path); fall back to the serialized id only if the row has none.
    data["id"] = str(row["id"]) or data.get("id")  # type: ignore[index]
    if not data.get("name"):
        data["name"] = row["name"]  # type: ignore[index]
    return MetricDefinition.from_dict(data)


async def load_persisted_metrics() -> int:
    """Load metrics from the ``metrics`` table into the runtime registry.

    Best-effort, mirroring ``load_persisted_queries``: any failure to reach the
    DB or parse a row is logged as a warning and never propagated, so it can be
    wired into startup without risking the app failing to boot when the DB /
    table is unavailable.

    Returns the number of metrics successfully registered.

    DEPRECATED source — the ``metrics`` table is being collapsed into
    queries-with-``config.metric`` (migration 0012). Startup now calls
    :func:`load_metrics_from_queries`; this loader is retained for the
    pre-migration window / direct callers and is a no-op once the table is empty.
    """
    try:
        from app.db import fetch

        rows = await fetch("SELECT id, slug, name, definition FROM metrics")
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash startup.
        logger.warning("load_persisted_metrics: could not read metrics table: %s", exc)
        return 0

    registry = get_metric_registry()
    loaded = 0
    for row in rows:
        try:
            metric = _definition_from_row(row)
            if metric is None:
                continue
            registry.register(metric)
            loaded += 1
        except Exception as exc:  # noqa: BLE001 — skip one bad row, keep going.
            logger.warning(
                "load_persisted_metrics: skipping malformed metric row: %s", exc
            )
            continue

    if loaded:
        logger.info("load_persisted_metrics: registered %d persisted metrics", loaded)
    return loaded


# ---------------------------------------------------------------------------
# Query-backed metric loader (queries-with-`config.metric` → registry)
# ---------------------------------------------------------------------------
# The unified source of metrics: a ``queries`` row whose ``config`` carries a
# ``metric`` block IS a governed metric. The registry sources metrics from those
# rows (keyed by ``config.metric.slug``), so a plain query is unaffected and a
# query-with-metric is consumable by AI/watches/pre-agg/dashboards unchanged.


def _coerce_config(config: object) -> dict | None:
    """Return *config* as a dict (parsing JSON text), or ``None`` if not a dict."""
    import json

    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (ValueError, TypeError):
            return None
    return config if isinstance(config, dict) else None


def _definition_from_query_row(row: object) -> MetricDefinition | None:
    """Build a :class:`MetricDefinition` from a query-with-``config.metric`` row.

    Maps a ``queries`` row onto the metric contract (Section 1 of the
    query/metric unification doc):

    - ``id``           = ``config.metric.slug``  (the stable metric id)
    - ``name``         = ``query.name``
    - ``base_sql``     = ``config.sql``          (``base_table`` stays ``None`` —
                          queries are SQL, used as a trusted subquery)
    - ``datastore_id`` = ``config.datastore_id``
    - measure / dimensions / time_dimension / default_filters / rls_keys /
      owner / description = from ``config.metric.*``

    Reuses ``MetricDefinition.from_dict`` so validation rules are identical to
    the ``metrics``-table path. Returns ``None`` when the row has no usable
    ``config.metric`` block (so a plain query is silently skipped).
    """
    config = _coerce_config(row["config"])  # type: ignore[index]
    if config is None:
        return None
    metric = config.get("metric")
    if not isinstance(metric, dict):
        return None
    slug = str(metric.get("slug") or "").strip()
    if not slug:
        return None

    data: dict = {
        "id": slug,
        "name": row["name"],  # type: ignore[index]
        "measure": metric.get("measure") or {},
        "base_sql": config.get("sql"),
        "base_table": None,
        "datastore_id": config.get("datastore_id"),
        "dimensions": metric.get("dimensions") or [],
        "time_dimension": metric.get("time_dimension"),
        "default_filters": metric.get("default_filters") or [],
        "rls_keys": metric.get("rls_keys") or [],
        "owner": metric.get("owner"),
        "description": metric.get("description") or "",
    }
    return MetricDefinition.from_dict(data)


async def load_metrics_from_queries() -> int:
    """Load metrics from queries-with-``config.metric`` into the runtime registry.

    The unified source: ``SELECT … FROM queries WHERE config ? 'metric'`` →
    :func:`_definition_from_query_row` → register by ``config.metric.slug``.
    Tenant/registry semantics mirror :func:`load_persisted_metrics` (process-
    global singleton; org scoping happens at the route layer). Best-effort:
    never crashes startup.

    Returns the number of metrics successfully registered.
    """
    try:
        from app.db import fetch

        rows = await fetch(
            "SELECT id, org_id, project_id, name, config FROM queries "
            "WHERE config ? 'metric'"
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash startup.
        logger.warning(
            "load_metrics_from_queries: could not read queries table: %s", exc
        )
        return 0

    registry = get_metric_registry()
    loaded = 0
    for row in rows:
        try:
            metric = _definition_from_query_row(row)
            if metric is None:
                continue
            registry.register(metric)
            loaded += 1
        except Exception as exc:  # noqa: BLE001 — skip one bad row, keep going.
            logger.warning(
                "load_metrics_from_queries: skipping malformed query-metric row: %s",
                exc,
            )
            continue

    if loaded:
        logger.info(
            "load_metrics_from_queries: registered %d query-backed metrics", loaded
        )
    return loaded


async def ensure_persisted_metric(
    metric_id: str, org_id: str | None = None
) -> MetricDefinition | None:
    """Lazily load a single metric on a registry miss, by its slug.

    The metric id IS the ``config.metric.slug`` of the backing query. The runtime
    registry is populated at startup, so a metric authored on another process is
    invisible until restart — the routes call this on a ``registry.get()`` miss
    to load just that query-with-metric from the DB. Best-effort: returns the
    metric if found+loaded, else ``None``.

    TENANT ISOLATION (SEC): the slug→query lookup MUST be org-scoped. Slugs are
    only UNIQUE per (org_id, slug); without an org filter a caller could resolve
    ANOTHER org's metric by guessing/knowing its slug, leaking that org's
    base_sql / datastore binding / dimensions and (via /metrics/{slug}/query)
    executing it. When *org_id* is supplied the ``WHERE`` clause is restricted to
    that org so a slug only resolves within the caller's tenant. ``org_id=None``
    preserves the unscoped lookup for trusted internal callers (e.g. startup
    loaders / system ticks) only.
    """
    registry = get_metric_registry()
    try:
        from app.db import fetchrow

        if org_id is not None:
            row = await fetchrow(
                "SELECT id, org_id, project_id, name, config FROM queries "
                "WHERE config->'metric'->>'slug' = $1 AND org_id = $2::uuid LIMIT 1",
                metric_id,
                org_id,
            )
        else:
            row = await fetchrow(
                "SELECT id, org_id, project_id, name, config FROM queries "
                "WHERE config->'metric'->>'slug' = $1 LIMIT 1",
                metric_id,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the request.
        logger.warning("ensure_persisted_metric(%s): DB read failed: %s", metric_id, exc)
        return None
    if row is None:
        return None
    try:
        metric = _definition_from_query_row(row)
        if metric is None:
            return None
        registry.register(metric)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_persisted_metric(%s): register failed: %s", metric_id, exc)
        return None
    return registry.get(metric.id)


async def metric_belongs_to_org(metric_id: str, org_id: str) -> bool:
    """Return True when *metric_id* (slug) is backed by a query in *org_id*.

    TENANT ISOLATION (SEC): the metric registry is a process-GLOBAL singleton, so
    a ``registry.get(slug)`` hit may belong to a DIFFERENT org (it was loaded by
    that org's startup/request on this same process). Before a route hands back a
    metric resolved from the shared registry, it MUST confirm the slug is exposed
    by a query OWNED by the caller's org. In-code seeds (``SEED_METRIC_IDS``,
    e.g. ``demo_revenue``) belong to no tenant and are always allowed. Best-effort
    on DB error → False (fail closed: a metric we cannot prove the caller owns is
    treated as not theirs).
    """
    if metric_id in SEED_METRIC_IDS:
        return True
    try:
        from app.db import fetchrow

        row = await fetchrow(
            "SELECT 1 AS ok FROM queries "
            "WHERE config->'metric'->>'slug' = $1 AND org_id = $2::uuid LIMIT 1",
            metric_id,
            org_id,
        )
    except Exception as exc:  # noqa: BLE001 — fail closed on a scoping read error.
        logger.warning(
            "metric_belongs_to_org(%s, %s): DB read failed: %s", metric_id, org_id, exc
        )
        return False
    return row is not None
