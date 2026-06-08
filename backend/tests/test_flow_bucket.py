"""Tests for the ``bucket_load`` task handler.

Coverage
--------
1. CSV format — row-shaped upstream result written to a ``file://`` path;
   file exists and round-trips correctly.
2. JSON format — same as CSV but using JSON serialisation.
3. NDJSON format — rows written as newline-delimited JSON.
4. Append mode — existing CSV is merged with new rows.
5. Raw-bytes upstream — bytes written verbatim.
6. List-of-dicts upstream — treated as rows.
7. Unknown upstream shape — JSON fallback.
8. Missing ``uri`` config → ValueError.
9. Missing ``source`` config → ValueError.
10. Invalid format → ValueError.
11. Invalid mode → ValueError.
12. Missing source key in ctx.inputs → KeyError.
13. Secret resolution — creds JSON is parsed from ctx.secrets.
14. Missing secret → ValueError.
15. Bad secret JSON → ValueError.
16. Return dict has the correct keys and values.
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.executor import TaskContext
from app.flows.handlers.bucket import handle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    inputs: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
) -> TaskContext:
    """Return a TaskContext with the given inputs and secrets."""
    ctx = TaskContext(
        flow_params={},
        inputs=inputs or {},
        now=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    # TaskContext gains a ``secrets`` field per the shared contract.
    # Assign directly so the test works even if the dataclass default is
    # an empty dict defined elsewhere.
    ctx.secrets = secrets or {}
    return ctx


SAMPLE_ROWS = [
    {"id": 1, "name": "alpha", "value": 10.0},
    {"id": 2, "name": "beta", "value": 20.0},
    {"id": 3, "name": "gamma", "value": 30.0},
]

UPSTREAM_ROWS = {"rows": SAMPLE_ROWS, "columns": ["id", "name", "value"], "row_count": 3}


def _file_uri(tmp_path, filename: str) -> str:
    """Return a ``file://`` URI pointing at *filename* inside *tmp_path*."""
    root = str(tmp_path)
    return f"file://{root}/{filename}"


# ---------------------------------------------------------------------------
# 1. CSV format
# ---------------------------------------------------------------------------


def test_csv_round_trip(tmp_path):
    """CSV rows written to file:// path round-trip correctly."""
    uri = _file_uri(tmp_path, "output.csv")
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS})

    result = handle(
        {"uri": uri, "source": "q1", "format": "csv", "mode": "overwrite"},
        ctx,
        claims={},
    )

    assert result["format"] == "csv"
    assert result["row_count"] == 3
    assert result["bytes_written"] > 0
    assert result["uri"].startswith("file://")
    assert result["uri"].endswith("output.csv")

    # Round-trip: read back and verify rows.
    dest_file = os.path.join(str(tmp_path), "output.csv")
    assert os.path.isfile(dest_file), "CSV file must exist after handle()"

    with open(dest_file, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        loaded = list(reader)

    assert len(loaded) == 3
    assert loaded[0]["name"] == "alpha"
    assert loaded[2]["name"] == "gamma"


# ---------------------------------------------------------------------------
# 2. JSON format
# ---------------------------------------------------------------------------


def test_json_round_trip(tmp_path):
    """JSON rows written to file:// path round-trip correctly."""
    uri = _file_uri(tmp_path, "output.json")
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS})

    result = handle(
        {"uri": uri, "source": "q1", "format": "json", "mode": "overwrite"},
        ctx,
        claims={},
    )

    assert result["format"] == "json"
    assert result["row_count"] == 3

    dest_file = os.path.join(str(tmp_path), "output.json")
    assert os.path.isfile(dest_file), "JSON file must exist after handle()"

    with open(dest_file, encoding="utf-8") as fh:
        loaded = json.load(fh)

    assert isinstance(loaded, list)
    assert len(loaded) == 3
    assert loaded[1]["name"] == "beta"


# ---------------------------------------------------------------------------
# 3. NDJSON format
# ---------------------------------------------------------------------------


