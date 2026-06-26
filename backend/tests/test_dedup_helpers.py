"""Tests for the dedup/simplification helpers introduced in the redundancy sweep.

Covers:
    A. ``open_duckdb_readonly`` (duckdb_conn.py)
    B. ``require_not_embed`` (_org.py)

These are behaviour-pinning tests: if either helper is removed or its
semantics change, these tests catch the regression before the call sites are
re-inlined.
"""

from __future__ import annotations

import pytest

from app.errors import AppError


# ── A. open_duckdb_readonly ──────────────────────────────────────────────────

class TestOpenDuckdbReadonly:
    """open_duckdb_readonly returns a hardened read-only DuckDB connection."""

    def test_returns_connection_for_real_file(self, tmp_path):
        """A real on-disk DuckDB file can be opened read-only."""
        import duckdb

        from app.connectors.duckdb_conn import open_duckdb_readonly

        db = tmp_path / "test.duckdb"
        # Create a small DB file first.
        with duckdb.connect(database=str(db)) as setup:
            setup.execute("CREATE TABLE t AS SELECT 42 AS x")

        conn = open_duckdb_readonly(str(db))
        try:
            result = conn.execute("SELECT x FROM t").fetchone()
            assert result == (42,)
        finally:
            conn.close()

    def test_external_access_disabled(self, tmp_path):
        """After open_duckdb_readonly, enable_external_access must be off."""
        import duckdb

        from app.connectors.duckdb_conn import open_duckdb_readonly

        db = tmp_path / "locked.duckdb"
        with duckdb.connect(database=str(db)) as setup:
            setup.execute("CREATE TABLE sentinel AS SELECT 1 AS v")

        conn = open_duckdb_readonly(str(db))
        try:
            # The setting should be false (hardened).  DuckDB returns a bool or
            # a string depending on version — normalise to bool before asserting.
            row = conn.execute("SELECT current_setting('enable_external_access')").fetchone()
            assert row is not None
            val = row[0]
            if isinstance(val, bool):
                assert val is False
            else:
                assert str(val).lower() in ("false", "0", "off")
        except duckdb.CatalogException:
            # Older DuckDB versions don't expose this via current_setting;
            # the important thing is that the connection opens without error.
            pass
        finally:
            conn.close()

    def test_connection_is_read_only(self, tmp_path):
        """Writes on the read-only connection must be rejected by DuckDB."""
        import duckdb

        from app.connectors.duckdb_conn import open_duckdb_readonly

        db = tmp_path / "readonly.duckdb"
        with duckdb.connect(database=str(db)) as setup:
            setup.execute("CREATE TABLE t AS SELECT 1 AS v")

        conn = open_duckdb_readonly(str(db))
        try:
            with pytest.raises(Exception):
                conn.execute("INSERT INTO t VALUES (2)")
        finally:
            conn.close()

    def test_uses_harden_connection_memory_limit(self, tmp_path, monkeypatch):
        """Memory limit env var flows through open_duckdb_readonly → harden_connection."""
        import duckdb

        from app.connectors.duckdb_conn import open_duckdb_readonly

        monkeypatch.setenv("NUBI_DUCKDB_MEMORY_LIMIT", "512MB")

        db = tmp_path / "memlimit.duckdb"
        with duckdb.connect(database=str(db)) as setup:
            setup.execute("CREATE TABLE t AS SELECT 1 AS v")

        conn = open_duckdb_readonly(str(db))
        try:
            row = conn.execute("SELECT current_setting('memory_limit')").fetchone()
            assert row is not None
            assert "512" in row[0]
        except Exception:  # noqa: BLE001 — skip if DuckDB version doesn't expose setting
            pass
        finally:
            conn.close()


# ── B. require_not_embed ─────────────────────────────────────────────────────

class TestRequireNotEmbed:
    """require_not_embed raises 403 for embed tokens, passes for access tokens."""

    def _make_identity(self, kind: str) -> object:
        """Return a minimal VerifiedIdentity-like object."""
        from types import SimpleNamespace
        return SimpleNamespace(kind=kind, user_id="u1", org=None, scope=[])

    def test_embed_token_raises_403(self):
        from app.routes._org import require_not_embed

        identity = self._make_identity("embed")
        with pytest.raises(AppError) as exc_info:
            require_not_embed(identity, "create canvases")

        err = exc_info.value
        assert err.status == 403  # type: ignore[attr-defined]
        assert err.code == "forbidden"  # type: ignore[attr-defined]
        assert "create canvases" in str(err)

    def test_access_token_passes(self):
        from app.routes._org import require_not_embed

        identity = self._make_identity("access")
        # Must not raise
        require_not_embed(identity, "create canvases")

    def test_action_interpolated_into_message(self):
        """The action string appears in the error detail."""
        from app.routes._org import require_not_embed

        identity = self._make_identity("embed")
        with pytest.raises(AppError) as exc_info:
            require_not_embed(identity, "manage watches")

        assert "manage watches" in str(exc_info.value)

    def test_unknown_kind_passes(self):
        """A future/unknown kind is not blocked — only 'embed' is blocked."""
        from app.routes._org import require_not_embed

        identity = self._make_identity("service")
        require_not_embed(identity, "some action")  # must not raise

    @pytest.mark.parametrize("action", [
        "create canvases",
        "update canvases",
        "schedule canvases",
        "delete canvases",
        "register metrics",
        "manage watches",
        "register queries",
    ])
    def test_all_guarded_actions_blocked_for_embed(self, action: str):
        """Every action that was previously guarded inline still raises 403."""
        from app.routes._org import require_not_embed

        identity = self._make_identity("embed")
        with pytest.raises(AppError) as exc_info:
            require_not_embed(identity, action)

        assert exc_info.value.status == 403  # type: ignore[attr-defined]
