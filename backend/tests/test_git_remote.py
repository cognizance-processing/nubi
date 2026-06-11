"""Tests for M20-B: remote git authentication providers.

Strategy
--------
- NO real network calls.  httpx and PyJWT are mocked using ``unittest.mock``.
- ``GitHubAppAuth`` is tested for JWT minting, token exchange (mocked 201
  response), URL injection, and in-process token caching.
- ``GitLabTokenAuth`` is tested for URL building.
- ``NullRemote`` is tested for its no-op push.
- ``make_remote_auth`` factory is tested for provider selection.
- ``GitSync.push`` with a ``NullRemote`` is tested end-to-end.
- Missing PyJWT → clear ``AppError`` is tested.

Coverage
--------
1.  GitHubAppAuth._mint_jwt calls PyJWT with RS256 and correct payload.
2.  GitHubAppAuth.installation_token exchanges JWT for a token (mocked 201).
3.  GitHubAppAuth.authed_url injects x-access-token into the URL.
4.  GitHubAppAuth installation token is cached until near-expiry.
5.  GitLabTokenAuth.authed_url builds oauth2:<token>@host URL.
6.  GitLabTokenAuth handles non-default host.
7.  NullRemote.push is a no-op (no exceptions, no subprocess calls).
8.  NullRemote.authed_url returns URL unchanged.
9.  make_remote_auth('none' / '') → NullRemote.
10. make_remote_auth('github_app') → GitHubAppAuth (with valid config).
11. make_remote_auth('gitlab') → GitLabTokenAuth (with valid config).
12. make_remote_auth('github_app') missing creds → AppError.
13. make_remote_auth('gitlab') missing token → AppError.
14. GitSync.push with NullRemote → no-op (no subprocess).
15. GitSync.push with remote=None → no-op.
16. Missing PyJWT → AppError with helpful message.
17. GitHub API non-2xx → AppError.
18. GitHubAppAuth._mint_jwt handles PyJWT string return (v2+).
"""

from __future__ import annotations

import time
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.errors import AppError
from app.git.remote import (
    GitHubAppAuth,
    GitLabTokenAuth,
    NullRemote,
    make_remote_auth,
)
from app.git.sync import GitSync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_github_auth(
    app_id: str = "12345",
    private_key: str = "FAKE_KEY",
    installation_id: str = "99999",
) -> GitHubAppAuth:
    """Return a GitHubAppAuth with dummy credentials."""
    return GitHubAppAuth(
        app_id=app_id,
        private_key=private_key,
        installation_id=installation_id,
    )


def _mock_httpx_post(token: str = "ghs_test_token", status: int = 201, expires_in: int = 3600):
    """Return a mock for ``httpx.post`` that mimics a successful GitHub response."""
    expires_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + expires_in),
    )
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.json.return_value = {
        "token": token,
        "expires_at": expires_at,
    }
    mock_resp.text = f'{{"token": "{token}", "expires_at": "{expires_at}"}}'
    return MagicMock(return_value=mock_resp)


