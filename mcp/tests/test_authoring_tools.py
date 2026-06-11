"""Tests for the full dashboard-authoring MCP tool loop.

Covers the seven authoring tools added on top of the original six:

    get_context        — the DISCOVER step (queries + conventions)
    get_spec_schema    — the CONTRACT (DashboardSpec JSON Schema)
    validate_spec      — the REPAIR step (structured {valid, errors, warnings})
    estimate_query     — dry-run scan/cost estimate (no execution)
    preview_widget     — row-limited result preview (reuses run_query's path)
    upsert_dashboard   — the GOVERNED write (validates first; refuses invalid)
    promote            — the HUMAN-GATE dev -> prod pointer copy

Strategy
--------
- Use InMemoryRepo (set_repo) + InMemoryEnvStore (set_env_store) so no live
  database is needed — the same injection pattern as test_dashboard_tools.py.
- All tests exercise the *plain* tool-logic functions directly (no MCP
  transport), mirroring test_tools.py / test_dashboard_tools.py.

Path bootstrap + env vars match the sibling test modules so app.* imports work.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap — backend must be importable.
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).resolve().parent        # mcp/tests/
_MCP_DIR = _TESTS_DIR.parent                         # mcp/
_NUBI_ROOT = _MCP_DIR.parent                         # nubi/
_BACKEND_DIR = _NUBI_ROOT / "backend"                # nubi/backend/

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# ---------------------------------------------------------------------------
# Environment must be set before importing any app modules.
# ---------------------------------------------------------------------------

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-google-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-google-client-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")
os.environ.pop("CORS_ORIGINS", None)

import pytest

from app.repos import InMemoryRepo, set_repo
from app.environments.store import InMemoryEnvStore, set_env_store

from nubi_mcp.server import (
    _estimate_query,
    _get_context,
    _get_spec_schema,
    _preview_widget,
    _promote,
    _upsert_dashboard,
    _validate_spec,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def inject_stores():
    """Inject fresh in-memory Repo + EnvStore before each test; reset after."""
    repo = InMemoryRepo()
    env_store = InMemoryEnvStore()
    set_repo(repo)
    set_env_store(env_store)
    yield repo, env_store
    set_repo(None)
    set_env_store(None)


# ---------------------------------------------------------------------------
# Spec helpers
# ---------------------------------------------------------------------------


def _valid_spec() -> dict[str, Any]:
    """A minimal valid DashboardSpec bound to a registered query."""
    return {
        "title": "Demo Board",
        "widgets": [
            {
                "id": "w1",
                "type": "table",
                "query_id": "demo_all",
                "pos": {"x": 1, "y": 1, "w": 4, "h": 3},
            }
        ],
    }


def _invalid_spec() -> dict[str, Any]:
    """A chart widget missing its required x/y encoding — hard errors."""
    return {
        "title": "Broken Board",
        "widgets": [
            {
                "id": "w1",
                "type": "chart",
                "chart_type": "scatter",
                "query_id": "demo_points_10k",
                "pos": {"x": 1, "y": 1, "w": 4, "h": 3},
            }
        ],
    }


# ---------------------------------------------------------------------------
# get_context
# ---------------------------------------------------------------------------


class TestGetContext:
    """Tests for _get_context()."""

    def test_shape(self) -> None:
        ctx = _get_context()
        assert isinstance(ctx, dict)
        for key in ("queries", "conventions", "compact", "filtered_by"):
            assert key in ctx, f"Missing '{key}' in context: {ctx.keys()}"

    def test_queries_non_empty_with_params_and_schema(self) -> None:
        ctx = _get_context()
        assert len(ctx["queries"]) > 0
        entry = ctx["queries"][0]
        assert "id" in entry and "name" in entry
        assert "params" in entry
        assert "output_schema" in entry

    def test_conventions_present(self) -> None:
        ctx = _get_context()
        assert isinstance(ctx["conventions"], dict)
        assert ctx["conventions"], "conventions block should be non-empty"

    def test_compact_drops_verbose_fields(self) -> None:
        full = _get_context(compact=False)["queries"][0]
        compact = _get_context(compact=True)["queries"][0]
        assert "datastore" in full
        assert "datastore" not in compact
        assert _get_context(compact=True)["compact"] is True

    def test_filter_by_q_records_filter(self) -> None:
        ctx = _get_context(q="demo points")
        assert ctx["filtered_by"] == "demo points"
        # Filtering must never return more queries than the unfiltered call.
        assert len(ctx["queries"]) <= len(_get_context()["queries"])


# ---------------------------------------------------------------------------
# get_spec_schema
# ---------------------------------------------------------------------------


class TestGetSpecSchema:
    """Tests for _get_spec_schema()."""

    def test_returns_schema_dict(self) -> None:
        schema = _get_spec_schema()
        assert isinstance(schema, dict)
        # JSON Schema documents carry a properties map.
        assert "properties" in schema or "$defs" in schema

    def test_describes_dashboard_spec(self) -> None:
        schema = _get_spec_schema()
        assert schema.get("title") == "DashboardSpec"


# ---------------------------------------------------------------------------
# validate_spec
# ---------------------------------------------------------------------------


class TestValidateSpec:
    """Tests for _validate_spec()."""

    def test_valid_spec_is_valid(self) -> None:
        result = _validate_spec(_valid_spec())
        assert result["valid"] is True, f"Expected valid, got {result}"
        assert result["errors"] == []

    def test_result_shape(self) -> None:
        result = _validate_spec(_valid_spec())
        for key in ("valid", "errors", "warnings"):
            assert key in result, f"Missing '{key}' in {result}"
        assert isinstance(result["errors"], list)
        assert isinstance(result["warnings"], list)

    def test_invalid_spec_returns_structured_errors(self) -> None:
        result = _validate_spec(_invalid_spec())
        assert result["valid"] is False
        assert len(result["errors"]) >= 1, "Expected at least one error"
        codes = {e["code"] for e in result["errors"]}
        # The missing chart encoding is the canonical structured error.
        assert "missing_encoding_x" in codes, f"codes: {codes}"

    def test_structured_error_carries_path_and_keys(self) -> None:
        result = _validate_spec(_invalid_spec())
        err = next(e for e in result["errors"] if e["code"] == "missing_encoding_x")
        # JSON path tells the agent WHERE to fix.
        assert err["path"].endswith("encoding.x")
        # Every structured issue exposes the full repair contract keys (the
        # demo-seed queries declare no output_schema, so valid_options may be
        # None here — that is a valid, contract-compliant value).
        for key in ("path", "code", "message", "severity", "suggestion", "valid_options"):
            assert key in err, f"Missing '{key}' in structured issue: {err}"
        assert err["severity"] == "error"
        assert err["message"]


# ---------------------------------------------------------------------------
# estimate_query
# ---------------------------------------------------------------------------


class TestEstimateQuery:
    """Tests for _estimate_query()."""

    def test_estimate_registered_query(self) -> None:
        est = _estimate_query(query_id="demo_points_10k")
        assert est["supported"] is True
        assert est["mechanism"] == "duckdb_explain"
        assert "est_rows" in est
        assert est["connector_type"] == "duckdb"

    def test_estimate_raw_sql(self) -> None:
        est = _estimate_query(sql="SELECT 1 AS a")
        assert est["supported"] is True
        assert "est_rows" in est

    def test_unknown_query_id_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown query_id"):
            _estimate_query(query_id="nope_not_a_query")

    def test_requires_query_id_or_sql(self) -> None:
        with pytest.raises(ValueError, match="either query_id or sql"):
            _estimate_query()

    def test_rejects_both_query_id_and_sql(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            _estimate_query(query_id="demo_all", sql="SELECT 1")


# ---------------------------------------------------------------------------
# preview_widget
# ---------------------------------------------------------------------------


class TestPreviewWidget:
    """Tests for _preview_widget()."""

    def test_preview_shape(self) -> None:
        result = _preview_widget("demo_points_10k", limit=5)
        assert set(result.keys()) >= {"columns", "rows", "row_count"}

    def test_limit_caps_rows(self) -> None:
        result = _preview_widget("demo_points_10k", limit=4)
        assert len(result["rows"]) <= 4
        assert result["row_count"] == 10_000

    def test_columns_match_query(self) -> None:
        result = _preview_widget("demo_points_10k", limit=1)
        for col in ("id", "x", "y", "category"):
            assert col in result["columns"]

    def test_unknown_query_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown query_id"):
            _preview_widget("nope", limit=1)


# ---------------------------------------------------------------------------
# upsert_dashboard
# ---------------------------------------------------------------------------


class TestUpsertDashboard:
    """Tests for _upsert_dashboard() — the governed write."""

    def test_valid_spec_creates_board(self, inject_stores) -> None:
        result = _upsert_dashboard("My Board", _valid_spec())
        assert "id" in result and result["id"]
        assert result["name"] == "My Board"
        assert result["env"] == "dev"
        assert result["version"] == 1

    def test_invalid_spec_is_refused_with_structured_errors(self, inject_stores) -> None:
        repo, _env = inject_stores
        import asyncio

        result = _upsert_dashboard("Bad Board", _invalid_spec())
        # The governed write refuses an invalid spec.
        assert result.get("ok") is False
        assert result["reason"] == "invalid_spec"
        assert len(result["errors"]) >= 1
        codes = {e["code"] for e in result["errors"]}
        assert "missing_encoding_x" in codes
        # And NO board was persisted.
        boards = asyncio.run(repo.list("boards", "mcp"))
        assert boards == [], f"Invalid spec must not persist a board: {boards}"

    def test_refuses_prod_env_by_default(self, inject_stores) -> None:
        with pytest.raises(ValueError, match="not writable"):
            _upsert_dashboard("Prod Board", _valid_spec(), env="prod")

    def test_board_stored_under_org(self, inject_stores) -> None:
        repo, _env = inject_stores
        import asyncio

        result = _upsert_dashboard("Stored Board", _valid_spec())
        boards = asyncio.run(repo.list("boards", "mcp"))
        assert any(b["id"] == result["id"] for b in boards)


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------


class TestPromote:
    """Tests for _promote() — the human-gated dev -> prod publish."""

    def test_promote_dev_to_prod(self, inject_stores) -> None:
        up = _upsert_dashboard("Promote Me", _valid_spec())
        result = _promote(up["id"])
        assert result["promoted"] is True
        assert result["from_env"] == "dev"
        assert result["to_env"] == "prod"
        assert result["version"] == up["version"]

    def test_prod_pointer_set_after_promote(self, inject_stores) -> None:
        _repo, env_store = inject_stores
        import asyncio

        up = _upsert_dashboard("Pointer Board", _valid_spec())
        _promote(up["id"])

        # Resolve the prod environment for this board's (deterministic) project
        # and assert it now points at the promoted version.
        from nubi_mcp.server import _project_id_for_org

        project_id = _project_id_for_org("mcp")
        prod_env = asyncio.run(env_store.get_environment_by_key(project_id, "prod"))
        assert prod_env is not None
        ptr = asyncio.run(
            env_store.get_pointer("board", up["id"], prod_env["id"])
        )
        assert ptr is not None, "prod pointer should exist after promote"

    def test_promote_without_source_pointer(self, inject_stores) -> None:
        """A board with no dev pointer cannot be promoted — reported, not raised."""
        _repo, env_store = inject_stores
        import asyncio

        from app.repos import get_repo
        from nubi_mcp.server import _project_id_for_org

        # Create a board directly (no version/pointer) so from_env has nothing.
        repo = get_repo()
        row = asyncio.run(
            repo.create(
                resource="boards",
                org_id="mcp",
                created_by="t",
                name="No Pointer",
                config={"spec": {}, "html": ""},
                project_id=_project_id_for_org("mcp"),
            )
        )
        result = _promote(row["id"])
        assert result["promoted"] is False
        assert result["reason"] == "no_source_pointer"
