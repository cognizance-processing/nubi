"""Portable, LLM-editable spec format for dashboards + queries (export/import).

Goal
----
Give every portable resource a single, stable, human-/LLM-editable document
format so a dashboard or query can be exported to a file, edited (by a human or
an LLM), committed to git, and re-imported — round-tripping cleanly.

Envelope
--------
A Kubernetes-style envelope, YAML-primary (JSON is also accepted, since YAML is
a JSON superset)::

    kind: dashboard            # dashboard | query
    apiVersion: nubi/v1
    metadata:
      name: <human name>
      id: <uuid?>             # present → import updates; absent → import creates
      project: <slug?>        # optional project hint (informational)
    spec: { ... }             # the resource's existing spec, unchanged

Connectors are deliberately EXCLUDED from portability (product decision: they
carry credentials / network topology and are not git-friendly). There is no
``connector`` kind.

Kind registry
-------------
Each supported kind maps to:

- ``resource``  — the org-scoped resource table name (``boards`` | ``queries``).
- ``spec_from_row(row)``   — extract the portable spec dict from a stored row.
- ``row_fields(env)``      — build the ``{name, config}`` create/update payload
  from an envelope (this is what the resource create/update path consumes).
- ``validate(spec)``       — reuse the kind's existing validator; returns a list
  of human-readable issue strings (empty list = valid).

Dashboards
~~~~~~~~~~
Stored in the ``boards`` table.  The portable spec is the canonical
``DashboardSpec`` document, which lives at ``row['config']['spec']`` (the editor
nests the spec under a ``spec`` key inside config; we fall back to treating the
whole ``config`` as the spec for forward/backward compatibility).  Validation
reuses ``app.dashboards.spec.validate_spec``.

Queries
~~~~~~~
Stored in the ``queries`` table.  The portable spec is
``{sql, params, datastore_id, name}`` — exactly the persisted ``queries.config``
shape that ``load_persisted_queries`` / ``ensure_persisted_query`` consume.
Validation reuses the query registry's ``QueryParam`` construction (the same
param validation the registry applies).

Public API
----------
``to_envelope(kind, row) -> dict``
    Build an envelope dict from a stored resource row.
``dump_envelope(env, format='yaml'|'json') -> str``
    Serialise an envelope to YAML (block style, stable key order) or JSON.
``parse_document(text) -> dict``
    Parse a YAML or JSON document string into an envelope dict (validates the
    envelope shape: kind/apiVersion/spec).
``validate_spec_for_kind(kind, spec) -> list[str]``
    Reuse the kind's existing validator; returns issue strings.
``row_fields_for_kind(kind, env) -> dict``
    Build the ``{name, config}`` payload for the resource create/update path.
``KIND_REGISTRY``
    Mapping of kind → :class:`KindHandler`.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

import yaml

from app.errors import AppError

API_VERSION = "nubi/v1"

# Ordered envelope keys — used to enforce a stable, predictable key order when
# serialising (both YAML and JSON).
_ENVELOPE_KEY_ORDER = ("kind", "apiVersion", "metadata", "spec")
_METADATA_KEY_ORDER = ("name", "id", "project")


# ---------------------------------------------------------------------------
# Kind registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KindHandler:
    """Describes how a portable kind maps to a stored resource.

    Attributes
    ----------
    kind:
        The envelope ``kind`` string (``"dashboard"`` | ``"query"`` | ``"flow"``).
    resource:
        The org-scoped resource table name (``"boards"`` | ``"queries"`` |
        ``"flows"``). For kinds not stored via the generic ``Repo`` (flows use
        the flow store), this is informational and the caller branches on
        ``kind`` for the upsert path.
    folder:
        The on-disk folder this kind serialises into (``"dashboards"`` |
        ``"queries"`` | ``"flows"``). Centralising the folder map here lets
        push AND pull iterate the registry so coverage stays symmetric — the
        root cause of the "flows push but never pull" asymmetry.
    spec_from_row:
        Extract the portable spec dict from a stored row.
    row_fields:
        Build the create/update payload from an envelope. Shape is kind-specific
        (``{name, config}`` for repo-backed kinds; ``{name, spec, id}`` for flows).
    validate:
        Reuse the kind's existing validator; returns a list of issue strings
        (empty list = valid).
    """

    kind: str
    resource: str
    spec_from_row: Callable[[dict[str, Any]], dict[str, Any]]
    row_fields: Callable[[dict[str, Any]], dict[str, Any]]
    validate: Callable[[dict[str, Any]], list[str]]
    folder: str = ""


# ── Dashboard handler ──────────────────────────────────────────────────────


def _dashboard_spec_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical DashboardSpec dict stored in a board row.

    The editor nests the spec under ``config['spec']``.  For
    forward/backward-compat we fall back to treating the whole ``config`` as the
    spec when no ``spec`` key is present.
    """
    config = row.get("config") or {}
    if isinstance(config, dict) and isinstance(config.get("spec"), dict):
        return dict(config["spec"])
    if isinstance(config, dict):
        return dict(config)
    return {}