def test_ndjson_round_trip(tmp_path):
    """NDJSON rows are written as one JSON object per line."""
    uri = _file_uri(tmp_path, "output.ndjson")
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS})

    result = handle(
        {"uri": uri, "source": "q1", "format": "ndjson", "mode": "overwrite"},
        ctx,
        claims={},
    )

    assert result["format"] == "ndjson"
    assert result["row_count"] == 3

    dest_file = os.path.join(str(tmp_path), "output.ndjson")
    assert os.path.isfile(dest_file)

    lines = dest_file and open(dest_file, encoding="utf-8").read().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["id"] == 1


# ---------------------------------------------------------------------------
# 4. Append mode (CSV)
# ---------------------------------------------------------------------------


def test_csv_append_mode(tmp_path):
    """Append mode merges existing rows with new rows."""
    uri = _file_uri(tmp_path, "append.csv")
    ctx_first = _make_ctx(inputs={"q1": {"rows": SAMPLE_ROWS[:1], "columns": ["id", "name", "value"]}})
    ctx_second = _make_ctx(inputs={"q1": {"rows": SAMPLE_ROWS[1:], "columns": ["id", "name", "value"]}})

    # First write (overwrite).
    handle({"uri": uri, "source": "q1", "format": "csv", "mode": "overwrite"}, ctx_first, {})

    # Second write (append).
    result = handle({"uri": uri, "source": "q1", "format": "csv", "mode": "append"}, ctx_second, {})

    assert result["row_count"] == 3  # 1 existing + 2 new

    dest_file = os.path.join(str(tmp_path), "append.csv")
    with open(dest_file, newline="", encoding="utf-8") as fh:
        loaded = list(csv.DictReader(fh))

    assert len(loaded) == 3
    names = [r["name"] for r in loaded]
    assert "alpha" in names
    assert "beta" in names
    assert "gamma" in names


# ---------------------------------------------------------------------------
# 5. Raw-bytes upstream
# ---------------------------------------------------------------------------


def test_raw_bytes_upstream(tmp_path):
    """Bytes upstream payload is written verbatim."""
    payload = b"\x00\x01\x02\x03 binary data"
    uri = _file_uri(tmp_path, "raw.bin")
    ctx = _make_ctx(inputs={"src": {"bytes": payload}})

    result = handle(
        {"uri": uri, "source": "src", "format": "csv", "mode": "overwrite"},
        ctx,
        claims={},
    )

    assert result["bytes_written"] == len(payload)
    dest_file = os.path.join(str(tmp_path), "raw.bin")
    assert open(dest_file, "rb").read() == payload


# ---------------------------------------------------------------------------
# 6. List-of-dicts upstream
# ---------------------------------------------------------------------------


def test_list_of_dicts_upstream(tmp_path):
    """A plain list-of-dicts is treated as rows."""
    uri = _file_uri(tmp_path, "list.json")
    ctx = _make_ctx(inputs={"src": SAMPLE_ROWS})  # list, not dict

    result = handle(
        {"uri": uri, "source": "src", "format": "json", "mode": "overwrite"},
        ctx,
        claims={},
    )

    assert result["row_count"] == 3
    dest_file = os.path.join(str(tmp_path), "list.json")
    loaded = json.loads(open(dest_file, encoding="utf-8").read())
    assert len(loaded) == 3


# ---------------------------------------------------------------------------
# 7. Unknown upstream shape → JSON fallback
# ---------------------------------------------------------------------------


def test_unknown_upstream_shape(tmp_path):
    """Unknown upstream shape is serialised as JSON."""
    uri = _file_uri(tmp_path, "misc.json")
    ctx = _make_ctx(inputs={"src": {"something_weird": 42}})

    result = handle(
        {"uri": uri, "source": "src", "format": "csv", "mode": "overwrite"},
        ctx,
        claims={},
    )

    dest_file = os.path.join(str(tmp_path), "misc.json")
    assert os.path.isfile(dest_file)
    loaded = json.loads(open(dest_file, encoding="utf-8").read())
    assert loaded["something_weird"] == 42


# ---------------------------------------------------------------------------
# 8. Missing uri → ValueError
# ---------------------------------------------------------------------------


def test_missing_uri_raises():
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS})
    with pytest.raises(ValueError, match="uri"):
        handle({"source": "q1", "format": "csv"}, ctx, {})


