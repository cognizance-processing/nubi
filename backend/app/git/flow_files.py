"""Flows-as-files — per-cell file projection of a flow spec (workstream A3).

A flow is canonically one ``FlowSpec`` (cells + edges + settings). For the
**file/folder persona** and reviewable git diffs we project that spec onto a
small directory tree, and load it back losslessly:

    flows/<slug>__<id8>/
        flow.toml                 # flow metadata + ordered [[cells]] + [layout]
        cells/01_<key>.sql        # source of an SQL cell   (config.sql)
        cells/02_<key>.py         # source of a Python cell (config.code)
        cells/03_<key>.md         # source of a Markdown cell (config.markdown)

Design
------
* **Lossless.** ``flow.toml`` stores the FULL cell dict minus the source text
  (which lives in the sidecar file) and minus ``ui`` coords (which live in a
  separate ``[layout]`` table so a canvas drag never dirties a cell's diff).
  Round-trip re-merges both, so any current/future ``TaskSpec`` field survives.
* **Stable order.** Cells are written in spec order with a zero-padded ``NN``
  prefix; ``flow.toml``'s ``[[cells]]`` array is the authoritative order on load.
* **Only the three editable cell kinds get a sidecar file.** Complex kinds
  (map/branch/materialize/bucket_load/agent/noop-without-markdown) keep their
  whole config inline in ``flow.toml`` — there is no single "source" to extract.

This module is pure (no I/O): it returns/consumes ``{path: content}`` so the git
layer (env_sync / project remote) can write or read via its existing helpers.
"""

from __future__ import annotations

import re
from typing import Any

import toml

from app.flows.spec import validate_flow_spec

# ── source-key mapping ──────────────────────────────────────────────────────
# Each editable cell projects one config key to a sidecar file with a given ext.
_EXT_BY_SRC = {"sql": "sql", "code": "py", "markdown": "md"}


def _cell_source_key(cell: dict[str, Any]) -> str | None:
    """Return the config key holding this cell's editable source, or None.

    Prefers the user-facing ``cell_type`` (v4 "cells, not kinds"), falling back
    to the execution ``kind``. Returns None for kinds with no single source
    (map, branch, materialize, …) — those serialise wholly inside flow.toml.
    """
    cell_type = cell.get("cell_type")
    kind = cell.get("kind")
    config = cell.get("config") or {}
    if cell_type == "sql" or kind == "query":
        return "sql"
    if cell_type == "python" or kind == "python":
        return "code"
    if cell_type == "markdown" or (kind == "noop" and "markdown" in config):
        return "markdown"
    return None


def slugify(name: str) -> str:
    """Lowercase, hyphenated, filesystem-safe slug (empty → ``flow``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "flow"


def flow_dir(flow_id: str, flow_name: str) -> str:
    """Directory name for a flow: ``flows/<slug>__<id8>`` (id disambiguates)."""
    id8 = str(flow_id or "").replace("-", "")[:8] or "00000000"
    return f"flows/{slugify(flow_name)}__{id8}"


def _drop_none(obj: Any) -> Any:
    """Recursively drop ``None`` values (TOML cannot represent them)."""
    if isinstance(obj, dict):
        return {k: _drop_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_none(v) for v in obj]
    return obj


def serialize_flow_files(
    flow_id: str, flow_name: str, spec: dict[str, Any]
) -> list[dict[str, str]]:
    """Project a flow spec to a list of ``{"path", "content"}`` file dicts.

    Parameters
    ----------
    flow_id, flow_name:
        Identify the flow; drive the ``flows/<slug>__<id8>`` directory name.
    spec:
        A FlowSpec dict (``{version, name, params, tasks, runtime_config}``).
    """
    base = flow_dir(flow_id, flow_name)
    files: list[dict[str, str]] = []

    cells_out: list[dict[str, Any]] = []
    layout: dict[str, Any] = {}

    for idx, cell in enumerate(spec.get("tasks") or [], start=1):
        key = cell.get("key") or f"cell{idx}"
        # Copy the full cell; peel off ui (→ layout) and source (→ sidecar file).
        entry = {k: v for k, v in cell.items() if k != "ui"}
        ui = cell.get("ui")
        if ui:
            layout[key] = ui

        src_key = _cell_source_key(cell)
        if src_key is not None:
            ext = _EXT_BY_SRC[src_key]
            fname = f"cells/{idx:02d}_{key}.{ext}"
            config = dict(cell.get("config") or {})
            source = config.pop(src_key, "") or ""
            files.append({"path": f"{base}/{fname}", "content": str(source)})
            entry["config"] = config
            entry["file"] = fname
        cells_out.append(_drop_none(entry))

    manifest: dict[str, Any] = {
        "flow": _drop_none(
            {
                "name": spec.get("name") or flow_name,
                "id": str(flow_id or ""),
                "version": spec.get("version", 1),
            }
        ),
    }
    if spec.get("runtime_config"):
        manifest["runtime_config"] = _drop_none(spec["runtime_config"])
    if spec.get("params"):
        manifest["params"] = [_drop_none(p) for p in spec["params"]]
    manifest["cells"] = cells_out
    if layout:
        manifest["layout"] = layout

    files.append({"path": f"{base}/flow.toml", "content": toml.dumps(manifest)})
    return files


def load_flow_files(files: dict[str, str]) -> dict[str, Any]:
    """Reconstruct a validated flow spec dict from ``{relpath: content}``.

    ``files`` keys are paths RELATIVE to the flow directory (e.g. ``flow.toml``,
    ``cells/01_foo.sql``). Raises ``ValueError`` if flow.toml is missing or the
    reconstructed spec fails hard validation.
    """
    manifest_text = files.get("flow.toml")
    if manifest_text is None:
        raise ValueError("flow.toml not found in flow file set")
    manifest = toml.loads(manifest_text)

    flow_meta = manifest.get("flow") or {}
    tasks: list[dict[str, Any]] = []
    layout = manifest.get("layout") or {}

    for entry in manifest.get("cells") or []:
        cell = dict(entry)
        fname = cell.pop("file", None)
        if fname is not None:
            src_key = _cell_source_key(cell)
            if src_key is not None:
                content = files.get(fname)
                if content is None:
                    raise ValueError(f"cell source file missing: {fname}")
                config = dict(cell.get("config") or {})
                config[src_key] = content
                cell["config"] = config
        ui = layout.get(cell.get("key"))
        if ui is not None:
            cell["ui"] = ui
        tasks.append(cell)

    spec: dict[str, Any] = {
        "version": flow_meta.get("version", 1),
        "name": flow_meta.get("name") or "Untitled flow",
        "tasks": tasks,
    }
    if manifest.get("params"):
        spec["params"] = manifest["params"]
    if manifest.get("runtime_config"):
        spec["runtime_config"] = manifest["runtime_config"]

    parsed, issues = validate_flow_spec(spec)
    hard = [i for i in issues if not str(i).startswith("[warn]")]
    if parsed is None or hard:
        raise ValueError(f"invalid flow spec from files: {hard or issues}")
    return spec