def _dashboard_row_fields(env: dict[str, Any]) -> dict[str, Any]:
    """Build the ``{name, config}`` board payload from an envelope.

    Mirrors how the editor stores boards: the spec lives under
    ``config['spec']``; the board ``name`` comes from metadata.name (falling
    back to the spec ``title``).
    """
    spec = env.get("spec") or {}
    meta = env.get("metadata") or {}
    name = meta.get("name") or spec.get("title") or "Untitled dashboard"
    return {"name": name, "config": {"spec": spec}}


def _dashboard_validate(spec: dict[str, Any]) -> list[str]:
    """Validate a dashboard spec via the existing DashboardSpec validator."""
    from app.dashboards.spec import validate_spec

    parsed, issues = validate_spec(spec)
    if parsed is None:
        # Hard parse failure — issues already describe the field errors.
        return issues or ["Invalid dashboard spec."]
    # Soft warnings (e.g. unknown query_id forward refs) are returned too, but
    # the caller treats only hard parse failures as fatal (parsed is not None).
    return issues


# ── Query handler ──────────────────────────────────────────────────────────


def _query_spec_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return the portable query spec ``{sql, params, datastore_id, name}``.

    The persisted ``queries.config`` already carries this shape (as written by
    the query registry persistence path).  We normalise the four portable keys,
    preferring the row ``name`` for the human label.
    """
    config = row.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    spec: dict[str, Any] = {
        "name": config.get("name") or row.get("name") or "",
        "sql": config.get("sql", ""),
        "params": config.get("params") or [],
        "datastore_id": config.get("datastore_id"),
    }
    # Output-shape contract: carry the declared output_schema ([{name,type}])
    # so it round-trips through export/import. Absent → omitted entirely (a
    # query without a declared contract stays contract-less on re-import).
    output_schema = config.get("output_schema")
    if isinstance(output_schema, list):
        spec["output_schema"] = output_schema
    return spec


def _query_row_fields(env: dict[str, Any]) -> dict[str, Any]:
    """Build the ``{name, config}`` query payload from an envelope.

    The config is the exact persisted ``queries.config`` shape that
    ``load_persisted_queries`` / ``ensure_persisted_query`` consume.
    """
    spec = env.get("spec") or {}
    meta = env.get("metadata") or {}
    name = meta.get("name") or spec.get("name") or "Untitled query"
    config = {
        "name": name,
        "sql": spec.get("sql", ""),
        "params": spec.get("params") or [],
        "datastore_id": spec.get("datastore_id"),
    }
    # Re-import the output-shape contract when the envelope declares one (skip
    # entirely otherwise — see _query_spec_from_row).
    output_schema = spec.get("output_schema")
    if isinstance(output_schema, list):
        config["output_schema"] = output_schema
    return {"name": name, "config": config}


def _query_validate(spec: dict[str, Any]) -> list[str]:
    """Validate a query spec.

    Reuses the query registry's ``QueryParam`` construction for param
    validation (the same validation the registry applies when loading persisted
    queries), and enforces that ``sql`` is a non-empty string.
    """
    from app.queries.registry import QueryParam

    issues: list[str] = []

    sql = spec.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        issues.append("Field 'sql': must be a non-empty string.")

    params = spec.get("params")
    if params is not None:
        if not isinstance(params, list):
            issues.append("Field 'params': must be a list of param descriptors.")
        else:
            for idx, item in enumerate(params):
                if not isinstance(item, dict):
                    issues.append(f"params[{idx}]: must be an object.")
                    continue
                if "name" not in item or not str(item.get("name", "")).strip():
                    issues.append(f"params[{idx}]: 'name' is required.")
                    continue
                # Reuse the registry's QueryParam construction — this validates
                # the 'type' Literal and overall shape exactly as the registry
                # does when loading persisted queries.
                try:
                    QueryParam(
                        name=str(item["name"]),
                        type=item.get("type", "text"),
                        default=item.get("default"),
                        required=bool(item.get("required", False)),
                        options_query_id=item.get("options_query_id"),
                    )
                except Exception as exc:  # noqa: BLE001
                    issues.append(f"params[{idx}]: {exc}")

    return issues


# ── Flow handler ───────────────────────────────────────────────────────────


def _flow_spec_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical FlowSpec dict stored on a flow row.

    Flows persist their spec in the ``spec`` column (not nested under
    ``config``), so the portable spec IS that dict.
    """
    spec = row.get("spec")
    return dict(spec) if isinstance(spec, dict) else {}


