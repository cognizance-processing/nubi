"""Tests for flows-as-files (load/dump) and local flow execution.

Test coverage
-------------
- load_flow_file: round-trip YAML → dict → YAML → dict (round_trip_yaml).
- load_flow_file: JSON round-trip.
- load_flow_file: validation error on bad spec.
- dump_flow: creates parent directories when absent.
- flows run command: trivial noop flow (in-memory store, no server).
- flows run command: python task that sets a result value.
- flows push command: dry-run creates no API calls.
- flows pull command: writes YAML files from mocked API.
- secrets set command: writes to local file.
- secrets list command: lists local secrets.

All backend imports are performed via the same sys.path adjustment used by
``flows_files.py`` (climbing to the repo root and adding backend/).  If the
backend package is not importable the local-run test is skipped rather than
failing, because CI environments may not have the backend deps installed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from nubi_cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(json_data: Any = None, status_code: int = 200) -> MagicMock:
    """Build a minimal mock httpx.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.is_success = 200 <= status_code < 300
    mock.content = b""
    mock.text = json.dumps(json_data) if json_data is not None else ""
    mock.json.return_value = json_data if json_data is not None else {}
    return mock


def _backend_importable() -> bool:
    """Return True if the backend package is importable."""
    try:
        from nubi_cli.flows_files import _ensure_backend_on_path  # noqa: PLC0415

        return _ensure_backend_on_path()
    except Exception:
        return False


_BACKEND_AVAILABLE = _backend_importable()

# ---------------------------------------------------------------------------
# Minimal flow specs for testing
# ---------------------------------------------------------------------------

_NOOP_FLOW_SPEC: dict = {
    "version": 1,
    "name": "test_noop_flow",
    "params": [],
    "tasks": [
        {"key": "step1", "kind": "noop", "needs": [], "config": {}},
    ],
}

_PYTHON_FLOW_SPEC: dict = {
    "version": 1,
    "name": "test_python_flow",
    "params": [],
    "tasks": [
        {
            "key": "compute",
            "kind": "python",
            "needs": [],
            "config": {"code": "result = {'answer': 42}"},
        }
    ],
}

_INVALID_FLOW_SPEC: dict = {
    "version": 1,
    "name": "bad",
    "tasks": [
        # python task without 'code' — hard validation error.
        {"key": "bad_task", "kind": "python", "needs": [], "config": {}}
    ],
}


# ---------------------------------------------------------------------------
# flows_files.load_flow_file + dump_flow
# ---------------------------------------------------------------------------


class TestLoadDumpYaml:
    """Round-trip: load YAML → dict → dump YAML → re-load."""

    def test_yaml_round_trip(self, tmp_path: Path) -> None:
        from nubi_cli.flows_files import dump_flow, load_flow_file  # noqa: PLC0415

        src = tmp_path / "flow.yaml"
        dump_flow(_NOOP_FLOW_SPEC, src)

        reloaded = load_flow_file(src)
        assert reloaded["name"] == "test_noop_flow"
        assert reloaded["version"] == 1
        assert len(reloaded["tasks"]) == 1
        assert reloaded["tasks"][0]["key"] == "step1"

    def test_json_round_trip(self, tmp_path: Path) -> None:
        from nubi_cli.flows_files import dump_flow, load_flow_file  # noqa: PLC0415

        src = tmp_path / "flow.json"
        dump_flow(_NOOP_FLOW_SPEC, src)

        reloaded = load_flow_file(src)
        assert reloaded["name"] == "test_noop_flow"
        assert len(reloaded["tasks"]) == 1

    def test_dump_creates_parent_dirs(self, tmp_path: Path) -> None:
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        deep = tmp_path / "a" / "b" / "c" / "flow.yaml"
        dump_flow(_NOOP_FLOW_SPEC, deep)
        assert deep.exists()

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        from nubi_cli.flows_files import load_flow_file  # noqa: PLC0415

        with pytest.raises(FileNotFoundError):
            load_flow_file(tmp_path / "nonexistent.yaml")

    def test_load_invalid_spec_raises(self, tmp_path: Path) -> None:
        """A spec that fails hard validation raises FlowFileError."""
        from nubi_cli.flows_files import FlowFileError, dump_flow, load_flow_file  # noqa: PLC0415

        src = tmp_path / "bad_flow.yaml"
        dump_flow(_INVALID_FLOW_SPEC, src)

        with pytest.raises(FlowFileError):
            load_flow_file(src)

    def test_load_bad_yaml_raises(self, tmp_path: Path) -> None:
        from nubi_cli.flows_files import FlowFileError, load_flow_file  # noqa: PLC0415

        src = tmp_path / "broken.yaml"
        src.write_text("key: [unclosed bracket\n")

        with pytest.raises(FlowFileError):
            load_flow_file(src)

    def test_load_bad_json_raises(self, tmp_path: Path) -> None:
        from nubi_cli.flows_files import FlowFileError, load_flow_file  # noqa: PLC0415

        src = tmp_path / "broken.json"
        src.write_text("{not valid json")

        with pytest.raises(FlowFileError):
            load_flow_file(src)


