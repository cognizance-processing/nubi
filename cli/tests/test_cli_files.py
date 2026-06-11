"""Tests for the files-as-code CLI surface (doc Sections A–E).

Strategy mirrors test_cli.py:
- ``typer.testing.CliRunner`` drives the app.
- HTTP is prevented by monkeypatching ``nubi_cli.client.{get,post,put,delete}``.
- File serialization round-trips, .gitignore generation, secrets materialize
  expansion, and the GH/GitLab sealing/encoding logic are tested directly
  against the helper modules with stubs (no network, no PyNaCl dependency for
  the encode-path test).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from nubi_cli import project as _project
from nubi_cli import secrets_files as _secrets_files
from nubi_cli import vcs_secrets as _vcs
from nubi_cli.main import app

runner = CliRunner()


def _resp(json_data: Any = None, content: bytes = b"", status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.is_success = 200 <= status_code < 300
    mock.content = content
    mock.text = json.dumps(json_data) if json_data is not None else ""
    mock.json.return_value = json_data if json_data is not None else {}
    return mock


@pytest.fixture
def logged_in(monkeypatch):
    """Force load_token() to return a token across the modules that call it."""
    monkeypatch.setattr("nubi_cli.main.load_token", lambda: "tok")
    monkeypatch.setattr("nubi_cli.config.load_token", lambda: "tok")


# ---------------------------------------------------------------------------
# project.py — .gitignore generation
# ---------------------------------------------------------------------------


class TestGitignore:
    def test_creates_with_secrets_block(self, tmp_path: Path):
        assert _project.write_gitignore(tmp_path) is True
        text = (tmp_path / ".gitignore").read_text()
        assert ".nubi/secrets/" in text
        assert ".nubi/credentials" in text
        assert "*.local.env" in text

    def test_idempotent_append(self, tmp_path: Path):
        (tmp_path / ".gitignore").write_text("node_modules/\n")
        assert _project.write_gitignore(tmp_path) is True
        # Second call is a no-op (block already present).
        assert _project.write_gitignore(tmp_path) is False
        text = (tmp_path / ".gitignore").read_text()
        assert text.count(".nubi/secrets/") == 1
        assert "node_modules/" in text


# ---------------------------------------------------------------------------
# project.py — serialization round-trips
# ---------------------------------------------------------------------------


class TestQueryRoundTrip:
    def test_query_envelope_to_files_and_back(self, tmp_path: Path):
        env = {
            "kind": "query",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Top Customers", "id": "11111111-1111-1111-1111-111111111111"},
            "spec": {
                "name": "Top Customers",
                "sql": "SELECT * FROM customers LIMIT 10",
                "params": [],
                "datastore_id": "ds-1",
                "output_schema": [{"name": "id", "type": "int"}],
            },
        }
        items = _project.envelope_to_files(env)
        paths = {it["path"] for it in items}
        # Slug-named (human) stem, not the uuid.
        assert any(p.startswith("queries/top-customers") and p.endswith(".sql") for p in paths)
        assert any(p.endswith(".meta.json") for p in paths)

        _project.write_files(tmp_path, items)
        out = _project.read_all(tmp_path, ["query"])["query"]
        assert len(out) == 1
        rt = out[0]
        assert rt["metadata"]["id"] == env["metadata"]["id"]
        assert rt["spec"]["sql"] == env["spec"]["sql"]
        assert rt["spec"]["output_schema"] == env["spec"]["output_schema"]

    def test_read_accepts_id_named_files(self, tmp_path: Path):
        """Read must accept id-named files too (doc A note)."""
        rid = "22222222-2222-2222-2222-222222222222"
        qdir = tmp_path / "queries"
        qdir.mkdir()
        (qdir / f"{rid}.sql").write_text("SELECT 1")
        (qdir / f"{rid}.meta.json").write_text(
            json.dumps({"id": rid, "name": "Raw", "config": {"datastore_id": "d"}})
        )
        out = _project.read_all(tmp_path, ["query"])["query"]
        assert len(out) == 1
        assert out[0]["metadata"]["id"] == rid
        assert out[0]["spec"]["sql"] == "SELECT 1"


class TestDashboardRoundTrip:
    def test_dashboard_round_trip(self, tmp_path: Path):
        env = {
            "kind": "dashboard",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Sales", "id": "33333333-3333-3333-3333-333333333333"},
            "spec": {"title": "Sales", "widgets": []},
        }
        items = _project.envelope_to_files(env)
        assert any(it["path"].startswith("dashboards/sales") for it in items)
        _project.write_files(tmp_path, items)
        out = _project.read_all(tmp_path, ["dashboard"])["dashboard"]
        assert len(out) == 1
        assert out[0]["metadata"]["id"] == env["metadata"]["id"]
        assert out[0]["spec"]["title"] == "Sales"


class TestConnectorRoundTrip:
    def test_connector_round_trip_non_secret_only(self, tmp_path: Path):
        env = {
            "kind": "connector",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Prod Postgres", "id": "ds-uuid"},
            "spec": {
                "connector_type": "postgres",
                "host": "db.internal",
                "port": 5432,
                "database": "analytics",
                "user": "readonly",
                "secrets": ["password"],
            },
        }
        items = _project.envelope_to_files(env)
        assert items[0]["path"] == "connectors/prod-postgres.yaml"
        # No secret material in the serialized manifest.
        assert "password" not in items[0]["content"] or "secrets" in items[0]["content"]
        _project.write_files(tmp_path, items)
        out = _project.read_all(tmp_path, ["connector"])["connector"]
        assert len(out) == 1
        assert out[0]["spec"]["connector_type"] == "postgres"
        assert out[0]["spec"]["host"] == "db.internal"
        assert out[0]["metadata"]["id"] == "ds-uuid"


class TestFlowRoundTrip:
    def test_flow_round_trip(self, tmp_path: Path):
        env = {
            "kind": "flow",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "ETL", "id": "44444444-4444-4444-4444-444444444444"},
            "spec": {
                "version": 1,
                "name": "ETL",
                "tasks": [
                    {"key": "extract", "kind": "query", "cell_type": "sql",
                     "config": {"sql": "SELECT 1"}},
                ],
            },
        }
        items = _project.envelope_to_files(env)
        assert any(it["path"].endswith("flow.toml") for it in items)
        assert any("__44444444" in it["path"] for it in items)
        _project.write_files(tmp_path, items)
        out = _project.read_all(tmp_path, ["flow"])["flow"]
        assert len(out) == 1
        assert out[0]["metadata"]["id"] == env["metadata"]["id"]
        assert out[0]["spec"]["tasks"][0]["config"]["sql"] == "SELECT 1"


# ---------------------------------------------------------------------------
# secrets_files.py — materialize expansion
# ---------------------------------------------------------------------------


class TestMaterialize:
    def test_expands_both_prefixes(self, tmp_path: Path):
        environ = {
            "NUBI_SECRET__STRIPE_API_KEY": "sk_live_x",
            "NUBI_CONNECTOR__PROD_POSTGRES__PASSWORD": "s3cr3t",
            "PATH": "/usr/bin",  # ignored
        }
        counts = _secrets_files.materialize(tmp_path, environ)
        assert counts == {"flow": 1, "connector": 1}

        flow = _secrets_files.read_dotenv(_secrets_files.flow_env_path(tmp_path))
        assert flow["STRIPE_API_KEY"] == "sk_live_x"
        conn = _secrets_files.read_dotenv(_secrets_files.connectors_env_path(tmp_path))
        assert conn["PROD_POSTGRES__PASSWORD"] == "s3cr3t"

    def test_materialize_is_additive(self, tmp_path: Path):
        _secrets_files.write_dotenv(
            _secrets_files.flow_env_path(tmp_path), {"EXISTING": "keep"}
        )
        _secrets_files.materialize(tmp_path, {"NUBI_SECRET__NEW": "v"})
        flow = _secrets_files.read_dotenv(_secrets_files.flow_env_path(tmp_path))
        assert flow["EXISTING"] == "keep"
        assert flow["NEW"] == "v"

    def test_connector_key_convention(self):
        assert _secrets_files.connector_key("Prod Postgres", "password") == "PROD_POSTGRES__PASSWORD"
        assert _secrets_files.connector_key("analytics-bq", "service_account_json") == (
            "ANALYTICS_BQ__SERVICE_ACCOUNT_JSON"
        )

    def test_load_flow_secrets_env_overrides_file(self, tmp_path: Path):
        _secrets_files.write_dotenv(_secrets_files.flow_env_path(tmp_path), {"A": "file"})
        out = _secrets_files.load_flow_secrets(tmp_path, {"NUBI_SECRET_A": "env", "NUBI_SECRET_B": "envb"})
        assert out["A"] == "env"
        assert out["B"] == "envb"


class TestMaterializeCommand:
    def test_cli_materialize(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("NUBI_SECRET__FOO", "bar")
        result = runner.invoke(app, ["secrets", "materialize", "--dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        flow = _secrets_files.read_dotenv(_secrets_files.flow_env_path(tmp_path))
        assert flow.get("FOO") == "bar"


# ---------------------------------------------------------------------------
# vcs_secrets.py — sealing / encoding logic (stubbed transport)
# ---------------------------------------------------------------------------


class TestVcsParse:
    def test_parse_https(self):
        assert _vcs.parse_repo_url("https://github.com/acme/repo") == ("acme", "repo")

    def test_parse_https_dotgit(self):
        assert _vcs.parse_repo_url("https://github.com/acme/repo.git") == ("acme", "repo")

    def test_parse_ssh(self):
        assert _vcs.parse_repo_url("git@github.com:acme/repo.git") == ("acme", "repo")

    def test_gitlab_project_path_encoded(self):
        assert _vcs.gitlab_project_path("https://gitlab.com/grp/sub/repo") == "grp%2Fsub%2Frepo"

    def test_prefixed_names(self):
        out = _vcs.prefixed_names({"stripe_key": "v"}, {"prod_postgres__password": "p"})
        assert out["NUBI_SECRET__STRIPE_KEY"] == "v"
        assert out["NUBI_CONNECTOR__PROD_POSTGRES__PASSWORD"] == "p"


class TestGithubPush:
    def test_seal_and_put(self):
        """GitHub path: fetch public key, seal each value, PUT to actions/secrets."""
        calls: list[tuple] = []

        def transport(method, url, headers, body):
            calls.append((method, url, body))
            if method == "GET" and url.endswith("public-key"):
                return 200, {"key": "BASE64KEY", "key_id": "kid-1"}
            if method == "PUT":
                return 201, {}
            raise AssertionError(f"unexpected {method} {url}")

        # Stub seal so the test never needs a real libsodium key.
        def fake_seal(pubkey, value):
            assert pubkey == "BASE64KEY"
            return f"sealed({value})"

        written = _vcs.push_github(
            "https://github.com/acme/repo",
            "ghtok",
            {"NUBI_SECRET__FOO": "bar"},
            transport=transport,
            seal=fake_seal,
        )
        assert written == ["NUBI_SECRET__FOO"]
        # The public-key GET happened, then a PUT carrying the sealed value + key_id.
        put = [c for c in calls if c[0] == "PUT"][0]
        assert put[1].endswith("/actions/secrets/NUBI_SECRET__FOO")
        assert put[2] == {"encrypted_value": "sealed(bar)", "key_id": "kid-1"}

    def test_pubkey_failure_raises(self):
        def transport(method, url, headers, body):
            return 403, {"message": "forbidden"}

        with pytest.raises(_vcs.VcsSecretError):
            _vcs.push_github("https://github.com/a/b", "t", {"X": "y"}, transport=transport)

    def test_real_seal_roundtrip(self):
        """Exercise the real PyNaCl sealing path against a generated keypair."""
        nacl_public = pytest.importorskip("nacl.public")
        import base64

        from nacl import encoding

        sk = nacl_public.PrivateKey.generate()
        pub_b64 = sk.public_key.encode(encoding.Base64Encoder()).decode()
        sealed_b64 = _vcs.seal_secret(pub_b64, "hunter2")
        # Decrypt with the private key to confirm a valid sealed box.
        plaintext = nacl_public.SealedBox(sk).decrypt(base64.b64decode(sealed_b64))
        assert plaintext == b"hunter2"


class TestGitlabPush:
    def test_update_then_create_on_404(self):
        calls: list[tuple] = []

        def transport(method, url, headers, body):
            calls.append((method, url, body))
            if method == "PUT":
                return 404, {}  # variable doesn't exist yet
            if method == "POST":
                return 201, {}
            raise AssertionError(method)

        written = _vcs.push_gitlab(
            "https://gitlab.com/acme/repo",
            "gltok",
            {"NUBI_SECRET__FOO": "bar"},
            environment_scope="prod",
            transport=transport,
        )
        assert written == ["NUBI_SECRET__FOO"]
        post = [c for c in calls if c[0] == "POST"][0]
        assert post[2]["masked"] is True
        assert post[2]["environment_scope"] == "prod"
        assert "%2F" in post[1]  # url-encoded project path


# ---------------------------------------------------------------------------
# init command
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_scaffolds_files(self, tmp_path: Path):
        result = runner.invoke(app, ["init", str(tmp_path), "--name", "My Project"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "nubi.yaml").exists()
        assert (tmp_path / ".nubi" / "project.json").exists()
        assert (tmp_path / ".gitignore").exists()
        manifest = _project.read_nubi_yaml(tmp_path)
        assert manifest["metadata"]["name"] == "My Project"
        assert manifest["kind"] == "project"

    def test_init_ci_github(self, tmp_path: Path):
        result = runner.invoke(app, ["init", str(tmp_path), "--ci", "github"])
        assert result.exit_code == 0, result.output
        wf = tmp_path / ".github" / "workflows" / "nubi-deploy.yml"
        assert wf.exists()
        assert "nubi deploy" in wf.read_text()

    def test_init_ci_gitlab(self, tmp_path: Path):
        result = runner.invoke(app, ["init", str(tmp_path), "--ci", "gitlab"])
        assert result.exit_code == 0, result.output
        ci = tmp_path / ".gitlab-ci.yml"
        assert ci.exists()
        assert "nubi secrets materialize" in ci.read_text()


# ---------------------------------------------------------------------------
# pull / push commands (mock HTTP)
# ---------------------------------------------------------------------------


class TestPull:
    def test_pull_queries(self, tmp_path: Path, monkeypatch, logged_in):
        def fake_get(path, **kwargs):
            if path == "queries":
                return _resp([{"id": "q1", "name": "Q One"}])
            if path == "export/query/q1":
                return _resp(
                    {
                        "kind": "query",
                        "apiVersion": "nubi/v1",
                        "metadata": {"name": "Q One", "id": "q1"},
                        "spec": {"name": "Q One", "sql": "SELECT 1", "params": [], "datastore_id": "d"},
                    }
                )
            return _resp([])

        monkeypatch.setattr("nubi_cli.client.get", fake_get)
        result = runner.invoke(app, ["pull", "--kinds", "query", str(tmp_path)])
        assert result.exit_code == 0, result.output
        out = _project.read_all(tmp_path, ["query"])["query"]
        assert len(out) == 1
        assert out[0]["spec"]["sql"] == "SELECT 1"


class TestPush:
    def test_push_dry_run_makes_no_calls(self, tmp_path: Path, monkeypatch, logged_in):
        env = {
            "kind": "dashboard",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Dash", "id": "d1"},
            "spec": {"title": "Dash"},
        }
        _project.write_files(tmp_path, _project.envelope_to_files(env))

        mock_post = MagicMock(side_effect=AssertionError("no POST in dry-run"))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)
        result = runner.invoke(app, ["push", "--dry-run", "--kinds", "dashboard", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "UPDATE" in result.output
        mock_post.assert_not_called()

    def test_push_live_imports(self, tmp_path: Path, monkeypatch, logged_in):
        env = {
            "kind": "dashboard",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Dash", "id": "d1"},
            "spec": {"title": "Dash"},
        }
        _project.write_files(tmp_path, _project.envelope_to_files(env))
        mock_post = MagicMock(return_value=_resp({"id": "d1"}))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)
        result = runner.invoke(app, ["push", "--kinds", "dashboard", str(tmp_path)])
        assert result.exit_code == 0, result.output
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "import"


# ---------------------------------------------------------------------------
# secrets set --connector / pull / delete
# ---------------------------------------------------------------------------


class TestSecretsConnector:
    def test_set_connector_writes_env(self, tmp_path: Path, monkeypatch):
        # Run inside the project dir so _project_root() resolves to tmp_path.
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["secrets", "set", "password", "s3cr3t", "--connector", "Prod Postgres"]
        )
        assert result.exit_code == 0, result.output
        conn = _secrets_files.read_dotenv(_secrets_files.connectors_env_path(tmp_path))
        assert conn["PROD_POSTGRES__PASSWORD"] == "s3cr3t"


class TestSecretsDelete:
    def test_delete_calls_api(self, tmp_path: Path, monkeypatch, logged_in):
        mock_del = MagicMock(return_value=_resp({}))
        monkeypatch.setattr("nubi_cli.client.delete", mock_del)
        result = runner.invoke(app, ["secrets", "delete", "OLD_KEY", "--dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        mock_del.assert_called_once()
        assert mock_del.call_args[0][0] == "secrets/OLD_KEY"


# ---------------------------------------------------------------------------
# whoami / logout / git connect
# ---------------------------------------------------------------------------


class TestWhoami:
    def test_whoami_prints_email(self, monkeypatch, logged_in):
        monkeypatch.setattr(
            "nubi_cli.client.get",
            MagicMock(return_value=_resp({"user": {"id": "u1", "email": "a@b.com", "org_id": "o1"}})),
        )
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0, result.output
        assert "a@b.com" in result.output


class TestGitConnect:
    def test_git_connect_requires_project(self, tmp_path: Path, monkeypatch, logged_in):
        result = runner.invoke(
            app,
            ["git", "connect", "--provider", "github", "--repo-url",
             "https://github.com/a/b", "--token", "t", str(tmp_path)],
        )
        # No project bound → exits non-zero with guidance.
        assert result.exit_code != 0

    def test_git_connect_posts(self, tmp_path: Path, monkeypatch, logged_in):
        _project.write_nubi_yaml(
            tmp_path, _project.build_manifest("P", "proj-1", "org-1")
        )
        _project.write_project_json(tmp_path, {"project_id": "proj-1"})
        mock_post = MagicMock(return_value=_resp({}))
        monkeypatch.setattr("nubi_cli.client.post", mock_post)
        result = runner.invoke(
            app,
            ["git", "connect", "--provider", "github", "--repo-url",
             "https://github.com/a/b", "--token", "tok", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert mock_post.call_args[0][0] == "git/connect"
        assert mock_post.call_args[1]["json"]["project_id"] == "proj-1"
        # nubi.yaml now mirrors the (non-secret) binding.
        manifest = _project.read_nubi_yaml(tmp_path)
        assert manifest["spec"]["git"]["provider"] == "github"