def _flow_row_fields(env: dict[str, Any]) -> dict[str, Any]:
    """Build the flow create/update payload ``{name, spec, id}`` from an envelope.

    Flows are stored via the flow store rather than the generic ``Repo``, so the
    payload carries ``spec`` (not ``config``) and the ``id`` for upsert keying.
    """
    spec = env.get("spec") or {}
    meta = env.get("metadata") or {}
    name = meta.get("name") or spec.get("name") or "Untitled flow"
    return {"name": name, "spec": spec, "id": meta.get("id")}


def _flow_validate(spec: dict[str, Any]) -> list[str]:
    """Validate a flow spec via the existing FlowSpec validator."""
    from app.flows.spec import validate_flow_spec

    parsed, issues = validate_flow_spec(spec)
    if parsed is None:
        return issues or ["Invalid flow spec."]
    return issues


KIND_REGISTRY: dict[str, KindHandler] = {
    "dashboard": KindHandler(
        kind="dashboard",
        resource="boards",
        folder="dashboards",
        spec_from_row=_dashboard_spec_from_row,
        row_fields=_dashboard_row_fields,
        validate=_dashboard_validate,
    ),
    "query": KindHandler(
        kind="query",
        resource="queries",
        folder="queries",
        spec_from_row=_query_spec_from_row,
        row_fields=_query_row_fields,
        validate=_query_validate,
    ),
    "flow": KindHandler(
        kind="flow",
        resource="flows",
        folder="flows",
        spec_from_row=_flow_spec_from_row,
        row_fields=_flow_row_fields,
        validate=_flow_validate,
    ),
}


def get_handler(kind: str) -> KindHandler:
    """Return the :class:`KindHandler` for *kind*, or raise AppError 404.

    Unknown kinds (including the deliberately-excluded ``connector``) return a
    404 so no information leaks about unsupported kinds.
    """
    handler = KIND_REGISTRY.get(kind)
    if handler is None:
        raise AppError(
            "not_found",
            f"Unknown portable kind: {kind!r}. Supported: "
            f"{sorted(KIND_REGISTRY)!r}.",
            404,
        )
    return handler


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


