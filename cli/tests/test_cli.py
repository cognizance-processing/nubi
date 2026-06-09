"""Tests for the Nubi CLI.

Strategy
--------
- ``typer.testing.CliRunner`` drives the app.
- HTTP is prevented by monkeypatching ``nubi_cli.client.get``,
  ``nubi_cli.client.post``, and ``nubi_cli.client.put`` with lightweight fakes.
- Real Arrow IPC bytes are generated inline with pyarrow for the ``run`` test.
- No actual network connections are made in any test.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from nubi_cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(json_data: Any = None, content: bytes = b"", status_code: int = 200):
    """Build a minimal mock that looks like an httpx.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.is_success = 200 <= status_code < 300
    mock.content = content
    mock.text = json.dumps(json_data) if json_data is not None else content.decode(errors="replace")
    mock.json.return_value = json_data if json_data is not None else {}
    return mock


def _make_arrow_bytes(num_rows: int = 5) -> bytes:
    """Return a minimal Arrow IPC stream containing *num_rows* rows."""
    import pyarrow as pa
    import pyarrow.ipc as pa_ipc

    schema = pa.schema([pa.field("value", pa.int64())])
    table = pa.table({"value": list(range(num_rows))})
    buf = io.BytesIO()
    with pa_ipc.new_stream(buf, schema) as writer:
        writer.write_table(table)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# deploy --dry-run
# ---------------------------------------------------------------------------