# ---------------------------------------------------------------------------
# 9. Missing source → ValueError
# ---------------------------------------------------------------------------


def test_missing_source_raises():
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS})
    with pytest.raises(ValueError, match="source"):
        handle({"uri": "file:///tmp/x/out.csv", "format": "csv"}, ctx, {})


# ---------------------------------------------------------------------------
# 10. Invalid format → ValueError
# ---------------------------------------------------------------------------


def test_invalid_format_raises(tmp_path):
    uri = _file_uri(tmp_path, "out.xml")
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS})
    with pytest.raises(ValueError, match="format"):
        handle({"uri": uri, "source": "q1", "format": "xml"}, ctx, {})


# ---------------------------------------------------------------------------
# 11. Invalid mode → ValueError
# ---------------------------------------------------------------------------


def test_invalid_mode_raises(tmp_path):
    uri = _file_uri(tmp_path, "out.csv")
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS})
    with pytest.raises(ValueError, match="mode"):
        handle({"uri": uri, "source": "q1", "format": "csv", "mode": "truncate"}, ctx, {})


# ---------------------------------------------------------------------------
# 12. Missing source key in ctx.inputs → KeyError
# ---------------------------------------------------------------------------


def test_missing_source_key_raises(tmp_path):
    uri = _file_uri(tmp_path, "out.csv")
    ctx = _make_ctx(inputs={})
    with pytest.raises(KeyError, match="q1"):
        handle({"uri": uri, "source": "q1", "format": "csv"}, ctx, {})


# ---------------------------------------------------------------------------
# 13. Secret resolution — creds JSON parsed from ctx.secrets
# ---------------------------------------------------------------------------


def test_secret_resolution_file_backend(tmp_path):
    """Secret creds are resolved and (for file://) have no effect on the upload."""
    uri = _file_uri(tmp_path, "secret_test.csv")
    creds_json = json.dumps({"some_key": "some_value"})
    ctx = _make_ctx(
        inputs={"q1": UPSTREAM_ROWS},
        secrets={"my_storage_secret": creds_json},
    )

    result = handle(
        {
            "uri": uri,
            "source": "q1",
            "format": "csv",
            "secret": "my_storage_secret",
        },
        ctx,
        claims={},
    )

    assert result["row_count"] == 3
    dest_file = os.path.join(str(tmp_path), "secret_test.csv")
    assert os.path.isfile(dest_file)


# ---------------------------------------------------------------------------
# 14. Missing secret → ValueError
# ---------------------------------------------------------------------------


def test_missing_secret_raises(tmp_path):
    uri = _file_uri(tmp_path, "out.csv")
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS}, secrets={})
    with pytest.raises(ValueError, match="not found in ctx.secrets"):
        handle({"uri": uri, "source": "q1", "format": "csv", "secret": "ghost_secret"}, ctx, {})


# ---------------------------------------------------------------------------
# 15. Bad secret JSON → ValueError
# ---------------------------------------------------------------------------


def test_bad_secret_json_raises(tmp_path):
    uri = _file_uri(tmp_path, "out.csv")
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS}, secrets={"bad": "not-json{"})
    with pytest.raises(ValueError, match="not valid JSON"):
        handle({"uri": uri, "source": "q1", "format": "csv", "secret": "bad"}, ctx, {})


# ---------------------------------------------------------------------------
# 16. Return dict keys and types
# ---------------------------------------------------------------------------


def test_return_dict_shape(tmp_path):
    """Result dict contains exactly the documented keys with correct types."""
    uri = _file_uri(tmp_path, "shape.csv")
    ctx = _make_ctx(inputs={"q1": UPSTREAM_ROWS})

    result = handle(
        {"uri": uri, "source": "q1", "format": "csv", "mode": "overwrite"},
        ctx,
        claims={},
    )

    assert set(result.keys()) >= {"uri", "format", "row_count", "bytes_written"}
    assert isinstance(result["uri"], str)
    assert isinstance(result["format"], str)
    assert isinstance(result["row_count"], int)
    assert isinstance(result["bytes_written"], int)
    assert result["bytes_written"] > 0