def to_envelope(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """Build a portable envelope dict from a stored resource row.

    Parameters
    ----------
    kind:
        ``"dashboard"`` | ``"query"``.
    row:
        A stored resource row (as returned by ``repo.get`` / ``repo.list``).

    Returns
    -------
    dict
        The envelope: ``{kind, apiVersion, metadata, spec}``.
    """
    handler = get_handler(kind)
    spec = handler.spec_from_row(row)

    metadata: dict[str, Any] = {"name": row.get("name") or ""}
    if row.get("id") is not None:
        metadata["id"] = str(row["id"])
    # Carry the project hint when the row has one (informational; project
    # scoping on import is driven by the X-Project-Id header, not this field).
    if row.get("project_id") is not None:
        metadata["project"] = str(row["project_id"])

    return {
        "kind": kind,
        "apiVersion": API_VERSION,
        "metadata": metadata,
        "spec": spec,
    }


def slug_for_envelope(env: dict[str, Any]) -> str:
    """Return a filesystem-safe slug for an envelope (for filename).

    Derived from ``metadata.name`` (lower-cased, non-alnum → ``-``), falling
    back to the kind.  Never empty.
    """
    import re

    meta = env.get("metadata") or {}
    base = str(meta.get("name") or env.get("kind") or "resource")
    slug = base.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or str(env.get("kind") or "resource")


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _ordered(env: dict[str, Any]) -> "OrderedDict[str, Any]":
    """Return the envelope with keys in the canonical stable order.

    Top-level keys follow ``_ENVELOPE_KEY_ORDER``; metadata keys follow
    ``_METADATA_KEY_ORDER``.  Any extra keys are appended in their existing
    order so nothing is silently dropped.
    """
    out: "OrderedDict[str, Any]" = OrderedDict()
    for key in _ENVELOPE_KEY_ORDER:
        if key in env:
            out[key] = env[key]
    for key in env:
        if key not in out:
            out[key] = env[key]

    meta = out.get("metadata")
    if isinstance(meta, dict):
        ordered_meta: "OrderedDict[str, Any]" = OrderedDict()
        for key in _METADATA_KEY_ORDER:
            if key in meta:
                ordered_meta[key] = meta[key]
        for key in meta:
            if key not in ordered_meta:
                ordered_meta[key] = meta[key]
        out["metadata"] = ordered_meta

    return out


# Custom representer so OrderedDict serialises as a plain mapping (block style)
# while preserving our chosen key order.
def _ordered_dict_representer(dumper: yaml.Dumper, data: "OrderedDict[str, Any]"):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


yaml.add_representer(OrderedDict, _ordered_dict_representer, Dumper=yaml.SafeDumper)


def dump_envelope(env: dict[str, Any], format: str = "yaml") -> str:
    """Serialise an envelope to a YAML or JSON document string.

    Parameters
    ----------
    env:
        The envelope dict.
    format:
        ``"yaml"`` (default) → block-style YAML with stable key order.
        ``"json"``           → pretty JSON with stable key order.

    Returns
    -------
    str
        The serialised document.

    Raises
    ------
    AppError("validation_error", 400)
        If *format* is not ``"yaml"`` or ``"json"``.
    """
    fmt = (format or "yaml").lower()
    ordered = _ordered(env)

    if fmt == "json":
        # OrderedDict is JSON-serialisable; key order is preserved.
        return json.dumps(ordered, indent=2, ensure_ascii=False, default=str)

    if fmt in ("yaml", "yml"):
        return yaml.safe_dump(
            ordered,
            default_flow_style=False,  # block style
            sort_keys=False,  # preserve our stable key order
            allow_unicode=True,
            width=10_000,  # avoid line-wrapping long SQL strings
        )

    raise AppError(
        "validation_error",
        f"Unsupported format: {format!r}. Use 'yaml' or 'json'.",
        400,
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_document(text: str) -> dict[str, Any]:
    """Parse a YAML or JSON envelope document string.

    YAML is a JSON superset, so ``yaml.safe_load`` parses both formats.  The
    parsed value is validated to be an envelope-shaped mapping with a known
    ``kind`` and a ``spec``.

    Parameters
    ----------
    text:
        The document text (YAML or JSON).

    Returns
    -------
    dict
        The parsed envelope dict.

    Raises
    ------
    AppError("validation_error", 400)
        If the document is empty, not a mapping, or missing required envelope
        fields.
    AppError("not_found", 404)
        If the ``kind`` is not a supported portable kind.
    """
    if not isinstance(text, str) or not text.strip():
        raise AppError("validation_error", "Empty document.", 400)

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AppError(
            "validation_error",
            f"Could not parse document as YAML/JSON: {exc}",
            400,
        ) from exc

    if not isinstance(data, dict):
        raise AppError(
            "validation_error",
            "Document must be a mapping with kind/apiVersion/metadata/spec.",
            400,
        )

    kind = data.get("kind")
    if not isinstance(kind, str) or not kind:
        raise AppError("validation_error", "Missing required field: 'kind'.", 400)

    # Validates the kind is supported (raises 404 otherwise).
    get_handler(kind)

    api_version = data.get("apiVersion")
    if api_version is not None and api_version != API_VERSION:
        raise AppError(
            "validation_error",
            f"Unsupported apiVersion: {api_version!r}. Expected {API_VERSION!r}.",
            400,
        )

    spec = data.get("spec")
    if not isinstance(spec, dict):
        raise AppError(
            "validation_error",
            "Missing or invalid 'spec' (must be a mapping).",
            400,
        )

    metadata = data.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise AppError(
            "validation_error",
            "'metadata' must be a mapping when present.",
            400,
        )

    # Normalise: ensure apiVersion + metadata are present in the returned env.
    return {
        "kind": kind,
        "apiVersion": API_VERSION,
        "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# Validation + create/update payload helpers
# ---------------------------------------------------------------------------


def validate_spec_for_kind(kind: str, spec: dict[str, Any]) -> list[str]:
    """Reuse the kind's existing validator; return a list of issue strings."""
    return get_handler(kind).validate(spec)


def row_fields_for_kind(kind: str, env: dict[str, Any]) -> dict[str, Any]:
    """Build the ``{name, config}`` create/update payload for the kind."""
    return get_handler(kind).row_fields(env)