class TestDeployDryRun:
    """--dry-run must print the plan and make NO HTTP calls."""

    def test_dry_run_create_shown(self, tmp_path: Path, monkeypatch):
        """A file without an id is shown as CREATE in the plan."""
        resource_file = tmp_path / "my_board.json"
        resource_file.write_text(
            json.dumps({"resource": "boards", "name": "My Board", "config": {}})
        )

        # Patch HTTP verbs — they must NOT be called
        mock_post = MagicMock(side_effect=AssertionError("POST should not be called in dry-run"))
        mock_put = MagicMock(side_effect=AssertionError("PUT should not be called in dry-run"))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)
        monkeypatch.setattr("nubi_cli.client.put", mock_put)

        result = runner.invoke(app, ["deploy", str(tmp_path), "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "CREATE" in result.output
        assert "my_board.json" in result.output
        mock_post.assert_not_called()
        mock_put.assert_not_called()

    def test_dry_run_update_shown(self, tmp_path: Path, monkeypatch):
        """A file WITH an id is shown as UPDATE in the plan."""
        resource_file = tmp_path / "existing_board.json"
        resource_file.write_text(
            json.dumps(
                {
                    "resource": "boards",
                    "id": "board-uuid-1234",
                    "name": "Existing Board",
                    "config": {},
                }
            )
        )

        mock_post = MagicMock(side_effect=AssertionError("POST must not be called"))
        mock_put = MagicMock(side_effect=AssertionError("PUT must not be called"))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)
        monkeypatch.setattr("nubi_cli.client.put", mock_put)

        result = runner.invoke(app, ["deploy", str(tmp_path), "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "UPDATE" in result.output
        assert "existing_board.json" in result.output
        mock_post.assert_not_called()
        mock_put.assert_not_called()

    def test_dry_run_mixed_plan(self, tmp_path: Path, monkeypatch):
        """Both CREATE and UPDATE appear for a mixed directory."""
        (tmp_path / "new.json").write_text(
            json.dumps({"resource": "boards", "name": "New"})
        )
        (tmp_path / "old.json").write_text(
            json.dumps({"resource": "boards", "id": "abc", "name": "Old"})
        )

        mock_post = MagicMock(side_effect=AssertionError("no POST in dry-run"))
        mock_put = MagicMock(side_effect=AssertionError("no PUT in dry-run"))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)
        monkeypatch.setattr("nubi_cli.client.put", mock_put)

        result = runner.invoke(app, ["deploy", str(tmp_path), "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "CREATE" in result.output
        assert "UPDATE" in result.output
        mock_post.assert_not_called()
        mock_put.assert_not_called()


# ---------------------------------------------------------------------------
# deploy validation
# ---------------------------------------------------------------------------


class TestDeployValidation:
    """Files missing 'resource'/'name' (or with an unknown resource type) are
    reported and excluded from the plan — no HTTP call is ever attempted."""

    @staticmethod
    def _patch_no_http(monkeypatch) -> tuple[MagicMock, MagicMock]:
        mock_post = MagicMock(side_effect=AssertionError("POST must not be called for invalid files"))
        mock_put = MagicMock(side_effect=AssertionError("PUT must not be called for invalid files"))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)
        monkeypatch.setattr("nubi_cli.client.put", mock_put)
        return mock_post, mock_put

    @staticmethod
    def _all_output(result) -> str:
        try:
            return result.output + (result.stderr or "")
        except ValueError:
            return result.output

    def test_missing_resource_field_skipped(self, tmp_path: Path, monkeypatch):
        """A file without a 'resource' field is skipped with an error."""
        (tmp_path / "no_resource.json").write_text(
            json.dumps({"name": "Orphan", "config": {}})
        )
        mock_post, mock_put = self._patch_no_http(monkeypatch)

        result = runner.invoke(app, ["deploy", str(tmp_path)])

        output = self._all_output(result)
        assert result.exit_code == 1, output
        assert "no_resource.json" in output
        assert "resource" in output
        mock_post.assert_not_called()
        mock_put.assert_not_called()

    def test_unknown_resource_type_skipped(self, tmp_path: Path, monkeypatch):
        """A file with an unknown resource type is skipped with an error."""
        (tmp_path / "bad_type.json").write_text(
            json.dumps({"resource": "gizmos", "name": "Gizmo", "config": {}})
        )
        mock_post, mock_put = self._patch_no_http(monkeypatch)

        result = runner.invoke(app, ["deploy", str(tmp_path)])

        output = self._all_output(result)
        assert result.exit_code == 1, output
        assert "bad_type.json" in output
        assert "gizmos" in output
        mock_post.assert_not_called()
        mock_put.assert_not_called()

    def test_missing_name_field_skipped(self, tmp_path: Path, monkeypatch):
        """A file without a 'name' field is skipped with an error."""
        (tmp_path / "no_name.json").write_text(
            json.dumps({"resource": "boards", "config": {}})
        )
        mock_post, mock_put = self._patch_no_http(monkeypatch)

        result = runner.invoke(app, ["deploy", str(tmp_path)])

        output = self._all_output(result)
        assert result.exit_code == 1, output
        assert "no_name.json" in output
        assert "name" in output
        mock_post.assert_not_called()
        mock_put.assert_not_called()

    def test_invalid_excluded_from_dry_run_plan(self, tmp_path: Path, monkeypatch):
        """--dry-run: invalid files are reported and left out of the plan."""
        (tmp_path / "good.json").write_text(
            json.dumps({"resource": "boards", "name": "Good", "config": {}})
        )
        (tmp_path / "no_resource.json").write_text(
            json.dumps({"name": "Bad", "config": {}})
        )
        mock_post, mock_put = self._patch_no_http(monkeypatch)

        result = runner.invoke(app, ["deploy", str(tmp_path), "--dry-run"])

        output = self._all_output(result)
        assert result.exit_code == 0, output
        assert "good.json" in result.output
        assert "Skipping" in output and "no_resource.json" in output
        mock_post.assert_not_called()
        mock_put.assert_not_called()

    def test_invalid_excluded_from_live_deploy(self, tmp_path: Path, monkeypatch):
        """Live deploy: only the valid file produces an HTTP call."""
        (tmp_path / "good.json").write_text(
            json.dumps({"resource": "boards", "name": "Good", "config": {}})
        )
        (tmp_path / "no_name.json").write_text(
            json.dumps({"resource": "boards", "config": {}})
        )
        mock_post = MagicMock(return_value=_make_response(json_data={"id": "new-id"}))
        mock_put = MagicMock(side_effect=AssertionError("PUT must not be called"))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)
        monkeypatch.setattr("nubi_cli.client.put", mock_put)

        result = runner.invoke(app, ["deploy", str(tmp_path)])

        output = self._all_output(result)
        assert result.exit_code == 0, output
        assert "no_name.json" in output
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "boards"
        mock_put.assert_not_called()


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


class TestLogin:
    """Login should POST /auth/login and save the returned token."""

    def test_login_saves_token(self, tmp_path: Path, monkeypatch):
        """Successful login persists the access_token."""
        # Redirect credentials to a temp location
        creds_file = tmp_path / ".nubi" / "credentials"
        monkeypatch.setattr("nubi_cli.config._CREDENTIALS_PATH", creds_file)

        mock_post = MagicMock(
            return_value=_make_response(
                json_data={"access_token": "test-jwt-token-abc", "user": {"id": "u1"}}
            )
        )
        monkeypatch.setattr("nubi_cli.client.post", mock_post)

        result = runner.invoke(
            app, ["login"], input="test@example.com\nsecret\n"
        )

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "auth/login"
        assert call_args[1]["json"]["email"] == "test@example.com"
        assert call_args[1]["json"]["password"] == "secret"

        # Token should be on disk
        assert creds_file.exists()
        stored = json.loads(creds_file.read_text())
        assert stored["access_token"] == "test-jwt-token-abc"

    def test_login_failure_shows_error(self, tmp_path: Path, monkeypatch):
        """A server error surfaces a non-zero exit and error message."""
        from nubi_cli.client import CLIError

        monkeypatch.setattr(
            "nubi_cli.client.post",
            MagicMock(side_effect=CLIError("invalid_credentials", "Wrong email or password", 401)),
        )
        creds_file = tmp_path / ".nubi" / "credentials"
        monkeypatch.setattr("nubi_cli.config._CREDENTIALS_PATH", creds_file)

        result = runner.invoke(app, ["login"], input="bad@example.com\nwrong\n")

        assert result.exit_code != 0
        # The error message should appear in stderr output (captured by mix_stderr=False)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


class TestRun:
    """run should POST /query and print a row count."""

    def test_run_prints_row_count(self, monkeypatch):
        """Arrow IPC response → row count printed."""
        arrow_bytes = _make_arrow_bytes(num_rows=42)
        mock_post = MagicMock(
            return_value=_make_response(content=arrow_bytes)
        )
        monkeypatch.setattr("nubi_cli.client.post", mock_post)

        result = runner.invoke(app, ["run", "query-uuid-999"])

        assert result.exit_code == 0, result.output
        assert "42" in result.output
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "query"
        assert call_args[1]["json"]["query_id"] == "query-uuid-999"

    def test_run_fallback_without_pyarrow(self, monkeypatch):
        """Without pyarrow, byte count is reported rather than row count."""
        raw = b"some-arrow-bytes-placeholder"
        mock_post = MagicMock(return_value=_make_response(content=raw))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)

        # Simulate pyarrow being unavailable by patching the import
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pyarrow.ipc":
                raise ImportError("no pyarrow")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        result = runner.invoke(app, ["run", "query-uuid-000"])

        assert result.exit_code == 0, result.output
        assert str(len(raw)) in result.output


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


class TestDiff:
    """diff should compare local files vs server and report field-level changes."""

    def test_diff_shows_changed_name(self, tmp_path: Path, monkeypatch):
        """When the server has a different name the diff is shown."""
        resource_file = tmp_path / "board.json"
        resource_file.write_text(
            json.dumps(
                {
                    "resource": "boards",
                    "id": "board-123",
                    "name": "New Name",
                    "config": {},
                }
            )
        )

        # Server returns the old name
        server_data = {
            "id": "board-123",
            "name": "Old Name",
            "config": {},
        }
        mock_get = MagicMock(return_value=_make_response(json_data=server_data))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)

        result = runner.invoke(app, ["diff", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Old Name" in result.output
        assert "New Name" in result.output
        # Confirm it was a GET, not a write
        mock_get.assert_called_once()

    def test_diff_shows_new_for_missing_id(self, tmp_path: Path, monkeypatch):
        """A file without an id is marked NEW without calling the API."""
        resource_file = tmp_path / "new_resource.json"
        resource_file.write_text(
            json.dumps({"resource": "datastores", "name": "Brand New"})
        )

        mock_get = MagicMock(side_effect=AssertionError("GET must not be called for id-less files"))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)

        result = runner.invoke(app, ["diff", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "NEW" in result.output
        mock_get.assert_not_called()

    def test_diff_shows_new_for_404(self, tmp_path: Path, monkeypatch):
        """A 404 from the server is shown as NEW."""
        from nubi_cli.client import CLIError

        resource_file = tmp_path / "ghost.json"
        resource_file.write_text(
            json.dumps({"resource": "boards", "id": "ghost-id", "name": "Ghost"})
        )

        mock_get = MagicMock(
            side_effect=CLIError("not_found", "Resource not found", 404)
        )
        monkeypatch.setattr("nubi_cli.client.get", mock_get)

        result = runner.invoke(app, ["diff", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "NEW" in result.output

    def test_diff_ok_when_unchanged(self, tmp_path: Path, monkeypatch):
        """Resources that match the server are shown as OK, no diff output."""
        resource_file = tmp_path / "same.json"
        resource_file.write_text(
            json.dumps(
                {
                    "resource": "boards",
                    "id": "board-xyz",
                    "name": "Matching Name",
                    "config": {"key": "val"},
                }
            )
        )

        server_data = {
            "id": "board-xyz",
            "name": "Matching Name",
            "config": {"key": "val"},
        }
        mock_get = MagicMock(return_value=_make_response(json_data=server_data))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)

        result = runner.invoke(app, ["diff", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "OK" in result.output
