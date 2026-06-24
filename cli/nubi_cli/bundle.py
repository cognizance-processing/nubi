"""Declarative resource-bundle loader and applier for ``nubi apply`` / ``nubi plan``.

Bundle format
-------------
A *bundle* is a directory with the following structure::

    mybundle/
      bundle.yaml          ← required: org + version + optional project
      metrics/             ← optional; *.yaml files, each a MetricDefinition
        revenue.yaml
        churn.yaml
      datastores.yaml      ← optional; list of connector envelopes
      dashboards/          ← optional; *.json files, each a dashboard envelope
        main.json
      queries/             ← optional; *.yaml or *.json files, each a query envelope
        top_customers.yaml

``bundle.yaml`` schema::

    apiVersion: nubi/v1
    kind: bundle
    metadata:
      org: <org-id-or-slug>          # required
      version: "1"                   # required
      project: <project-id>          # optional

Metric YAML schema (``metrics/*.yaml``)::

    # Converted to a portability envelope of kind "query" with config.metric block.
    slug: revenue                    # stable metric id (slug)
    name: Revenue                    # human name
    measure:
      name: revenue
      agg: sum
      expr: amount
      type: additive
    base_table: orders               # use base_table OR base_sql
    # base_sql: "SELECT …"
    datastore_id: <connector-id>     # optional; pin to a specific connector
    dimensions:
      - name: region
        type: text
      - name: product_category
        type: text
    time_dimension:
      name: created_at
      type: time
    description: "Total revenue."

Datastore YAML schema (``datastores.yaml``)::

    # A list of connector portability envelopes.
    - kind: connector
      apiVersion: nubi/v1
      metadata:
        name: Production Postgres
        id: <existing-connector-id>   # optional; present → update
      spec:
        connector_type: postgres
        host: db.example.com
        port: 5432
        database: prod
        user: analytics

Dashboard JSON schema (``dashboards/*.json``)::

    A portability envelope with kind "dashboard" (Nubi's existing format).

Idempotency
-----------
Applying the SAME bundle twice is a true no-op: every resource is identified by
a stable key (metric slug, dashboard id, connector id) and the backend upserts
by that key.  The CLI reports ``unchanged`` for resources that are already
up-to-date.

Partial failure
---------------
A resource that fails to apply is recorded as ``failed`` in the results table
and the CLI exits with code 1 after printing the error.  The remaining resources
in the bundle continue to be applied — one bad resource does NOT abort the rest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Bundle parsing errors
# ---------------------------------------------------------------------------


class BundleError(Exception):
    """Raised when a bundle directory or file cannot be loaded."""


# ---------------------------------------------------------------------------
# bundle.yaml loader
# ---------------------------------------------------------------------------


def load_bundle_meta(bundle_dir: Path) -> dict[str, Any]:
    """Read and validate ``bundle.yaml`` in *bundle_dir*.

    Returns the parsed ``bundle.yaml`` dict.

    Raises
    ------
    BundleError
        When ``bundle.yaml`` is absent, cannot be parsed, or is missing
        required fields (``metadata.org``, ``metadata.version``).
    """
    bundle_file = bundle_dir / "bundle.yaml"
    if not bundle_file.exists():
        raise BundleError(
            f"bundle.yaml not found in {bundle_dir}. "
            "A bundle directory must contain a bundle.yaml file."
        )
    try:
        raw = yaml.safe_load(bundle_file.read_text())
    except yaml.YAMLError as exc:
        raise BundleError(f"bundle.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise BundleError("bundle.yaml must be a YAML mapping.")

    meta = raw.get("metadata") or {}
    if not meta.get("org"):
        raise BundleError(
            "bundle.yaml must include metadata.org (the target organisation)."
        )
    if not meta.get("version"):
        raise BundleError(
            "bundle.yaml must include metadata.version (e.g. '1')."
        )
    return raw


# ---------------------------------------------------------------------------
# Resource loaders
# ---------------------------------------------------------------------------


def _load_yaml_file(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise BundleError(f"{path.name}: invalid YAML — {exc}") from exc


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise BundleError(f"{path.name}: invalid JSON — {exc}") from exc


def _load_yaml_or_json(path: Path) -> Any:
    """Load a YAML or JSON file based on extension."""
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return _load_yaml_file(path)
    if suffix == ".json":
        return _load_json_file(path)
    raise BundleError(f"{path.name}: unsupported file extension (use .yaml or .json).")


# ---------------------------------------------------------------------------
# Metric → portability envelope conversion
# ---------------------------------------------------------------------------


def _metric_to_envelope(raw: dict[str, Any], source_path: Path) -> dict[str, Any]:
    """Convert a metric YAML definition to a portability envelope (kind='query').

    Metrics are stored as queries-with-``config.metric`` in the backend (the
    metric/query unification introduced in Wave C).  The portability envelope
    kind is therefore ``"query"``; the spec carries ``sql``, ``datastore_id``,
    and the ``metric`` block.

    The stable metric identity is ``config.metric.slug``.  When the metric YAML
    carries a ``metadata.id`` that matches an existing query UUID, the backend
    updates that query; otherwise it upserts by slug.
    """
    if not isinstance(raw, dict):
        raise BundleError(f"{source_path.name}: metric file must be a YAML mapping.")

    slug = str(raw.get("slug") or "").strip()
    if not slug:
        raise BundleError(
            f"{source_path.name}: metric definition must include a 'slug' field."
        )

    name = str(raw.get("name") or slug).strip()
    base_table = raw.get("base_table")
    base_sql = raw.get("base_sql")
    datastore_id = raw.get("datastore_id")

    if not base_table and not base_sql:
        raise BundleError(
            f"{source_path.name}: metric '{slug}' must specify 'base_table' or 'base_sql'."
        )

    # Build the config.metric block (mirrors _query_config_from_metric in backend).
    metric_block = {
        "slug": slug,
        "measure": raw.get("measure") or {},
        "dimensions": raw.get("dimensions") or [],
        "time_dimension": raw.get("time_dimension"),
        "default_filters": raw.get("default_filters") or [],
        "rls_keys": raw.get("rls_keys") or [],
        "owner": raw.get("owner"),
        "description": raw.get("description") or "",
    }
    # Remove None values from metric block for cleanliness.
    metric_block = {k: v for k, v in metric_block.items() if v is not None}

    spec: dict[str, Any] = {"metric": metric_block}
    if base_sql:
        spec["sql"] = base_sql
    if datastore_id:
        spec["datastore_id"] = datastore_id
    if base_table and not base_sql:
        # Synthesise a minimal SELECT * FROM <table> SQL — the backend uses
        # base_sql when present; base_table is a shorthand for the compile layer.
        spec["sql"] = f"SELECT * FROM {base_table}"
        spec["base_table"] = base_table

    meta: dict[str, Any] = {"name": name}
    # Carry an explicit id through if the file specifies one (allows pinning to
    # an existing query UUID for update-in-place).
    existing_id = raw.get("id") or (raw.get("metadata") or {}).get("id")
    if existing_id:
        meta["id"] = str(existing_id)

    return {
        "kind": "query",
        "apiVersion": "nubi/v1",
        "metadata": meta,
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# Bundle → envelope list
# ---------------------------------------------------------------------------


class BundleLoadResult:
    """Result of loading a bundle directory."""

    def __init__(self) -> None:
        self.envelopes: list[dict[str, Any]] = []
        self.errors: list[str] = []   # per-file load errors (non-fatal)
        self.meta: dict[str, Any] = {}

    def add_envelope(self, env: dict[str, Any]) -> None:
        self.envelopes.append(env)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)


def load_bundle(bundle_dir: Path) -> BundleLoadResult:
    """Read a bundle directory and return all portability envelopes.

    Load order: metrics → datastores → dashboards → queries.
    Errors in individual files are collected but do NOT abort loading; they are
    surfaced to the caller in ``result.errors`` so the CLI can print them.

    Raises
    ------
    BundleError
        When ``bundle.yaml`` is absent or invalid (fatal; always raised).
    """
    result = BundleLoadResult()
    result.meta = load_bundle_meta(bundle_dir)  # raises BundleError if invalid

    # ── Metrics ────────────────────────────────────────────────────────────
    metrics_dir = bundle_dir / "metrics"
    if metrics_dir.is_dir():
        for path in sorted(metrics_dir.glob("*.yaml")):
            try:
                raw = _load_yaml_file(path)
                env = _metric_to_envelope(raw, path)
                result.add_envelope(env)
            except BundleError as exc:
                result.add_error(f"metrics/{path.name}: {exc}")

    # ── Datastores (connectors) ─────────────────────────────────────────────
    ds_file = bundle_dir / "datastores.yaml"
    if ds_file.exists():
        try:
            raw = _load_yaml_file(ds_file)
            if not isinstance(raw, list):
                result.add_error("datastores.yaml: must be a YAML list of connector envelopes.")
            else:
                for i, item in enumerate(raw):
                    if not isinstance(item, dict):
                        result.add_error(f"datastores.yaml[{i}]: must be a mapping.")
                        continue
                    # Ensure kind/apiVersion are present.
                    item.setdefault("kind", "connector")
                    item.setdefault("apiVersion", "nubi/v1")
                    result.add_envelope(item)
        except BundleError as exc:
            result.add_error(str(exc))

    # ── Dashboards ─────────────────────────────────────────────────────────
    dashboards_dir = bundle_dir / "dashboards"
    if dashboards_dir.is_dir():
        for path in sorted(dashboards_dir.glob("*.json")):
            try:
                raw = _load_json_file(path)
                if not isinstance(raw, dict):
                    result.add_error(f"dashboards/{path.name}: must be a JSON object.")
                    continue
                raw.setdefault("kind", "dashboard")
                raw.setdefault("apiVersion", "nubi/v1")
                result.add_envelope(raw)
            except BundleError as exc:
                result.add_error(f"dashboards/{path.name}: {exc}")

    # ── Queries ────────────────────────────────────────────────────────────
    queries_dir = bundle_dir / "queries"
    if queries_dir.is_dir():
        for path in sorted(queries_dir.glob("*")):
            if path.suffix.lower() not in (".yaml", ".yml", ".json"):
                continue
            try:
                raw = _load_yaml_or_json(path)
                if not isinstance(raw, dict):
                    result.add_error(f"queries/{path.name}: must be a mapping.")
                    continue
                raw.setdefault("kind", "query")
                raw.setdefault("apiVersion", "nubi/v1")
                result.add_envelope(raw)
            except BundleError as exc:
                result.add_error(f"queries/{path.name}: {exc}")

    return result
