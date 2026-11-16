"""Git remotes are plain HTTPS repositories on public hosts, and branch names are just names."""

from __future__ import annotations

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

BAD_URLS = [
    "--upload-pack=touch /tmp/pwned",
    "-oProxyCommand=id",
    "ext::sh -c 'id'",
    "file:///etc/passwd",
    "/srv/git/other-tenant.git",
    "ssh://git@github.com/acme/repo.git",
    "git://github.com/acme/repo.git",
    "http://github.com/acme/repo.git",
    "https://user:tok@github.com/acme/repo.git",
    "https://127.0.0.1/acme/repo.git",
    "https://169.254.169.254/latest/meta-data",
    "https://localhost:8000/acme/repo.git",
    "https://github.com/acme/repo.git --upload-pack=x",
    "https://github.com/acme/re po.git",
    "https://github.com/acme/repo.git\n--config=x",
    "https://github.com/../../etc",
    "",
]
BAD_BRANCHES = [
    "--upload-pack=x", "-f", "main..dev", "a b", "main~1", "main^", "a:b", "a?b", "a*b", "a[b", "a\\b",
    "refs/heads/", "x.lock", "/main", "main/", "@{upstream}", "a\x00b", "", ".hidden", "a//b",
]


@pytest.mark.parametrize("url", BAD_URLS, ids=[repr(u)[:40] for u in BAD_URLS])
def test_repo_url_must_be_public_https(url):
    from app.errors import AppError
    from app.git.safety import safe_repo_url

    with pytest.raises(AppError) as exc:
        safe_repo_url(url)
    assert exc.value.status == 400


@pytest.mark.parametrize("branch", BAD_BRANCHES, ids=[repr(b)[:24] for b in BAD_BRANCHES])
def test_branch_must_be_a_plain_ref_name(branch):
    from app.errors import AppError
    from app.git.safety import safe_branch

    with pytest.raises(AppError) as exc:
        safe_branch(branch)
    assert exc.value.status == 400


def test_ordinary_values_pass(monkeypatch):
    from app.git import safety

    monkeypatch.setattr(safety, "_host_is_public", lambda host: True)
    assert safety.safe_repo_url(" https://github.com/acme/widgets.git ") == "https://github.com/acme/widgets.git"
    assert safety.safe_repo_url("https://gitlab.example.com/group/sub/repo") == "https://gitlab.example.com/group/sub/repo"
    for branch in ("main", "env/dev", "feature/add-x_1.2", "release-2026.11"):
        assert safety.safe_branch(branch) == branch


def test_git_always_runs_with_a_restricted_environment():
    from app.git.safety import git_env

    env = git_env({"PATH": "/usr/bin", "GIT_ASKPASS": "/tmp/a"})
    assert env["GIT_ALLOW_PROTOCOL"] == "https"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == "/tmp/a"
    assert git_env(None)["GIT_ALLOW_PROTOCOL"] == "https"


def test_remote_operations_validate_and_separate_arguments(monkeypatch, tmp_path):
    from app.errors import AppError
    from app.git import remotes

    calls: list[tuple] = []

    def _fake_run(repo_dir, *args, allow_fail=False, env=None):
        calls.append(args)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(remotes, "_run_git", _fake_run)
    monkeypatch.setattr("app.git.safety._host_is_public", lambda host: True)

    provider = remotes.GitHubProvider(repo_url="--upload-pack=touch /tmp/x", branch="main", token="t")
    with pytest.raises(AppError):
        provider.clone_or_pull(tmp_path / "a")
    provider = remotes.GitHubProvider(repo_url="https://github.com/acme/w.git", branch="--force", token="t")
    with pytest.raises(AppError):
        provider.clone_or_pull(tmp_path / "b")
    assert calls == []  # nothing ran

    provider = remotes.GitHubProvider(repo_url="https://github.com/acme/w.git", branch="main", token="t")
    provider.clone_or_pull(tmp_path / "c")
    clone = next(c for c in calls if c and c[0] == "clone")
    assert "--" in clone and clone.index("--") < clone.index("https://github.com/acme/w.git")


def test_git_failures_do_not_echo_command_output(monkeypatch, tmp_path):
    import subprocess

    from app.errors import AppError
    from app.git import remotes

    class _Done:
        returncode = 128
        stdout = ""
        stderr = "fatal: could not read '/etc/shadow': root:$6$secret-hash"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done())
    with pytest.raises(AppError) as exc:
        remotes._run_git(tmp_path, "fetch", "origin", "main")
    assert "secret-hash" not in exc.value.message and "/etc/shadow" not in exc.value.message


@pytest.mark.parametrize(
    "body",
    [
        {"repo_url": "--upload-pack=id", "branch": "main"},
        {"repo_url": "ext::sh -c id", "branch": "main"},
        {"repo_url": "https://github.com/acme/w.git", "branch": "--force"},
        {"repo_url": "https://127.0.0.1/acme/w.git", "branch": "main"},
    ],
)
def test_connect_route_refuses_unsafe_bindings(body):
    import uuid
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient

    from app.repos.provider import set_repo
    from tests.security.conftest_helpers import as_user, make_tenant_app

    org = str(uuid.uuid4())
    app, _repo, _users = make_tenant_app({"u": (org, "owner")}, modules=("app.routes.git",))
    update = AsyncMock()
    try:
        with (
            patch("app.routes.git.projects_repo.project_belongs_to_org", new=AsyncMock(return_value=True)),
            patch("app.routes.git.projects_repo.update_project", new=update),
            patch("app.routes.git.get_secret_store") as store,
            TestClient(app, raise_server_exceptions=False) as c,
        ):
            store.return_value.put = AsyncMock()
            resp = c.post(
                "/api/v1/git/connect",
                json={"project_id": str(uuid.uuid4()), "provider": "github", "token": "t", "base_path": "", **body},
                headers=as_user("u"),
            )
        assert resp.status_code == 400, resp.text
        update.assert_not_awaited()
    finally:
        set_repo(None)
