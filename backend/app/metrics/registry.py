"""Metric registry — in-process catalogue of governed metric definitions.

This mirrors ``app/queries/registry.py`` verbatim in shape: a plain dict-backed
singleton with ``register`` / ``unregister`` / ``get`` / ``all``, a module-level
``get_metric_registry()`` accessor, a ``reset_for_tests()`` helper, and a
DB-backed loader (``load_persisted_metrics``) that hydrates the registry from the
``metrics`` table (migration 0008) at startup.

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


async def ensure_persisted_metric(metric_id: str) -> MetricDefinition | None:
    """Lazily load a single persisted metric on a registry miss.

    Mirrors ``ensure_persisted_query``: the runtime registry is populated at
    startup, so metrics created while the server is running on another process
    are invisible until restart. The routes call this on a ``registry.get()``
    miss to load just that row from the DB. Best-effort: returns the metric if
    found+loaded, else ``None``.
    """
    registry = get_metric_registry()
    try:
        from app.db import fetchrow

        row = await fetchrow(
            "SELECT id, slug, name, definition FROM metrics WHERE id = $1::uuid",
            metric_id,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the request.
        logger.warning("ensure_persisted_metric(%s): DB read failed: %s", metric_id, exc)
        return None
    if row is None:
        return None
    try:
        metric = _definition_from_row(row)
        if metric is None:
            return None
        registry.register(metric)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_persisted_metric(%s): register failed: %s", metric_id, exc)
        return None
    return registry.get(metric.id)