# ---------------------------------------------------------------------------
# Local flow execution: noop flow
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _BACKEND_AVAILABLE, reason="Backend package not importable")
class TestLocalRunNoop:
    """'nubi flows run' drives a trivial noop flow to completion locally."""

    def test_noop_flow_succeeds(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        flow_file = tmp_path / "noop.yaml"
        dump_flow(_NOOP_FLOW_SPEC, flow_file)

        # Redirect local secrets to a temp location so tests are isolated.
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", tmp_path / "secrets")

        result = runner.invoke(app, ["flows", "run", str(flow_file)])

        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "success" in result.output.lower() or "success" in (result.stderr or "").lower()

    def test_noop_flow_with_param(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--param flags are accepted and the run still succeeds."""
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        spec = {
            "version": 1,
            "name": "param_flow",
            "params": [{"name": "region", "type": "text", "required": False}],
            "tasks": [{"key": "step1", "kind": "noop", "needs": [], "config": {}}],
        }
        flow_file = tmp_path / "param_flow.yaml"
        dump_flow(spec, flow_file)

        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", tmp_path / "secrets")

        result = runner.invoke(
            app, ["flows", "run", str(flow_file), "--param", "region=us"]
        )

        assert result.exit_code == 0, result.output + (result.stderr or "")


@pytest.mark.skipif(not _BACKEND_AVAILABLE, reason="Backend package not importable")
class TestLocalRunPython:
    """'nubi flows run' executes a python task and captures result."""

    def test_python_task_succeeds(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        flow_file = tmp_path / "py_flow.yaml"
        dump_flow(_PYTHON_FLOW_SPEC, flow_file)

        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", tmp_path / "secrets")

        result = runner.invoke(app, ["flows", "run", str(flow_file)])

        # Python tasks may produce a result dict {answer: 42}; the executor
        # returns it in the task_run.result field.
        assert result.exit_code == 0, result.output + (result.stderr or "")


@pytest.mark.skipif(not _BACKEND_AVAILABLE, reason="Backend package not importable")
class TestLocalRunSecrets:
    """'nubi flows run' wires local secrets (env + ~/.nubi/secrets) into tasks."""

    _SECRET_FLOW_SPEC: dict = {
        "version": 1,
        "name": "secret_flow",
        "params": [],
        "tasks": [
            {
                "key": "read_secret",
                "kind": "python",
                "needs": [],
                "config": {"code": "result = {'token': secrets.get('MY_TOKEN', '<missing>')}"},
            }
        ],
    }

    def test_env_var_secret_reaches_task_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NUBI_SECRET_<NAME> env vars populate TaskContext.secrets."""
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        flow_file = tmp_path / "secret_flow.yaml"
        dump_flow(self._SECRET_FLOW_SPEC, flow_file)

        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", tmp_path / "secrets")
        monkeypatch.setenv("NUBI_SECRET_MY_TOKEN", "env-tok-123")

        result = runner.invoke(app, ["flows", "run", str(flow_file)])

        assert result.exit_code == 0, result.output + (result.stderr or "")
        # The secret VALUE must appear in the printed task result.
        assert "env-tok-123" in result.output

    def test_local_secrets_file_reaches_task_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Secrets from ~/.nubi/secrets populate TaskContext.secrets."""
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        flow_file = tmp_path / "secret_flow.yaml"
        dump_flow(self._SECRET_FLOW_SPEC, flow_file)

        secrets_path = tmp_path / "secrets"
        secrets_path.write_text(json.dumps({"MY_TOKEN": "file-tok-456"}))
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", secrets_path)
        monkeypatch.delenv("NUBI_SECRET_MY_TOKEN", raising=False)

        result = runner.invoke(app, ["flows", "run", str(flow_file)])

        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "file-tok-456" in result.output

    def test_env_var_overrides_secrets_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When both define the same name, NUBI_SECRET_* wins over the file."""
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        flow_file = tmp_path / "secret_flow.yaml"
        dump_flow(self._SECRET_FLOW_SPEC, flow_file)

        secrets_path = tmp_path / "secrets"
        secrets_path.write_text(json.dumps({"MY_TOKEN": "file-tok-456"}))
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", secrets_path)
        monkeypatch.setenv("NUBI_SECRET_MY_TOKEN", "env-tok-789")

        result = runner.invoke(app, ["flows", "run", str(flow_file)])

        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "env-tok-789" in result.output
        assert "file-tok-456" not in result.output

    def test_secret_template_interpolation_in_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """{{ secrets.NAME }} in a task config resolves before execution."""
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        spec = {
            "version": 1,
            "name": "secret_template_flow",
            "params": [],
            "tasks": [
                {
                    "key": "templated",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "result = {'tok': '{{ secrets.MY_TOKEN }}'}"},
                }
            ],
        }
        flow_file = tmp_path / "secret_template_flow.yaml"
        dump_flow(spec, flow_file)

        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", tmp_path / "secrets")
        monkeypatch.setenv("NUBI_SECRET_MY_TOKEN", "tpl-tok-321")

        result = runner.invoke(app, ["flows", "run", str(flow_file)])

        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "tpl-tok-321" in result.output


@pytest.mark.skipif(not _BACKEND_AVAILABLE, reason="Backend package not importable")
class TestLocalRunErrors:
    """'nubi flows run' exits non-zero on invalid file or failed spec."""

    def test_missing_file(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["flows", "run", str(tmp_path / "ghost.yaml")])
        assert result.exit_code != 0

    def test_invalid_param_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        flow_file = tmp_path / "noop.yaml"
        dump_flow(_NOOP_FLOW_SPEC, flow_file)
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", tmp_path / "secrets")

        result = runner.invoke(app, ["flows", "run", str(flow_file), "--param", "noequalsign"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# flows push (mocked API)
# ---------------------------------------------------------------------------


class TestFlowsPush:
    """flows push: dry-run and live push via mocked API client."""

    def test_push_dry_run_no_api_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        flow_file = tmp_path / "my_flow.yaml"
        dump_flow(_NOOP_FLOW_SPEC, flow_file)

        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        mock_get = MagicMock(return_value=_make_response(json_data=[]))
        mock_post = MagicMock(side_effect=AssertionError("POST must not be called in dry-run"))
        mock_put = MagicMock(side_effect=AssertionError("PUT must not be called in dry-run"))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)
        monkeypatch.setattr("nubi_cli.client.post", mock_post)
        monkeypatch.setattr("nubi_cli.client.put", mock_put)

        result = runner.invoke(
            app, ["flows", "push", str(flow_file), "--dry-run"]
        )

        assert result.exit_code == 0, result.output
        assert "CREATE" in result.output
        mock_post.assert_not_called()
        mock_put.assert_not_called()

    def test_push_create_new_flow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        flow_file = tmp_path / "my_flow.yaml"
        dump_flow(_NOOP_FLOW_SPEC, flow_file)

        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        mock_get = MagicMock(return_value=_make_response(json_data=[]))
        mock_post = MagicMock(return_value=_make_response(json_data={"id": "new-uuid"}))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)
        monkeypatch.setattr("nubi_cli.client.post", mock_post)

        result = runner.invoke(app, ["flows", "push", str(flow_file)])

        assert result.exit_code == 0, result.output
        assert "CREATED" in result.output
        mock_post.assert_called_once()

    def test_push_update_existing_flow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        flow_file = tmp_path / "my_flow.yaml"
        dump_flow(_NOOP_FLOW_SPEC, flow_file)

        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        existing = [{"id": "existing-uuid", "name": "test_noop_flow"}]
        mock_get = MagicMock(return_value=_make_response(json_data=existing))
        mock_put = MagicMock(return_value=_make_response(json_data={"id": "existing-uuid"}))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)
        monkeypatch.setattr("nubi_cli.client.put", mock_put)

        result = runner.invoke(app, ["flows", "push", str(flow_file)])

        assert result.exit_code == 0, result.output
        assert "UPDATED" in result.output
        mock_put.assert_called_once()

    def test_push_not_logged_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: None)

        result = runner.invoke(app, ["flows", "push"])

        assert result.exit_code != 0

    def test_push_no_args_discovers_files_in_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no file args, *.yaml/*.json in the cwd are discovered and pushed."""
        from nubi_cli.flows_files import dump_flow  # noqa: PLC0415

        dump_flow(_NOOP_FLOW_SPEC, tmp_path / "discovered.yaml")
        json_spec = dict(_PYTHON_FLOW_SPEC)
        dump_flow(json_spec, tmp_path / "discovered.json")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        mock_get = MagicMock(return_value=_make_response(json_data=[]))
        mock_post = MagicMock(return_value=_make_response(json_data={"id": "new-uuid"}))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)
        monkeypatch.setattr("nubi_cli.client.post", mock_post)

        result = runner.invoke(app, ["flows", "push"])

        assert result.exit_code == 0, result.output
        assert "discovered.yaml" in result.output
        assert "discovered.json" in result.output
        assert mock_post.call_count == 2
        pushed_names = {c[1]["json"]["name"] for c in mock_post.call_args_list}
        assert pushed_names == {"test_noop_flow", "test_python_flow"}


# ---------------------------------------------------------------------------
# flows pull (mocked API)
# ---------------------------------------------------------------------------


class TestFlowsPull:
    """flows pull: fetches flows from API and writes YAML files."""

    def test_pull_writes_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        server_flows = [
            {"id": "flow-1", "name": "revenue_flow", "spec": _NOOP_FLOW_SPEC}
        ]
        mock_get = MagicMock(return_value=_make_response(json_data=server_flows))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)

        result = runner.invoke(
            app, ["flows", "pull", "--dir", str(tmp_path)]
        )

        assert result.exit_code == 0, result.output
        # Expect a YAML or JSON file.
        out_files = list(tmp_path.iterdir())
        assert len(out_files) == 1
        assert out_files[0].suffix in (".yaml", ".yml", ".json")

    def test_pull_empty_server(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        mock_get = MagicMock(return_value=_make_response(json_data=[]))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)

        result = runner.invoke(app, ["flows", "pull", "--dir", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "No flows found" in result.output

    def test_pull_not_logged_in(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: None)
        result = runner.invoke(app, ["flows", "pull", "--dir", str(tmp_path)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# secrets set + list
# ---------------------------------------------------------------------------


class TestSecretsSet:
    """secrets set: writes to the local secrets file."""

    def test_set_writes_local(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        secrets_path = tmp_path / "secrets"
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", secrets_path)
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: None)

        result = runner.invoke(app, ["secrets", "set", "MY_KEY", "my_secret_value"])

        assert result.exit_code == 0, result.output
        assert secrets_path.exists()
        stored = json.loads(secrets_path.read_text())
        assert stored["MY_KEY"] == "my_secret_value"

    def test_set_also_calls_api_when_logged_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secrets_path = tmp_path / "secrets"
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", secrets_path)
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        mock_post = MagicMock(return_value=_make_response(json_data={"name": "API_KEY"}))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)

        result = runner.invoke(app, ["secrets", "set", "API_KEY", "api_value"])

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["name"] == "API_KEY"

    def test_set_local_only_skips_api(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secrets_path = tmp_path / "secrets"
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", secrets_path)
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        mock_post = MagicMock(side_effect=AssertionError("POST must not be called with --local-only"))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)

        result = runner.invoke(app, ["secrets", "set", "K", "v", "--local-only"])

        assert result.exit_code == 0, result.output
        mock_post.assert_not_called()


class TestSecretsList:
    """secrets list: shows local and/or API secrets."""

    def test_list_local_secrets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        secrets_path = tmp_path / "secrets"
        secrets_path.write_text(json.dumps({"FOO": "bar", "BAZ": "qux"}))
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", secrets_path)
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: None)

        result = runner.invoke(app, ["secrets", "list"])

        assert result.exit_code == 0, result.output
        assert "FOO" in result.output
        assert "BAZ" in result.output
        # Values must not be displayed
        assert "bar" not in result.output
        assert "qux" not in result.output

    def test_list_merges_api_secrets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secrets_path = tmp_path / "secrets"
        secrets_path.write_text(json.dumps({"LOCAL_ONLY": "val"}))
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", secrets_path)
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        api_secrets = [{"name": "API_ONLY"}, {"name": "LOCAL_ONLY"}]
        mock_get = MagicMock(return_value=_make_response(json_data=api_secrets))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)

        result = runner.invoke(app, ["secrets", "list"])

        assert result.exit_code == 0, result.output
        assert "LOCAL_ONLY" in result.output
        assert "API_ONLY" in result.output

    def test_list_local_only_skips_api(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secrets_path = tmp_path / "secrets"
        secrets_path.write_text(json.dumps({"K": "v"}))
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", secrets_path)
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: "test-token")

        mock_get = MagicMock(side_effect=AssertionError("GET must not be called with --local-only"))
        monkeypatch.setattr("nubi_cli.client.get", mock_get)

        result = runner.invoke(app, ["secrets", "list", "--local-only"])

        assert result.exit_code == 0, result.output
        assert "K" in result.output
        mock_get.assert_not_called()

    def test_list_empty_shows_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secrets_path = tmp_path / "secrets"
        monkeypatch.setattr("nubi_cli.main._LOCAL_SECRETS_PATH", secrets_path)
        monkeypatch.setattr("nubi_cli.main.load_token", lambda: None)

        result = runner.invoke(app, ["secrets", "list"])

        assert result.exit_code == 0, result.output
        assert "No secrets" in result.output
