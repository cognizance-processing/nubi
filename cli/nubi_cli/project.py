"""On-disk Nubi project format (files-as-code, doc Section A).

A checked-out Nubi project is a normal git repo whose layout mirrors exactly
what ``app.git.env_sync.serialize_version_files`` /
``app.git.flow_files.serialize_flow_files`` write, so ``nubi pull``, the in-app
git file-view, and CI all agree on one shape::

    my-project/
    ├─ nubi.yaml                  # project manifest (committed)
    ├─ .gitignore                 # generated; ignores .nubi/secrets/
    ├─ .nubi/
    │  ├─ project.json            # {project_id, org_id, api_url, default_env}
    │  └─ secrets/                # GITIGNORED — connectors.env + flow.env
    ├─ connectors/<slug>.yaml     # non-secret connector manifests
    ├─ queries/<slug>.sql + .meta.json (+ .schema.json)
    ├─ dashboards/<slug>.json
    └─ flows/<slug>__<id8>/flow.toml + cells/*

This module is the CLI's serializer seam. Per the doc (A.114) it is a thin
wrapper over the backend serializers when those are importable (dev checkout),
falling back to a self-contained implementation so the standalone CLI package
still round-trips dashboards/queries/flows without the backend on the path.

Read MUST accept both id-named and slug-named files (doc A note).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: envelope kind -> on-disk folder
KIND_FOLDER: dict[str, str] = {
    "dashboard": "dashboards",
    "query": "queries",
    "flow": "flows",
    "connector": "connectors",
}
FOLDER_KIND: dict[str, str] = {v: k for k, v in KIND_FOLDER.items()}

API_VERSION = "nubi/v1"

_GITIGNORE_BLOCK = """\
# Nubi local secrets — never commit
.nubi/secrets/
.nubi/credentials
*.local.env
"""

_GITIGNORE_MARKER = "# Nubi local secrets — never commit"


# ---------------------------------------------------------------------------
# Slug + backend import helpers
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """Lowercase, hyphenated, filesystem-safe slug (empty → ``resource``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "resource"


def _backend_serializers():
    """Return ``(serialize_version_files, serialize_flow_files)`` or ``(None, None)``.

    Uses the same dev-checkout path adjustment as ``flows_files`` so the CLI
    reuses ONE source of truth for the on-disk shape when the backend is
    importable.  Returns ``(None, None)`` for the standalone-package path.
    """
    try:
        from .flows_files import _ensure_backend_on_path  # noqa: PLC0415

        if not _ensure_backend_on_path():
            return None, None
        from app.git.env_sync import serialize_version_files  # noqa: PLC0415
        from app.git.flow_files import serialize_flow_files  # noqa: PLC0415

        return serialize_version_files, serialize_flow_files
    except Exception:  # noqa: BLE001 — standalone package, no backend
        return None, None


# ---------------------------------------------------------------------------
# .gitignore + project.json + nubi.yaml
# ---------------------------------------------------------------------------


def write_gitignore(root: Path) -> bool:
    """Write/append the Nubi secrets block to ``<root>/.gitignore`` idempotently.

    Returns True when the file was created or the block appended, False when the
    block was already present.
    """
    path = root / ".gitignore"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if _GITIGNORE_MARKER in existing:
            return False
        sep = "" if existing.endswith("\n") else "\n"
        path.write_text(existing + sep + "\n" + _GITIGNORE_BLOCK, encoding="utf-8")
        return True
    path.write_text(_GITIGNORE_BLOCK, encoding="utf-8")
    return True


def project_json_path(root: Path) -> Path:
    return root / ".nubi" / "project.json"


def read_project_json(root: Path) -> dict[str, Any]:
    """Read ``.nubi/project.json``; empty dict when absent/malformed."""
    path = project_json_path(root)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_project_json(root: Path, pointer: dict[str, Any]) -> None:
    """Write the non-secret local pointer to ``.nubi/project.json``."""
    path = project_json_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")


def write_nubi_yaml(root: Path, manifest: dict[str, Any]) -> None:
    """Write ``nubi.yaml`` (project manifest) — YAML when available, else JSON."""
    path = root / "nubi.yaml"
    try:
        import yaml  # noqa: PLC0415

        text = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    except ImportError:
        text = json.dumps(manifest, indent=2)
    path.write_text(text, encoding="utf-8")


def read_nubi_yaml(root: Path) -> dict[str, Any]:
    """Read ``nubi.yaml``; empty dict when absent/unparseable."""
    path = root / "nubi.yaml"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(text)
    except ImportError:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def build_manifest(
    name: str,
    project_id: str | None,
    org_id: str | None,
    default_env: str = "dev",
    environments: list[str] | None = None,
    git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``nubi.yaml`` manifest dict (doc A.133)."""
    metadata: dict[str, Any] = {"name": name}
    if project_id:
        metadata["id"] = project_id
    if org_id:
        metadata["org"] = org_id
    spec: dict[str, Any] = {
        "default_env": default_env,
        "environments": environments or [default_env],
    }
    if git:
        spec["git"] = git
    return {
        "apiVersion": API_VERSION,
        "kind": "project",
        "metadata": metadata,
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# Envelope → file tree (write) and file tree → envelope (read)
# ---------------------------------------------------------------------------


def _query_files_fallback(rid: str, name: str, config: dict[str, Any]) -> list[dict[str, str]]:
    """Self-contained query serializer mirroring env_sync.serialize_version_files."""
    meta = {
        "id": rid,
        "name": name or "",
        "config": {k: v for k, v in config.items() if k not in ("sql", "output_schema")},
    }
    items = [
        {"path": f"queries/{rid}.sql", "content": str(config.get("sql", ""))},
        {"path": f"queries/{rid}.meta.json", "content": json.dumps(meta, indent=2, sort_keys=True)},
    ]
    schema = config.get("output_schema")
    if isinstance(schema, list):
        norm = [
            {"name": str(s["name"]), "type": str(s.get("type") or "text")}
            for s in schema
            if isinstance(s, dict) and s.get("name") is not None
        ]
        items.append(
            {"path": f"queries/{rid}.json", "content": json.dumps(norm, indent=2, sort_keys=True)}
        )
    return items


def envelope_to_files(env: dict[str, Any], *, prefer_slug: bool = True) -> list[dict[str, str]]:
    """Serialize a portable envelope to ``[{path, content}]`` file items.

    Dashboards/queries/flows reuse the backend serializer when importable so the
    on-disk shape stays identical to the in-app git file-view; otherwise a
    self-contained fallback produces the same layout.  The local format prefers
    human slugs for query/dashboard stems (doc A.102) — when *prefer_slug* the
    file stem is the slug, but the resource UUID always lives INSIDE the file.
    """
    kind = env.get("kind")
    meta = env.get("metadata") or {}
    spec = env.get("spec") or {}
    rid = str(meta.get("id") or "")
    name = str(meta.get("name") or "")
    slug = slugify(name) if prefer_slug else (rid or slugify(name))

    serialize_version_files, serialize_flow_files = _backend_serializers()

    if kind == "connector":
        return [_connector_file(env)]

    if kind == "flow":
        # Flows are always the canonical <slug>__<id8> tree (id8 embedded in path).
        if serialize_flow_files is not None:
            return serialize_flow_files(rid, name, spec)
        return _flow_files_fallback(rid, name, spec)

    if kind == "query":
        config = {
            "name": name or spec.get("name") or "",
            "sql": spec.get("sql", ""),
            "params": spec.get("params") or [],
            "datastore_id": spec.get("datastore_id"),
        }
        if isinstance(spec.get("output_schema"), list):
            config["output_schema"] = spec["output_schema"]
        if serialize_version_files is not None:
            items = serialize_version_files("query", rid or slug, name, config)
        else:
            items = _query_files_fallback(rid or slug, name, config)
        if prefer_slug and rid:
            items = [_restem(it, rid, slug) for it in items]
        return items

    if kind == "dashboard":
        config = {"spec": spec}
        if serialize_version_files is not None:
            items = serialize_version_files("board", rid or slug, name, config)
        else:
            items = [
                {
                    "path": f"dashboards/{rid or slug}.json",
                    "content": json.dumps(
                        {"id": rid, "name": name, "config": config}, indent=2, sort_keys=True
                    ),
                }
            ]
        if prefer_slug and rid:
            items = [_restem(it, rid, slug) for it in items]
        return items

    raise ValueError(f"Unknown envelope kind: {kind!r}")


def _restem(item: dict[str, str], rid: str, slug: str) -> dict[str, str]:
    """Rewrite a backend-serialized path stem from ``<id>`` to ``<slug>``."""
    return {"path": item["path"].replace(f"/{rid}", f"/{slug}", 1), "content": item["content"]}


def _flow_files_fallback(rid: str, name: str, spec: dict[str, Any]) -> list[dict[str, str]]:
    """Minimal flow projection used only when the backend is not importable.

    Writes a single ``flow.toml`` carrying the whole spec as JSON inside the
    canonical ``<slug>__<id8>`` directory; round-trips via ``files_to_envelope``.
    """
    id8 = (rid or "").replace("-", "")[:8] or "00000000"
    base = f"flows/{slugify(name)}__{id8}"
    try:
        import toml  # noqa: PLC0415

        content = toml.dumps({"flow": {"name": name, "id": rid}, "spec_json": json.dumps(spec)})
    except ImportError:
        content = json.dumps({"flow": {"name": name, "id": rid}, "spec": spec}, indent=2)
    return [{"path": f"{base}/flow.toml", "content": content}]


def _connector_file(env: dict[str, Any]) -> dict[str, str]:
    """Serialize a connector envelope to ``connectors/<slug>.yaml`` (non-secret)."""
    meta = env.get("metadata") or {}
    spec = env.get("spec") or {}
    name = str(meta.get("name") or "")
    slug = slugify(name)
    doc = {
        "apiVersion": API_VERSION,
        "kind": "connector",
        "metadata": {"name": name, **({"id": str(meta["id"])} if meta.get("id") else {})},
        "spec": spec,
    }
    try:
        import yaml  # noqa: PLC0415

        content = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    except ImportError:
        content = json.dumps(doc, indent=2)
    return {"path": f"connectors/{slug}.yaml", "content": content}


def write_files(root: Path, items: list[dict[str, str]]) -> list[Path]:
    """Write ``[{path, content}]`` items under *root*; return the written paths."""
    written: list[Path] = []
    for item in items:
        fp = root / item["path"]
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(item["content"], encoding="utf-8")
        written.append(fp)
    return written


# ---------------------------------------------------------------------------
# Read: file tree → envelopes (accepts id-named AND slug-named files)
# ---------------------------------------------------------------------------


def _load_yaml_or_json(text: str) -> Any:
    try:
        import yaml  # noqa: PLC0415

        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)


def read_connector(path: Path) -> dict[str, Any]:
    """Read a ``connectors/<slug>.yaml`` file into a connector envelope dict."""
    data = _load_yaml_or_json(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: not a connector document")
    return {
        "kind": "connector",
        "apiVersion": data.get("apiVersion", API_VERSION),
        "metadata": data.get("metadata") or {},
        "spec": data.get("spec") or {},
    }


def read_dashboard(path: Path) -> dict[str, Any]:
    """Read a ``dashboards/<stem>.json`` file into a dashboard envelope."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    config = doc.get("config") or {}
    spec = config.get("spec") if isinstance(config, dict) and isinstance(config.get("spec"), dict) else config
    meta: dict[str, Any] = {"name": doc.get("name") or ""}
    if doc.get("id"):
        meta["id"] = str(doc["id"])
    return {"kind": "dashboard", "apiVersion": API_VERSION, "metadata": meta, "spec": spec or {}}


def read_query(meta_path: Path) -> dict[str, Any]:
    """Read a query from its ``.meta.json`` (+ sibling ``.sql``/``.schema.json``).

    Accepts both id-named and slug-named stems: the sibling files are resolved
    by stripping ``.meta.json`` and re-suffixing, so the on-disk stem is opaque.
    """
    stem = meta_path.name[: -len(".meta.json")]
    folder = meta_path.parent
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    config = dict(meta.get("config") or {})

    sql_path = folder / f"{stem}.sql"
    config["sql"] = sql_path.read_text(encoding="utf-8") if sql_path.exists() else config.get("sql", "")

    # output-shape sidecar: backend writes <stem>.json; the doc names it
    # <stem>.schema.json — accept either.
    for cand in (folder / f"{stem}.schema.json", folder / f"{stem}.json"):
        if cand.exists():
            try:
                schema = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(schema, list):
                    config["output_schema"] = schema
            except json.JSONDecodeError:
                pass
            break

    meta_out: dict[str, Any] = {"name": meta.get("name") or config.get("name") or ""}
    if meta.get("id"):
        meta_out["id"] = str(meta["id"])
    spec = {
        "name": meta_out["name"],
        "sql": config.get("sql", ""),
        "params": config.get("params") or [],
        "datastore_id": config.get("datastore_id"),
    }
    if isinstance(config.get("output_schema"), list):
        spec["output_schema"] = config["output_schema"]
    return {"kind": "query", "apiVersion": API_VERSION, "metadata": meta_out, "spec": spec}


def read_flow_dir(flow_dir: Path) -> dict[str, Any]:
    """Read a ``flows/<slug>__<id8>/`` directory into a flow envelope.

    Reuses the backend ``load_flow_files`` when importable (lossless per-cell
    reconstruction); falls back to the ``spec_json`` written by the standalone
    fallback serializer.
    """
    rel_files: dict[str, str] = {}
    for fp in flow_dir.rglob("*"):
        if fp.is_file():
            rel_files[str(fp.relative_to(flow_dir))] = fp.read_text(encoding="utf-8")
    manifest_text = rel_files.get("flow.toml")
    if manifest_text is None:
        raise ValueError(f"{flow_dir.name}: flow.toml missing")

    real_id = ""
    name = ""
    try:
        import toml  # noqa: PLC0415

        manifest = toml.loads(manifest_text)
        fmeta = manifest.get("flow") or {}
        real_id = str(fmeta.get("id") or "")
        name = str(fmeta.get("name") or "")
        if "spec_json" in manifest:  # standalone fallback
            spec = json.loads(manifest["spec_json"])
            return _flow_env(spec, real_id, name)
    except ImportError:
        manifest = json.loads(manifest_text)
        fmeta = manifest.get("flow") or {}
        real_id = str(fmeta.get("id") or "")
        name = str(fmeta.get("name") or "")
        if "spec" in manifest:
            return _flow_env(manifest["spec"], real_id, name)

    # Backend path: lossless reconstruction.
    serialize_version_files, _ = _backend_serializers()
    if serialize_version_files is not None:
        from app.git.flow_files import load_flow_files  # noqa: PLC0415

        spec = load_flow_files(rel_files)
        return _flow_env(spec, real_id or "", str(spec.get("name") or name))
    raise ValueError(f"{flow_dir.name}: cannot parse flow without backend or spec_json")


def _flow_env(spec: dict[str, Any], rid: str, name: str) -> dict[str, Any]:
    meta: dict[str, Any] = {"name": name or spec.get("name") or ""}
    if rid:
        meta["id"] = rid
    return {"kind": "flow", "apiVersion": API_VERSION, "metadata": meta, "spec": spec}


def read_all(root: Path, kinds: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Read the whole file tree into ``{kind: [envelope, ...]}``.

    Accepts id-named AND slug-named files for every kind (doc A note).
    """
    wanted = set(kinds) if kinds else set(KIND_FOLDER)
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in wanted}

    if "connector" in wanted and (root / "connectors").is_dir():
        for fp in sorted((root / "connectors").glob("*.yaml")):
            out["connector"].append(read_connector(fp))
    if "dashboard" in wanted and (root / "dashboards").is_dir():
        for fp in sorted((root / "dashboards").glob("*.json")):
            out["dashboard"].append(read_dashboard(fp))
    if "query" in wanted and (root / "queries").is_dir():
        for fp in sorted((root / "queries").glob("*.meta.json")):
            out["query"].append(read_query(fp))
    if "flow" in wanted and (root / "flows").is_dir():
        for d in sorted((root / "flows").iterdir()):
            if d.is_dir() and (d / "flow.toml").exists():
                out["flow"].append(read_flow_dir(d))
    return out
