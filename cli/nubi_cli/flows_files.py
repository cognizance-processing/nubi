"""Flows-as-files: load/dump FlowSpec to/from YAML or JSON on disk.

This module bridges the on-disk YAML/JSON representation of a flow
(suitable for version control) and the runtime FlowSpec dict validated
by ``app.flows.spec.validate_flow_spec``.

Public API
----------
load_flow_file(path) -> dict
    Read a ``.yaml``, ``.yml``, or ``.json`` file and return the parsed
    FlowSpec dict after validation.  Raises ``FlowFileError`` on any
    hard validation error.

dump_flow(spec, path) -> None
    Serialise a FlowSpec dict to *path*.  Extension determines format:
    ``.yaml``/``.yml`` → YAML; anything else → JSON.

FlowFileError
    Raised when loading fails due to a parse error or a hard FlowSpec
    validation error.

Notes
-----
- PyYAML is preferred for YAML files (``pyyaml``).  If it is not
  installed the module falls back to JSON-only mode and raises a
  ``FlowFileError`` for YAML inputs.
- Validation is performed by importing
  ``app.flows.spec.validate_flow_spec`` with a ``sys.path`` adjustment
  so the backend package is importable from the CLI environment.
  If the backend is unavailable, a lightweight structural check is
  performed instead (ensures ``name`` and ``tasks`` keys exist).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class FlowFileError(Exception):
    """Raised when a flow file cannot be loaded or fails validation."""


# ---------------------------------------------------------------------------
# Backend import helpers (lazy, with sys.path adjustment)
# ---------------------------------------------------------------------------


def _ensure_backend_on_path() -> bool:
    """Add the backend directory to sys.path if it is not already present.

    Returns True if the backend package appears importable after the
    adjustment, False otherwise.
    """
    # Try to find the backend directory relative to this file's location.
    # cli/nubi_cli/flows_files.py → climb two levels → nubi root → backend.
    candidates = [
        Path(__file__).parent.parent.parent / "backend",  # dev checkout layout
    ]
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "app" / "flows" / "spec.py").exists():
            backend_str = str(candidate)
            if backend_str not in sys.path:
                sys.path.insert(0, backend_str)
            return True
    return False


def _validate_via_backend(data: dict[str, Any]) -> tuple[Any, list[str]]:
    """Call app.flows.spec.validate_flow_spec after ensuring backend is on path.

    Returns (spec_or_none, issues) mirroring the backend function signature.
    Raises ImportError when the backend package is not importable.
    """
    _ensure_backend_on_path()
    from app.flows.spec import validate_flow_spec  # noqa: PLC0415

    return validate_flow_spec(data)


def _validate_lightweight(data: dict[str, Any]) -> list[str]:
    """Minimal structural validation used when the backend is not importable.

    Checks that the required top-level keys are present and that each task
    has at minimum a ``key`` and ``kind`` field.
    """
    issues: list[str] = []
    if not isinstance(data, dict):
        issues.append("Flow spec must be a JSON/YAML object (dict).")
        return issues
    if not data.get("name"):
        issues.append("Field 'name' is required.")
    tasks = data.get("tasks")
    if tasks is None:
        issues.append("Field 'tasks' is required.")
    elif not isinstance(tasks, list):
        issues.append("Field 'tasks' must be a list.")
    else:
        for i, task in enumerate(tasks):
            if not isinstance(task, dict):
                issues.append(f"Task at index {i} must be a dict.")
                continue
            if not task.get("key"):
                issues.append(f"Task at index {i} is missing 'key'.")
            if not task.get("kind"):
                issues.append(f"Task at index {i} is missing 'kind'.")
    return issues


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _load_yaml_text(text: str) -> Any:
    """Parse YAML text, raising FlowFileError on failure."""
    try:
        import yaml  # noqa: PLC0415

        return yaml.safe_load(text)
    except ImportError:
        raise FlowFileError(
            "PyYAML is not installed.  Install it with: pip install pyyaml"
        )
    except Exception as exc:
        raise FlowFileError(f"YAML parse error: {exc}") from exc


def _dump_yaml_text(data: Any) -> str:
    """Serialise *data* to YAML text."""
    try:
        import yaml  # noqa: PLC0415

        return yaml.dump(data, allow_unicode=True, sort_keys=False)
    except ImportError:
        raise FlowFileError(
            "PyYAML is not installed.  Install it with: pip install pyyaml"
        )


# ---------------------------------------------------------------------------
# load_flow_file
# ---------------------------------------------------------------------------


def load_flow_file(path: str | Path) -> dict[str, Any]:
    """Read a flow file from disk and return a validated FlowSpec dict.

    Supports ``.yaml``, ``.yml`` (PyYAML), and ``.json`` files.

    Parameters
    ----------
    path:
        Path to the flow file.

    Returns
    -------
    dict
        The parsed and validated FlowSpec dict.

    Raises
    ------
    FlowFileError
        If the file cannot be read, cannot be parsed, or fails hard
        FlowSpec validation.
    FileNotFoundError
        If *path* does not exist.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Flow file not found: {path}")

    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    # ── Parse ────────────────────────────────────────────────────────────────
    if suffix in (".yaml", ".yml"):
        data = _load_yaml_text(text)
    else:
        # Default: try JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FlowFileError(f"JSON parse error in {path.name}: {exc}") from exc

    if not isinstance(data, dict):
        raise FlowFileError(
            f"{path.name} did not parse to a dict — got {type(data).__name__}."
        )

    # ── Validate ─────────────────────────────────────────────────────────────
    try:
        _, issues = _validate_via_backend(data)
        # Separate hard errors from soft warnings.
        hard = [i for i in issues if not i.startswith("[warn]")]
        if hard:
            raise FlowFileError(
                f"Flow spec in {path.name} has validation errors:\n"
                + "\n".join(f"  - {e}" for e in hard)
            )
    except ImportError:
        # Backend not importable — fall back to lightweight checks.
        issues = _validate_lightweight(data)
        if issues:
            raise FlowFileError(
                f"Flow spec in {path.name} has validation errors:\n"
                + "\n".join(f"  - {e}" for e in issues)
            )

    return data


# ---------------------------------------------------------------------------
# dump_flow
# ---------------------------------------------------------------------------


def dump_flow(spec: dict[str, Any], path: str | Path) -> None:
    """Write a FlowSpec dict to *path*.

    The file format is chosen by the file extension:

    - ``.yaml`` or ``.yml`` → YAML (requires pyyaml).
    - Any other extension → JSON (pretty-printed, 2-space indent).

    Parameters
    ----------
    spec:
        FlowSpec dict (as returned by ``load_flow_file`` or the API).
    path:
        Destination file path.  Parent directories are created if absent.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()

    if suffix in (".yaml", ".yml"):
        text = _dump_yaml_text(spec)
    else:
        text = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"

    path.write_text(text, encoding="utf-8")
