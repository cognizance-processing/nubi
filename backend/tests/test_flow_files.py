"""Flows-as-files round-trip — serialize_flow_files / load_flow_files (A3)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

from app.git.flow_files import (
    flow_dir,
    load_flow_files,
    serialize_flow_files,
    slugify,
)


def _spec() -> dict:
    return {
        "version": 1,
        "name": "Daily Revenue",
        "params": [{"name": "region", "type": "text", "default": "us", "required": False}],
        "tasks": [
            {
                "key": "pull",
                "kind": "query",
                "cell_type": "sql",
                "needs": [],
                "config": {"sql": "SELECT * FROM orders", "datastore_id": "ds-1"},
                "retries": 2,
                "retry_backoff_s": 30,
                "timeout_s": 60,
                "cache_ttl_s": 0,
                "ui": {"x": 10, "y": 20},
            },
            {
                "key": "transform",
                "kind": "python",
                "cell_type": "python",
                "needs": ["pull"],
                "config": {"code": "result = {'rows': inputs['pull']}"},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 60,
                "cache_ttl_s": 0,
                "ui": {"x": 200, "y": 20},
            },
            {
                "key": "note",
                "kind": "noop",
                "cell_type": "markdown",
                "needs": [],
                "config": {"markdown": "# Heading\n\nExplanatory text."},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 60,
                "cache_ttl_s": 0,
            },
        ],
    }


def _as_relmap(files: list[dict], base: str) -> dict[str, str]:
    prefix = base + "/"
    return {f["path"][len(prefix):]: f["content"] for f in files}


def test_slug_and_dir():
    assert slugify("Daily Revenue!") == "daily-revenue"
    assert slugify("") == "flow"
    assert flow_dir("abcd1234-ef56", "Daily Revenue") == "flows/daily-revenue__abcd1234"


def test_serialize_emits_per_cell_files_with_padded_order():
    spec = _spec()
    files = serialize_flow_files("abcd1234-ef56", spec["name"], spec)
    paths = {f["path"] for f in files}
    base = "flows/daily-revenue__abcd1234"
    assert f"{base}/flow.toml" in paths
    assert f"{base}/cells/01_pull.sql" in paths
    assert f"{base}/cells/02_transform.py" in paths
    assert f"{base}/cells/03_note.md" in paths
    # Source lives ONLY in the sidecar, not duplicated into flow.toml.
    toml_text = next(f["content"] for f in files if f["path"].endswith("flow.toml"))
    assert "SELECT * FROM orders" not in toml_text
    # ...but the non-source config key is preserved in flow.toml.
    assert "ds-1" in toml_text
    sql = next(f["content"] for f in files if f["path"].endswith("01_pull.sql"))
    assert sql == "SELECT * FROM orders"


def test_round_trip_is_lossless():
    spec = _spec()
    base = flow_dir("abcd1234-ef56", spec["name"])
    files = serialize_flow_files("abcd1234-ef56", spec["name"], spec)
    rebuilt = load_flow_files(_as_relmap(files, base))

    # Validate via the canonical model on both sides for a normalised compare.
    from app.flows.spec import validate_flow_spec

    orig, _ = validate_flow_spec(spec)
    back, _ = validate_flow_spec(rebuilt)
    assert orig is not None and back is not None
    assert back.model_dump() == orig.model_dump()


def test_load_missing_manifest_raises():
    import pytest

    with pytest.raises(ValueError):
        load_flow_files({"cells/01_pull.sql": "SELECT 1"})
