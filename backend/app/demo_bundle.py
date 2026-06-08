"""Shared demo-content loader — the single source of truth for demo dashboards.

The demo workspace (queries + dashboards) is defined declaratively as JSON files
under ``backend/seed_data/demo/`` so the content lives in versioned files rather
than hardcoded Python:

  - ``queries.json`` : ``{logical_key: {name, sql, params}}``
  - ``boards.json``  : ``[{seed_id, name, starter, spec}, ...]`` where every widget
    references a query by the ``"@<logical_key>"`` placeholder (resolved to the
    real query UUID at seed time).

Two consumers share this loader:
  - ``seed.py --demo`` (superuser): materialises ALL boards (comprehensive demo).
  - ``app/sample.py`` (per project): materialises only the ``starter`` boards as a
    small, editable, removable onboarding bundle.

Both reuse the bundled read-only DuckDB star schema (``seed_data/sample.duckdb``,
built by ``seed_data_duckdb.build_duckdb_file``) as the single datasource.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEMO_DIR = Path(__file__).resolve().parent.parent / "seed_data" / "demo"


def load_queries() -> dict[str, dict[str, Any]]:
    """Return ``{logical_key: {name, sql, params}}`` from queries.json."""
    with open(_DEMO_DIR / "queries.json") as f:
        return json.load(f)


def load_boards(starter_only: bool = False) -> list[dict[str, Any]]:
    """Return board fixtures ``[{seed_id, name, starter, spec}, ...]``."""
    with open(_DEMO_DIR / "boards.json") as f:
        boards = json.load(f)
    return [b for b in boards if b.get("starter")] if starter_only else boards


def referenced_query_keys(boards: list[dict[str, Any]]) -> list[str]:
    """Logical query keys referenced by the given boards (via ``@key`` placeholders)."""
    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("@"):
                keys.add(node[1:])
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for b in boards:
        walk(b["spec"])
    return sorted(keys)


def resolve_placeholders(spec: Any, idmap: dict[str, str]) -> Any:
    """Deep-copy *spec*, replacing every ``"@key"`` string with ``idmap["@key"]``.

    Unknown placeholders resolve to ``""`` (an empty query_id renders as a
    "needs configuration" widget rather than crashing).
    """
    if isinstance(spec, str):
        return idmap.get(spec, "") if spec.startswith("@") else spec
    if isinstance(spec, dict):
        return {k: resolve_placeholders(v, idmap) for k, v in spec.items()}
    if isinstance(spec, list):
        return [resolve_placeholders(v, idmap) for v in spec]
    return spec


def sample_db_path() -> str:
    """Absolute path to the bundled demo DuckDB file, building it if missing."""
    from seed_data_duckdb import SAMPLE_DB_PATH, build_duckdb_file

    path = os.path.abspath(SAMPLE_DB_PATH)
    if not os.path.exists(path):
        build_duckdb_file(path)
    return path


def datastore_config(db_path: str) -> dict[str, Any]:
    """Connector config for the read-only DuckDB demo datasource."""
    return {
        "type": "duckdb",
        "database": db_path,
        "read_only": True,
        "description": "Bundled demo dataset (read-only FMCG sales star schema).",
    }
