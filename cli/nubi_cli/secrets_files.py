"""Local secret-file model (files-as-code, doc Section B).

Two gitignored dotenv files live under ``.nubi/secrets/``:

- ``connectors.env`` — connector secrets, ``<CONNECTOR_SLUG>__<FIELD>=value``
  upper-snake (doc B.186).
- ``flow.env`` — org/flow secrets (``{{ secrets.NAME }}`` values, doc B.198).

The CLI loads ``flow.env`` (+ ``NUBI_SECRET_<NAME>`` env vars) and injects them
through the runtime secret seam exactly as ``main._patch_secrets_store`` does,
just sourced from the project tree instead of ``~/.nubi/secrets``.

``secrets materialize`` (doc C.342) expands ``NUBI_SECRET__*`` /
``NUBI_CONNECTOR__*`` env vars into these files with NO backend call.
"""

from __future__ import annotations

import re
from pathlib import Path

# Env-var prefixes the pipeline injects (doc C.283-284).
FLOW_PREFIX = "NUBI_SECRET__"
CONNECTOR_PREFIX = "NUBI_CONNECTOR__"


def secrets_dir(root: Path) -> Path:
    return root / ".nubi" / "secrets"


def connectors_env_path(root: Path) -> Path:
    return secrets_dir(root) / "connectors.env"


def flow_env_path(root: Path) -> Path:
    return secrets_dir(root) / "flow.env"


def connector_key(slug: str, field: str) -> str:
    """``<CONNECTOR_SLUG>__<FIELD>`` upper-snake (doc B.186)."""
    norm = lambda s: re.sub(r"[^A-Z0-9]+", "_", s.upper()).strip("_")  # noqa: E731
    return f"{norm(slug)}__{norm(field)}"


# ---------------------------------------------------------------------------
# dotenv read/write (minimal, dependency-free)
# ---------------------------------------------------------------------------


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines; ignores blanks and ``#`` comments."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value
    return out


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_dotenv(path.read_text(encoding="utf-8"))


def write_dotenv(path: Path, values: dict[str, str], *, header: str | None = None) -> None:
    """Write a dotenv file with deterministic (sorted) key order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if header:
        lines.append(f"# {header}")
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def upsert_dotenv(path: Path, key: str, value: str, *, header: str | None = None) -> None:
    """Set a single key in a dotenv file, preserving the other entries."""
    values = read_dotenv(path)
    values[key] = value
    write_dotenv(path, values, header=header)


# ---------------------------------------------------------------------------
# materialize: NUBI_SECRET__* / NUBI_CONNECTOR__* env -> .env files
# ---------------------------------------------------------------------------


def materialize(root: Path, environ: dict[str, str]) -> dict[str, int]:
    """Expand prefixed env vars into the two ``.env`` files (doc C.283).

    - ``NUBI_SECRET__<NAME>``            → ``flow.env``  as ``<NAME>``.
    - ``NUBI_CONNECTOR__<SLUG>__<FIELD>``→ ``connectors.env`` as
      ``<SLUG>__<FIELD>`` (the connector prefix is simply stripped).

    Returns ``{"flow": n_flow, "connector": n_connector}``.  No backend call.
    """
    flow: dict[str, str] = {}
    connector: dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith(CONNECTOR_PREFIX):
            connector[key[len(CONNECTOR_PREFIX):]] = value
        elif key.startswith(FLOW_PREFIX):
            flow[key[len(FLOW_PREFIX):]] = value

    # Merge with any existing values so re-runs are additive/idempotent.
    if flow:
        existing = read_dotenv(flow_env_path(root))
        existing.update(flow)
        write_dotenv(flow_env_path(root), existing, header="Nubi flow/org secrets (materialized)")
    if connector:
        existing = read_dotenv(connectors_env_path(root))
        existing.update(connector)
        write_dotenv(
            connectors_env_path(root), existing, header="Nubi connector secrets (materialized)"
        )
    return {"flow": len(flow), "connector": len(connector)}


def load_flow_secrets(root: Path, environ: dict[str, str]) -> dict[str, str]:
    """Flow secrets for a local run: ``flow.env`` overlaid by ``NUBI_SECRET_*``.

    ``NUBI_SECRET_<NAME>`` env vars override the file (matches the existing
    ``main.flows_run`` precedence). Note this is the SINGLE-underscore runtime
    convention, distinct from the double-underscore materialize prefix.
    """
    out = read_dotenv(flow_env_path(root))
    for key, value in environ.items():
        if key.startswith("NUBI_SECRET_") and not key.startswith(FLOW_PREFIX):
            out[key[len("NUBI_SECRET_"):]] = value
    return out