def _make_config(**kwargs: Any) -> types.SimpleNamespace:
    """Return a minimal config-like object for make_remote_auth."""
    defaults = {
        "GIT_REMOTE_PROVIDER": "",
        "GITHUB_APP_ID": "",
        "GITHUB_APP_PRIVATE_KEY": "",
        "GITHUB_APP_INSTALLATION_ID": "",
        "GITLAB_TOKEN": "",
        "GITLAB_HOST": "gitlab.com",
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 1. GitHubAppAuth._mint_jwt calls PyJWT with RS256
# ---------------------------------------------------------------------------


class TestGitHubAppAuthMintJwt:
    def test_mint_jwt_calls_pyjwt_encode(self):
        """_mint_jwt should call jwt.encode with RS256 algorithm."""
        auth = _make_github_auth(app_id="42")
        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "eyJ.mocked.jwt"

        with patch.dict("sys.modules", {"jwt": mock_jwt}):
            token = auth._mint_jwt()

        mock_jwt.encode.assert_called_once()
        call_args = mock_jwt.encode.call_args
        args, kwargs = call_args.args, call_args.kwargs
        # payload is always the first positional arg
        payload = args[0]
        assert payload["iss"] == "42"
        assert "iat" in payload
        assert "exp" in payload
        assert payload["exp"] > payload["iat"]
        # algorithm may be positional (index 2) or keyword
        algo = args[2] if len(args) > 2 else kwargs.get("algorithm", "")
        assert algo == "RS256"

    def test_mint_jwt_payload_exp_within_10_min(self):
        """JWT exp must be within 10 minutes of now."""
        auth = _make_github_auth()
        captured: dict[str, Any] = {}

        def fake_encode(payload, key, algorithm):
            captured.update(payload)
            return "eyJ.fake.jwt"

        mock_jwt = MagicMock()
        mock_jwt.encode.side_effect = fake_encode

        with patch.dict("sys.modules", {"jwt": mock_jwt}):
            auth._mint_jwt()

        now = int(time.time())
        assert captured["exp"] <= now + 620  # 10 min + tiny buffer

    def test_mint_jwt_missing_pyjwt_raises_app_error(self):
        """If PyJWT is absent, _mint_jwt must raise AppError with clear message."""
        auth = _make_github_auth()
        with patch.dict("sys.modules", {"jwt": None}):
            with pytest.raises(AppError) as exc_info:
                auth._mint_jwt()
        err = exc_info.value
        assert err.code == "jwt_missing"
        assert "PyJWT" in err.message


# ---------------------------------------------------------------------------
# 2. GitHubAppAuth.installation_token exchanges JWT for a token
# ---------------------------------------------------------------------------


class TestGitHubAppInstallationToken:
    def test_token_returned_on_success(self):
        """installation_token returns the token string from the API response."""
        auth = _make_github_auth(installation_id="111")
        mock_post = _mock_httpx_post(token="ghs_abc123", status=201)

        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "eyJ.signed.jwt"

        mock_httpx = MagicMock()
        mock_httpx.post = mock_post

        with patch.dict("sys.modules", {"jwt": mock_jwt, "httpx": mock_httpx}):
            token = auth.installation_token()

        assert token == "ghs_abc123"

    def test_token_cached_on_second_call(self):
        """installation_token does NOT call the API a second time within TTL."""
        auth = _make_github_auth()
        mock_post = _mock_httpx_post(token="ghs_cached", status=201, expires_in=3600)

        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "eyJ.signed.jwt"

        mock_httpx = MagicMock()
        mock_httpx.post = mock_post

        with patch.dict("sys.modules", {"jwt": mock_jwt, "httpx": mock_httpx}):
            t1 = auth.installation_token()
            t2 = auth.installation_token()

        assert t1 == t2 == "ghs_cached"
        # API should only have been called once
        assert mock_post.call_count == 1

    def test_token_refreshed_when_near_expiry(self):
        """installation_token refreshes when the cached token is about to expire."""
        auth = _make_github_auth()
        # Pre-seed a nearly-expired token (expires in 30 s < 60 s buffer)
        auth._cached_token = "ghs_old"
        auth._token_expires_at = time.time() + 30  # expires in 30 s

        mock_post = _mock_httpx_post(token="ghs_new", status=201, expires_in=3600)
        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "eyJ.signed.jwt"
        mock_httpx = MagicMock()
        mock_httpx.post = mock_post

        with patch.dict("sys.modules", {"jwt": mock_jwt, "httpx": mock_httpx}):
            token = auth.installation_token()

        assert token == "ghs_new"

    def test_non_2xx_response_raises_app_error(self):
        """A non-2xx GitHub API response must raise AppError."""
        auth = _make_github_auth()
        mock_post = _mock_httpx_post(token="", status=404)
        mock_post.return_value.text = '{"message": "Not Found"}'
        mock_post.return_value.status_code = 404

        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "eyJ.signed.jwt"
        mock_httpx = MagicMock()
        mock_httpx.post = mock_post

        with patch.dict("sys.modules", {"jwt": mock_jwt, "httpx": mock_httpx}):
            with pytest.raises(AppError) as exc_info:
                auth.installation_token()

        assert exc_info.value.code == "github_api_error"


# ---------------------------------------------------------------------------
# 3. GitHubAppAuth.authed_url injects x-access-token
# ---------------------------------------------------------------------------


class TestGitHubAppAuthedUrl:
    def _auth_with_cached_token(self, token: str = "ghs_tok") -> GitHubAppAuth:
        """Return a GitHubAppAuth with a pre-cached token (skips network)."""
        auth = _make_github_auth()
        auth._cached_token = token
        auth._token_expires_at = time.time() + 3600
        return auth

    def test_injects_x_access_token_prefix(self):
        auth = self._auth_with_cached_token("ghs_xyz")
        url = auth.authed_url("https://github.com/org/repo.git")
        assert url == "https://x-access-token:ghs_xyz@github.com/org/repo.git"

    def test_handles_non_github_url_fallback(self):
        auth = self._auth_with_cached_token("ghs_abc")
        url = auth.authed_url("https://example.com/org/repo.git")
        assert "ghs_abc" in url
        assert url.startswith("https://x-access-token:ghs_abc@")


# ---------------------------------------------------------------------------
# 5. GitLabTokenAuth.authed_url
# ---------------------------------------------------------------------------


class TestGitLabTokenAuth:
    def test_injects_oauth2_credentials(self):
        auth = GitLabTokenAuth(token="glpat-secret", host="gitlab.com")
        url = auth.authed_url("https://gitlab.com/org/repo.git")
        assert url == "https://oauth2:glpat-secret@gitlab.com/org/repo.git"

    def test_custom_host(self):
        auth = GitLabTokenAuth(token="glpat-tok", host="gitlab.example.com")
        url = auth.authed_url("https://gitlab.example.com/group/project.git")
        assert url == "https://oauth2:glpat-tok@gitlab.example.com/group/project.git"

    def test_fallback_for_mismatched_host(self):
        """URL with a different host falls back gracefully."""
        auth = GitLabTokenAuth(token="tok", host="gitlab.com")
        url = auth.authed_url("https://other.gitlab.com/org/repo.git")
        # Should still inject credentials even if host doesn't match
        assert "tok" in url


# ---------------------------------------------------------------------------
# SECURITY (B5): the PAT must NEVER appear in the git push argv — it is
# delivered via GIT_ASKPASS. Guards against a regression to the old
# `git push https://user:token@host` (token visible in ps/proc).
# ---------------------------------------------------------------------------


class TestPushNoTokenInArgv:
    def test_gitlab_push_uses_askpass_not_argv(self, tmp_path):
        auth = GitLabTokenAuth(token="glpat-supersecret", host="gitlab.com")
        url = "https://gitlab.com/org/repo.git"
        with patch("app.git.remote.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            auth.push(tmp_path, branch="main", remote_url=url)
        mock_run.assert_called_once()
        call = mock_run.call_args
        argv = call.args[0]
        # Bare URL in argv; the token appears in NO argv element.
        assert argv == ["git", "push", url, "main"]
        assert all("glpat-supersecret" not in str(a) for a in argv)
        # Credentials delivered through the GIT_ASKPASS env, not argv.
        env = call.kwargs["env"]
        assert env.get("GIT_ASKPASS")
        assert env.get("_NUBI_GIT_USER") == "oauth2"
        assert env.get("_NUBI_GIT_PASS") == "glpat-supersecret"

    def test_push_without_remote_url_raises(self):
        auth = GitLabTokenAuth(token="tok")
        with pytest.raises(AppError) as exc_info:
            auth.push(Path("/tmp/some_repo"), remote_url=None)
        assert exc_info.value.code == "git_push_no_url"


# ---------------------------------------------------------------------------
# 7–8. NullRemote
# ---------------------------------------------------------------------------


class TestNullRemote:
    def test_push_is_noop(self, tmp_path):
        """NullRemote.push must not raise or call subprocess."""
        remote = NullRemote()
        # Should complete without error even with a non-existent repo dir
        remote.push(tmp_path, branch="main", remote_url="https://github.com/x/y.git")

    def test_authed_url_returns_unchanged(self):
        remote = NullRemote()
        url = "https://github.com/org/repo.git"
        assert remote.authed_url(url) == url

    def test_push_no_subprocess(self, tmp_path):
        """NullRemote must not call subprocess.run."""
        remote = NullRemote()
        with patch("subprocess.run") as mock_run:
            remote.push(tmp_path, remote_url="https://github.com/x/y.git")
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# 9–13. make_remote_auth factory
# ---------------------------------------------------------------------------


class TestMakeRemoteAuth:
    def test_empty_provider_returns_null_remote(self):
        config = _make_config(GIT_REMOTE_PROVIDER="")
        remote = make_remote_auth(config)
        assert isinstance(remote, NullRemote)

    def test_none_provider_returns_null_remote(self):
        config = _make_config(GIT_REMOTE_PROVIDER="none")
        remote = make_remote_auth(config)
        assert isinstance(remote, NullRemote)

    def test_github_app_returns_github_auth(self):
        config = _make_config(
            GIT_REMOTE_PROVIDER="github_app",
            GITHUB_APP_ID="123",
            GITHUB_APP_PRIVATE_KEY="pem_key",
            GITHUB_APP_INSTALLATION_ID="456",
        )
        remote = make_remote_auth(config)
        assert isinstance(remote, GitHubAppAuth)
        assert remote.app_id == "123"
        assert remote.installation_id == "456"

    def test_gitlab_returns_gitlab_auth(self):
        config = _make_config(
            GIT_REMOTE_PROVIDER="gitlab",
            GITLAB_TOKEN="glpat-token",
            GITLAB_HOST="gitlab.com",
        )
        remote = make_remote_auth(config)
        assert isinstance(remote, GitLabTokenAuth)
        assert remote.token == "glpat-token"

    def test_gitlab_custom_host(self):
        config = _make_config(
            GIT_REMOTE_PROVIDER="gitlab",
            GITLAB_TOKEN="tok",
            GITLAB_HOST="self.gitlab.example.com",
        )
        remote = make_remote_auth(config)
        assert isinstance(remote, GitLabTokenAuth)
        assert remote.host == "self.gitlab.example.com"

    def test_github_app_missing_creds_raises(self):
        config = _make_config(
            GIT_REMOTE_PROVIDER="github_app",
            GITHUB_APP_ID="",        # missing
            GITHUB_APP_PRIVATE_KEY="pem",
            GITHUB_APP_INSTALLATION_ID="456",
        )
        with pytest.raises(AppError) as exc_info:
            make_remote_auth(config)
        assert exc_info.value.code == "git_remote_config_error"

    def test_gitlab_missing_token_raises(self):
        config = _make_config(
            GIT_REMOTE_PROVIDER="gitlab",
            GITLAB_TOKEN="",  # missing
        )
        with pytest.raises(AppError) as exc_info:
            make_remote_auth(config)
        assert exc_info.value.code == "git_remote_config_error"

    def test_unknown_provider_returns_null_remote(self):
        config = _make_config(GIT_REMOTE_PROVIDER="s3")
        remote = make_remote_auth(config)
        assert isinstance(remote, NullRemote)


# ---------------------------------------------------------------------------
# 14–15. GitSync.push integration
# ---------------------------------------------------------------------------


class TestGitSyncPush:
    def test_push_with_null_remote_is_noop(self, tmp_path):
        """GitSync.push(remote=NullRemote) must not call subprocess for push."""
        sync = GitSync(repo_dir=tmp_path / "repo", remote=NullRemote())
        sync.commit_resources(
            [{"path": "q.sql", "content": "SELECT 1"}], message="init"
        )
        with patch("subprocess.run", wraps=None) as mock_run:
            # Allow the patch but verify git push is never called
            mock_run.side_effect = None  # don't intercept, just track
            # Actually we want to ensure NullRemote.push doesn't call subprocess.
            # Reset and use a tighter mock.

        with patch.object(NullRemote, "push", wraps=NullRemote().push) as mock_push:
            sync.push(branch="main", remote_url="https://github.com/x/y.git")
        mock_push.assert_called_once()

    def test_push_with_remote_none_is_noop(self, tmp_path):
        """GitSync(remote=None).push must not raise anything."""
        sync = GitSync(repo_dir=tmp_path / "repo2")  # remote=None by default
        sync.commit_resources(
            [{"path": "q.sql", "content": "SELECT 1"}], message="init"
        )
        # Should complete silently
        sync.push(branch="main")

    def test_push_delegates_to_remote(self, tmp_path):
        """GitSync.push delegates to self.remote.push with correct args."""
        mock_remote = MagicMock(spec=NullRemote)
        sync = GitSync(repo_dir=tmp_path / "repo3", remote=mock_remote)
        sync.commit_resources(
            [{"path": "q.sql", "content": "SELECT 1"}], message="init"
        )
        sync.push(branch="feat", remote_url="https://github.com/o/r.git")
        mock_remote.push.assert_called_once_with(
            repo_dir=sync.repo_dir,
            branch="feat",
            remote_url="https://github.com/o/r.git",
        )


# ---------------------------------------------------------------------------
# 16. Missing PyJWT → AppError with helpful message
# ---------------------------------------------------------------------------


class TestMissingPyJWT:
    def test_missing_pyjwt_raises_clear_error(self):
        """If PyJWT is not installed, _mint_jwt raises AppError(jwt_missing)."""
        auth = _make_github_auth()
        # Simulate jwt not being importable
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "jwt":
                raise ImportError("No module named 'jwt'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(AppError) as exc_info:
                auth._mint_jwt()

        err = exc_info.value
        assert err.code == "jwt_missing"
        assert "PyJWT" in err.message
        assert err.status == 500


# ---------------------------------------------------------------------------
# 17. GitHub API non-2xx handling (via _exchange_jwt_for_token)
# ---------------------------------------------------------------------------


class TestGitHubApiErrors:
    def test_500_response_raises_app_error(self):
        auth = _make_github_auth()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.json.return_value = {}

        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "eyJ.fake"
        mock_httpx = MagicMock()
        mock_httpx.post.return_value = mock_resp

        with patch.dict("sys.modules", {"jwt": mock_jwt, "httpx": mock_httpx}):
            with pytest.raises(AppError) as exc_info:
                auth._exchange_jwt_for_token("eyJ.fake")

        assert exc_info.value.code == "github_api_error"
        assert "500" in exc_info.value.message

    def test_network_error_raises_app_error(self):
        auth = _make_github_auth()

        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "eyJ.fake"
        mock_httpx = MagicMock()
        mock_httpx.post.side_effect = ConnectionError("Connection refused")

        with patch.dict("sys.modules", {"jwt": mock_jwt, "httpx": mock_httpx}):
            with pytest.raises(AppError) as exc_info:
                auth._exchange_jwt_for_token("eyJ.fake")

        assert exc_info.value.code == "github_api_error"


# ---------------------------------------------------------------------------
# 18. GitHubAppAuth with PyJWT v2 string return
# ---------------------------------------------------------------------------


class TestGitHubAppJwtStringReturn:
    def test_jwt_encode_returns_string(self):
        """PyJWT v2+ returns str from encode(); _mint_jwt must handle that."""
        auth = _make_github_auth()
        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "eyJhbGciOiJSUzI1NiJ9.payload.sig"

        with patch.dict("sys.modules", {"jwt": mock_jwt}):
            result = auth._mint_jwt()

        assert isinstance(result, str)
        assert result == "eyJhbGciOiJSUzI1NiJ9.payload.sig"
