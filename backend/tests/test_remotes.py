"""Tests for ``app.git.remotes`` — per-project remote git providers (M20-C).

Strategy
--------
NO real git subprocess calls and NO real network calls.

- ``subprocess.run`` is patched at ``app.git.remotes.subprocess.run`` for every
  test that exercises ``_run_git`` / ``clone_or_pull`` / ``push``.
- ``httpx`` is exercised only via ``app.git.remotes._http_json``, which is
  patched directly (or its lazy ``import httpx`` is faked via
  ``sys.modules``) so no network I/O ever happens.
- Filesystem use is limited to ``tmp_path`` (askpass script lifecycle, repo
  dirs) — no writes outside pytest's sandbox.

Coverage
--------
1. ``_scrub`` redacts embedded credentials from arbitrary text.
2. ``_askpass_env`` writes an executable (0700) helper script, wires the
   right env vars, and deletes the script afterward (even on exception).
3. ``_run_git`` — success passthrough, failure raises scrubbed ``AppError``,
   ``allow_fail=True`` swallows a non-zero exit and returns the result.
4. ``_inject_token`` — https injection, drop-existing-creds, ssh passthrough.
5. ``make_provider`` — github/gitlab dispatch (case/whitespace-insensitive),
   unknown provider -> ``AppError('git_provider_unknown', 400)``.
6. ``GitHubProvider`` / ``GitLabProvider`` — provider id, askpass username,
   ``authed_url`` token injection.
7. ``RemoteProvider._owner_repo`` — parses owner/repo, GitLab nested groups,
   raises on an unparsable URL.
8. ``clone_or_pull`` — fresh dir (successful branch clone), fresh dir with a
   failed clone (falls back to init + remote add), existing clone with a
   successful fetch (checkout FETCH_HEAD), existing clone with a failed fetch
   (checkout -B branch only). Token never appears in any argv.
9. ``push`` — clean tree (no commit/push attempted), dirty tree (commit +
   push, returns sha). Token never appears in argv.
10. ``open_change_request`` — GitHub: no-op when branch == default; creates a
    PR when different; treats 422 as "already exists" (None). Same shape for
    GitLab (409, merge_requests).
11. ``_http_json`` — ok status returns parsed JSON, allow-listed status
    returns None, other status raises ``AppError``, network exception raises
    ``AppError``, missing httpx dependency raises a clear ``AppError``.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.errors import AppError
from app.git.remotes import (
    GitHubProvider,
    GitLabProvider,
    _askpass_env,
    _http_json,
    _inject_token,
    _run_git,
    _scrub,
    make_provider,
)


# ---------------------------------------------------------------------------
# _scrub
# ---------------------------------------------------------------------------


def test_scrub_redacts_credentials():
    text = "fatal: https://x-access-token:ghp_secret123@github.com/o/r.git not found"
    scrubbed = _scrub(text)
    assert "ghp_secret123" not in scrubbed
    assert "https://***@github.com/o/r.git" in scrubbed


def test_scrub_handles_empty_and_none():
    assert _scrub("") == ""
    assert _scrub(None) == ""


def test_scrub_leaves_credential_free_text_untouched():
    text = "fatal: repository not found"
    assert _scrub(text) == text


# ---------------------------------------------------------------------------
# _askpass_env
# ---------------------------------------------------------------------------


def test_askpass_env_writes_executable_script_and_cleans_up():
    captured_path: str | None = None
    with _askpass_env("x-access-token", "s3cr3t-token") as env:
        captured_path = env["GIT_ASKPASS"]
        assert os.path.isfile(captured_path)
        mode = stat.S_IMODE(os.stat(captured_path).st_mode)
        assert mode == stat.S_IRWXU  # 0700 — owner-only, executable
        assert env["_NUBI_GIT_USER"] == "x-access-token"
        assert env["_NUBI_GIT_PASS"] == "s3cr3t-token"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
        assert env["GIT_CONFIG_VALUE_0"] == ""
        # The token must never be embedded literally in the script body.
        script_body = Path(captured_path).read_text()
        assert "s3cr3t-token" not in script_body
    # Cleaned up after the context exits.
    assert captured_path is not None
    assert not os.path.exists(captured_path)


def test_askpass_env_cleans_up_even_on_exception():
    captured_path: str | None = None
    with pytest.raises(RuntimeError):
        with _askpass_env("oauth2", "tok") as env:
            captured_path = env["GIT_ASKPASS"]
            raise RuntimeError("boom")
    assert captured_path is not None
    assert not os.path.exists(captured_path)


# ---------------------------------------------------------------------------
# _run_git
# ---------------------------------------------------------------------------


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_run_git_success_returns_completed_process(tmp_path: Path):
    with patch("app.git.remotes.subprocess.run", return_value=_completed(0, "ok\n")) as mock_run:
        result = _run_git(tmp_path, "status")
    assert result.returncode == 0
    assert result.stdout == "ok\n"
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] == str(tmp_path)


def test_run_git_failure_raises_scrubbed_app_error(tmp_path: Path):
    stderr = "remote: https://x-access-token:ghp_leak@github.com/o/r.git denied"
    with patch("app.git.remotes.subprocess.run", return_value=_completed(1, "", stderr)):
        with pytest.raises(AppError) as exc_info:
            _run_git(tmp_path, "push")
    err = exc_info.value
    assert err.code == "git_command_failed"
    assert err.status == 502
    assert "ghp_leak" not in err.message


def test_run_git_allow_fail_returns_result_without_raising(tmp_path: Path):
    with patch("app.git.remotes.subprocess.run", return_value=_completed(128, "", "no such branch")):
        result = _run_git(tmp_path, "fetch", allow_fail=True)
    assert result.returncode == 128


def test_run_git_none_repo_dir_runs_without_cwd():
    with patch("app.git.remotes.subprocess.run", return_value=_completed(0)) as mock_run:
        _run_git(None, "clone", "url", "dest")
    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] is None


# ---------------------------------------------------------------------------
# _inject_token
# ---------------------------------------------------------------------------


def test_inject_token_https_url():
    out = _inject_token("https://github.com/o/r.git", "x-access-token", "TOK")
    assert out == "https://x-access-token:TOK@github.com/o/r.git"


def test_inject_token_drops_existing_credentials():
    out = _inject_token("https://old:stale@github.com/o/r.git", "oauth2", "NEWTOK")
    assert out == "https://oauth2:NEWTOK@github.com/o/r.git"


def test_inject_token_non_https_returned_unchanged():
    ssh_url = "git@github.com:o/r.git"
    assert _inject_token(ssh_url, "x-access-token", "TOK") == ssh_url


def test_inject_token_strips_whitespace():
    out = _inject_token("  https://github.com/o/r.git  ", "oauth2", "TOK")
    assert out == "https://oauth2:TOK@github.com/o/r.git"


# ---------------------------------------------------------------------------
# make_provider
# ---------------------------------------------------------------------------


def test_make_provider_github():
    p = make_provider("github", "https://github.com/o/r.git", "main", "tok")
    assert isinstance(p, GitHubProvider)
    assert p.provider == "github"


def test_make_provider_gitlab():
    p = make_provider("gitlab", "https://gitlab.com/o/r.git", "main", "tok")
    assert isinstance(p, GitLabProvider)
    assert p.provider == "gitlab"


def test_make_provider_case_and_whitespace_insensitive():
    p = make_provider("  GitHub  ", "https://github.com/o/r.git", "main", "tok")
    assert isinstance(p, GitHubProvider)


def test_make_provider_unknown_raises_app_error():
    with pytest.raises(AppError) as exc_info:
        make_provider("bitbucket", "https://x/o/r.git", "main", "tok")
    assert exc_info.value.code == "git_provider_unknown"
    assert exc_info.value.status == 400


def test_provider_defaults_branch_to_main_when_blank():
    p = make_provider("github", "https://github.com/o/r.git", "  ", "tok")
    assert p.branch == "main"


# ---------------------------------------------------------------------------
# Provider identity / authed_url
# ---------------------------------------------------------------------------


def test_github_provider_identity_and_authed_url():
    p = GitHubProvider(repo_url="https://github.com/acme/widgets.git", branch="main", token="ghp_x")
    assert p.provider == "github"
    assert p._askpass_username == "x-access-token"
    assert p.authed_url() == "https://x-access-token:ghp_x@github.com/acme/widgets.git"


def test_gitlab_provider_identity_and_authed_url():
    p = GitLabProvider(repo_url="https://gitlab.com/acme/widgets.git", branch="main", token="glpat_x")
    assert p.provider == "gitlab"
    assert p._askpass_username == "oauth2"
    assert p.authed_url() == "https://oauth2:glpat_x@gitlab.com/acme/widgets.git"


# ---------------------------------------------------------------------------
# _owner_repo
# ---------------------------------------------------------------------------


def test_owner_repo_simple():
    p = GitHubProvider(repo_url="https://github.com/acme/widgets.git", branch="main", token="t")
    assert p._owner_repo() == ("acme", "widgets")


def test_owner_repo_gitlab_nested_group():
    p = GitLabProvider(repo_url="https://gitlab.com/group/subgroup/widgets.git", branch="main", token="t")
    assert p._owner_repo() == ("group/subgroup", "widgets")


def test_owner_repo_unparsable_raises_app_error():
    p = GitHubProvider(repo_url="https://github.com/justonepart", branch="main", token="t")
    with pytest.raises(AppError) as exc_info:
        p._owner_repo()
    assert exc_info.value.code == "git_repo_url_invalid"
    assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# clone_or_pull
# ---------------------------------------------------------------------------


def test_clone_or_pull_fresh_dir_successful_clone(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    provider = GitHubProvider(repo_url="https://github.com/o/r.git", branch="main", token="tok")

    with patch("app.git.remotes._run_git", return_value=_completed(0)) as mock_run_git:
        provider.clone_or_pull(repo_dir)

    calls = [c.args for c in mock_run_git.call_args_list]
    # First call is the branch-scoped clone.
    assert calls[0][1] == "clone"
    assert "tok" not in calls[0]  # token never in argv
    assert calls[0][-2] == "https://github.com/o/r.git"
    # Followed by identity setup (config user.name / user.email).
    assert any(c[1:3] == ("config", "user.name") for c in calls)


def test_clone_or_pull_fresh_dir_clone_fails_falls_back_to_init(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    provider = GitHubProvider(repo_url="https://github.com/o/r.git", branch="feat", token="tok")

    def _side_effect(repo_dir_arg, *args, **kwargs):
        if args and args[0] == "clone":
            return _completed(128, "", "repository not found")
        return _completed(0)

    with patch("app.git.remotes._run_git", side_effect=_side_effect) as mock_run_git:
        provider.clone_or_pull(repo_dir)

    calls = [c.args for c in mock_run_git.call_args_list]
    assert any(c[1] == "init" for c in calls)
    assert any(c[1:4] == ("remote", "add", "origin") for c in calls)
    assert any(c[1:3] == ("checkout", "-B") and c[3] == "feat" for c in calls)


def test_clone_or_pull_existing_clone_fetch_succeeds(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    (repo_dir / ".git").mkdir(parents=True)
    provider = GitHubProvider(repo_url="https://github.com/o/r.git", branch="main", token="tok")

    def _side_effect(repo_dir_arg, *args, **kwargs):
        if args and args[0] == "fetch":
            return _completed(0)
        return _completed(0)

    with patch("app.git.remotes._run_git", side_effect=_side_effect) as mock_run_git:
        provider.clone_or_pull(repo_dir)

    calls = [c.args for c in mock_run_git.call_args_list]
    assert any(c[1:4] == ("remote", "set-url", "origin") for c in calls)
    assert any(c[1] == "fetch" for c in calls)
    assert any(c[1:4] == ("checkout", "-B", "main") and c[4] == "FETCH_HEAD" for c in calls)


def test_clone_or_pull_existing_clone_fetch_fails_checks_out_local_branch(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    (repo_dir / ".git").mkdir(parents=True)
    provider = GitHubProvider(repo_url="https://github.com/o/r.git", branch="new-branch", token="tok")

    def _side_effect(repo_dir_arg, *args, **kwargs):
        if args and args[0] == "fetch":
            return _completed(1, "", "couldn't find remote ref new-branch")
        return _completed(0)

    with patch("app.git.remotes._run_git", side_effect=_side_effect) as mock_run_git:
        provider.clone_or_pull(repo_dir)

    calls = [c.args for c in mock_run_git.call_args_list]
    # No FETCH_HEAD checkout — falls back to a bare local branch checkout.
    assert not any("FETCH_HEAD" in c for c in calls)
    assert any(c[1:3] == ("checkout", "-B") and c[3] == "new-branch" for c in calls)


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


def test_push_clean_tree_no_commit_no_push(tmp_path: Path):
    provider = GitHubProvider(repo_url="https://github.com/o/r.git", branch="main", token="tok")

    def _side_effect(repo_dir_arg, *args, **kwargs):
        if args and args[0] == "status":
            return _completed(0, "")  # nothing staged
        if args and args[0] == "rev-parse":
            return _completed(0, "deadbeef\n")
        return _completed(0)

    with patch("app.git.remotes._run_git", side_effect=_side_effect):
        result = provider.push(tmp_path, "sync commit")

    assert result == {"committed": False, "sha": "deadbeef", "pushed": False}


def test_push_dirty_tree_commits_and_pushes(tmp_path: Path):
    provider = GitHubProvider(repo_url="https://github.com/o/r.git", branch="main", token="tok")

    def _side_effect(repo_dir_arg, *args, **kwargs):
        if args and args[0] == "status":
            return _completed(0, " M file.txt\n")
        if args and args[0] == "rev-parse":
            return _completed(0, "cafebabe\n")
        return _completed(0)

    with patch("app.git.remotes._run_git", side_effect=_side_effect) as mock_run_git:
        result = provider.push(tmp_path, "sync commit")

    assert result == {"committed": True, "sha": "cafebabe", "pushed": True}
    calls = [c.args for c in mock_run_git.call_args_list]
    assert any(c[1:3] == ("commit", "-m") for c in calls)
    push_calls = [c for c in calls if c[1] == "push"]
    assert push_calls
    # The bare URL (no embedded token) must be what's passed to `git push`.
    assert push_calls[0][2] == "https://github.com/o/r.git"
    assert "tok" not in push_calls[0]


# ---------------------------------------------------------------------------
# open_change_request
# ---------------------------------------------------------------------------


def test_github_open_change_request_noop_when_branch_is_default():
    provider = GitHubProvider(repo_url="https://github.com/o/r.git", branch="main", token="tok")
    with patch("app.git.remotes._http_json", return_value={"default_branch": "main"}):
        assert provider.open_change_request("title") is None


def test_github_open_change_request_creates_pr():
    provider = GitHubProvider(repo_url="https://github.com/o/r.git", branch="feat", token="tok")
    responses = [
        {"default_branch": "main"},
        {"html_url": "https://github.com/o/r/pull/9", "number": 9},
    ]
    with patch("app.git.remotes._http_json", side_effect=responses):
        result = provider.open_change_request("My PR", "body text")
    assert result == {"url": "https://github.com/o/r/pull/9", "number": 9}


def test_github_open_change_request_already_exists_returns_none():
    provider = GitHubProvider(repo_url="https://github.com/o/r.git", branch="feat", token="tok")
    responses = [
        {"default_branch": "main"},
        None,  # 422 already-exists mapped to None by _http_json
    ]
    with patch("app.git.remotes._http_json", side_effect=responses):
        assert provider.open_change_request("My PR") is None


def test_gitlab_open_change_request_creates_mr():
    provider = GitLabProvider(repo_url="https://gitlab.com/g/r.git", branch="feat", token="tok")
    responses = [
        {"default_branch": "main"},
        {"web_url": "https://gitlab.com/g/r/-/merge_requests/4", "iid": 4},
    ]
    with patch("app.git.remotes._http_json", side_effect=responses):
        result = provider.open_change_request("My MR", "body")
    assert result == {"url": "https://gitlab.com/g/r/-/merge_requests/4", "number": 4}


def test_gitlab_open_change_request_noop_when_default_branch_unknown():
    provider = GitLabProvider(repo_url="https://gitlab.com/g/r.git", branch="feat", token="tok")
    with patch("app.git.remotes._http_json", return_value=None):
        assert provider.open_change_request("My MR") is None


# ---------------------------------------------------------------------------
# _http_json
# ---------------------------------------------------------------------------


def _mock_httpx_response(status_code: int, json_body: dict | None = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no json")
    resp.text = text
    return resp


def test_http_json_ok_status_returns_parsed_json():
    fake_httpx = MagicMock()
    fake_httpx.request.return_value = _mock_httpx_response(200, {"a": 1})
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        result = _http_json("GET", "https://api.example.com/x", token="t", headers={}, ok=(200,))
    assert result == {"a": 1}


def test_http_json_allowlisted_status_returns_none():
    fake_httpx = MagicMock()
    fake_httpx.request.return_value = _mock_httpx_response(422, text="already exists")
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        result = _http_json(
            "POST", "https://api.example.com/x", token="t", headers={}, ok=(200, 201), allow=(422,)
        )
    assert result is None


def test_http_json_unexpected_status_raises_app_error():
    fake_httpx = MagicMock()
    fake_httpx.request.return_value = _mock_httpx_response(500, text="server exploded")
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        with pytest.raises(AppError) as exc_info:
            _http_json("GET", "https://api.example.com/x", token="t", headers={}, ok=(200,))
    assert exc_info.value.code == "git_api_error"
    assert exc_info.value.status == 502


def test_http_json_network_exception_raises_app_error():
    fake_httpx = MagicMock()
    fake_httpx.request.side_effect = RuntimeError("connection refused")
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        with pytest.raises(AppError) as exc_info:
            _http_json("GET", "https://api.example.com/x", token="t", headers={}, ok=(200,))
    assert exc_info.value.code == "git_api_error"


def test_http_json_missing_httpx_raises_clear_app_error():
    with patch.dict(sys.modules, {"httpx": None}):
        with pytest.raises(AppError) as exc_info:
            _http_json("GET", "https://api.example.com/x", token="t", headers={}, ok=(200,))
    assert exc_info.value.code == "httpx_missing"
    assert exc_info.value.status == 500


def test_http_json_ok_status_with_unparsable_body_returns_empty_dict():
    fake_httpx = MagicMock()
    fake_httpx.request.return_value = _mock_httpx_response(200, json_body=None)
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        result = _http_json("GET", "https://api.example.com/x", token="t", headers={}, ok=(200,))
    assert result == {}
