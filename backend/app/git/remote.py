"""Remote git authentication providers for Nubi — M20-B.

Provides a ``RemoteAuth`` provider interface and concrete implementations for:

- ``GitHubAppAuth``   — GitHub App JWT flow → installation access token.
- ``GitLabTokenAuth`` — GitLab personal/project token via HTTPS basic auth.
- ``NullRemote``      — default no-op (records intent, makes no network calls).

A factory ``make_remote_auth(config)`` selects the right provider based on the
``GIT_REMOTE_PROVIDER`` setting (``'github_app'``, ``'gitlab'``, or ``'none'``).

Design notes
------------
- PyJWT and httpx are imported **lazily** inside the methods that need them.
  This keeps the import graph clean and lets ``NullRemote`` and
  ``GitLabTokenAuth`` work without either library installed.
- ``GitHubAppAuth.installation_token()`` is intentionally a public method so
  tests can exercise the token-exchange logic independently of the URL helper.
- Token caching: the installation access token is cached in-process until 60 s
  before its expiry, at which point the next call refreshes it transparently.
"""

from __future__ import annotations

import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.errors import AppError

if TYPE_CHECKING:
    pass  # keep runtime import graph clean


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------


class RemoteAuth(ABC):
    """Abstract base for remote push authentication providers.

    Concrete sub-classes supply credentials so that ``GitSync.push`` can
    call ``git push`` to a remote HTTPS URL without interactive prompts.
    """

    @abstractmethod
    def authed_url(self, repo_url: str) -> str:
        """Return *repo_url* with embedded credentials for HTTPS push.

        Parameters
        ----------
        repo_url:
            The plain HTTPS clone URL, e.g.
            ``https://github.com/org/repo.git``.

        Returns
        -------
        str
            A URL like ``https://<user>:<token>@github.com/org/repo.git``
            that git can use without further prompting.
        """

    @abstractmethod
    def push(
        self,
        repo_dir: Path,
        branch: str = "main",
        remote_url: str | None = None,
    ) -> None:
        """Push *branch* of the repo at *repo_dir* to the remote.

        Parameters
        ----------
        repo_dir:
            Absolute path to the local git repository.
        branch:
            Branch name to push (default ``'main'``).
        remote_url:
            Override the remote URL.  When ``None`` the provider must have a
            URL configured internally (or infer one).
        """


# ---------------------------------------------------------------------------
# NullRemote
# ---------------------------------------------------------------------------


class NullRemote(RemoteAuth):
    """No-op remote — records intent but never makes network calls.

    This is the default when ``GIT_REMOTE_PROVIDER`` is unset or ``'none'``.
    It satisfies the interface so callers don't need to branch on None.
    """

    def authed_url(self, repo_url: str) -> str:
        """Return *repo_url* unchanged (no credentials)."""
        return repo_url

    def push(
        self,
        repo_dir: Path,
        branch: str = "main",
        remote_url: str | None = None,
    ) -> None:
        """No-op: record intent without making any network calls."""
        # Intentionally does nothing.  Remote push is disabled.
        pass


# ---------------------------------------------------------------------------
# GitLabTokenAuth
# ---------------------------------------------------------------------------


class GitLabTokenAuth(RemoteAuth):
    """Push to GitLab using a personal or project access token.

    Uses HTTPS basic authentication with the ``oauth2`` username convention
    supported by all GitLab editions::

        https://oauth2:<token>@gitlab.com/org/repo.git

    Parameters
    ----------
    token:
        A GitLab personal access token, project access token, or CI_JOB_TOKEN
        with ``write_repository`` permission.
    host:
        GitLab host (default ``'gitlab.com'``).  Override for self-hosted
        instances, e.g. ``'gitlab.example.com'``.
    """

    def __init__(self, token: str, host: str = "gitlab.com") -> None:
        self.token = token
        self.host = host.rstrip("/")

    def authed_url(self, repo_url: str) -> str:
        """Inject ``oauth2:<token>@`` into the HTTPS URL.

        Replaces the ``https://<host>`` prefix with
        ``https://oauth2:<token>@<host>``.
        """
        prefix = f"https://{self.host}"
        if repo_url.startswith(prefix):
            path_part = repo_url[len(prefix):]
            return f"https://oauth2:{self.token}@{self.host}{path_part}"
        # Fallback: inject before the first slash after the scheme
        if repo_url.startswith("https://"):
            rest = repo_url[len("https://"):]
            return f"https://oauth2:{self.token}@{rest}"
        return repo_url

    def push(
        self,
        repo_dir: Path,
        branch: str = "main",
        remote_url: str | None = None,
    ) -> None:
        """Run ``git push`` with the token-embedded URL."""
        if remote_url is None:
            raise AppError(
                "git_push_no_url",
                "GitLabTokenAuth.push() requires remote_url.",
                400,
            )
        # SECURITY (B5): pass the BARE url + credentials via GIT_ASKPASS — the
        # token is never placed in argv (do NOT use authed_url here).
        _git_push(repo_dir, remote_url, branch, username="oauth2", password=self.token)


# ---------------------------------------------------------------------------
# GitHubAppAuth
# ---------------------------------------------------------------------------

# Clock skew tolerance and token refresh buffer (seconds).
_JWT_VALIDITY_S = 600       # 10 minutes — GitHub's max
_TOKEN_REFRESH_BUFFER_S = 60  # refresh the installation token 60 s before expiry


class GitHubAppAuth(RemoteAuth):
    """Push to GitHub using a GitHub App installation access token.

    Flow
    ----
    1. Mint a short-lived JWT (RS256, 10 min) signed with the App private key.
    2. Exchange it for an **installation access token** via
       ``POST /app/installations/{id}/access_tokens`` (valid for ~1 hour).
    3. Use the installation token as HTTPS credentials:
       ``https://x-access-token:<token>@github.com/<org>/<repo>.git``.

    The installation token is cached in-process and transparently refreshed
    60 s before its expiry.

    Parameters
    ----------
    app_id:
        The numeric GitHub App ID (string or int).
    private_key:
        PEM-encoded RSA private key registered for the App.
    installation_id:
        The installation ID for the target org/user.
    """

    def __init__(
        self,
        app_id: str | int,
        private_key: str,
        installation_id: str | int,
    ) -> None:
        self.app_id = str(app_id)
        self.private_key = private_key
        self.installation_id = str(installation_id)

        # Cache fields
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0  # unix timestamp

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def _mint_jwt(self) -> str:
        """Return a signed GitHub App JWT (RS256, 10 minute validity).

        Raises
        ------
        AppError
            If PyJWT is not installed.
        """
        try:
            import jwt as pyjwt  # lazy import
        except ImportError as exc:
            raise AppError(
                "jwt_missing",
                "PyJWT is required for GitHub App authentication. "
                "Install it with: pip install PyJWT[cryptography]",
                500,
            ) from exc

        now = int(time.time())
        payload: dict[str, Any] = {
            "iat": now - 10,          # slight back-date for clock skew
            "exp": now + _JWT_VALIDITY_S,
            "iss": self.app_id,
        }
        try:
            token: str = pyjwt.encode(payload, self.private_key, algorithm="RS256")
        except Exception as exc:
            raise AppError(
                "jwt_sign_error",
                f"Failed to sign GitHub App JWT: {exc}",
                500,
            ) from exc
        return token

    def installation_token(self) -> str:
        """Return a valid installation access token, refreshing if needed.

        Makes a real HTTP call to the GitHub API the first time (or when the
        cached token is about to expire).

        Raises
        ------
        AppError
            On network error or non-2xx response from GitHub.
        """
        now = time.time()
        if self._cached_token and now < self._token_expires_at - _TOKEN_REFRESH_BUFFER_S:
            return self._cached_token

        jwt_token = self._mint_jwt()
        token, expires_at = self._exchange_jwt_for_token(jwt_token)

        self._cached_token = token
        self._token_expires_at = expires_at
        return token

    def _exchange_jwt_for_token(self, jwt_token: str) -> tuple[str, float]:
        """POST to GitHub API to exchange a JWT for an installation token.

        Parameters
        ----------
        jwt_token:
            A freshly minted GitHub App JWT.

        Returns
        -------
        tuple[str, float]
            ``(access_token, expiry_unix_timestamp)``

        Raises
        ------
        AppError
            If httpx is missing or the GitHub API returns an error.
        """
        try:
            import httpx  # lazy import
        except ImportError as exc:
            raise AppError(
                "httpx_missing",
                "httpx is required for GitHub App authentication. "
                "Install it with: pip install httpx",
                500,
            ) from exc

        url = (
            f"https://api.github.com/app/installations"
            f"/{self.installation_id}/access_tokens"
        )
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            resp = httpx.post(url, headers=headers)
        except Exception as exc:
            raise AppError(
                "github_api_error",
                f"HTTP request to GitHub API failed: {exc}",
                502,
            ) from exc

        if resp.status_code not in (200, 201):
            raise AppError(
                "github_api_error",
                f"GitHub API returned {resp.status_code}: {resp.text[:200]}",
                502,
            )

        data = resp.json()
        token: str = data["token"]

        # Parse the expiry time (ISO-8601 string) into a unix timestamp.
        expires_at_str: str = data.get("expires_at", "")
        expires_at_ts = _parse_iso_to_unix(expires_at_str)

        return token, expires_at_ts

    # ------------------------------------------------------------------
    # RemoteAuth interface
    # ------------------------------------------------------------------

    def authed_url(self, repo_url: str) -> str:
        """Return *repo_url* with an embedded installation token.

        Replaces the ``https://github.com`` prefix with
        ``https://x-access-token:<token>@github.com``.
        """
        token = self.installation_token()
        prefix = "https://github.com"
        if repo_url.startswith(prefix):
            path_part = repo_url[len(prefix):]
            return f"https://x-access-token:{token}@github.com{path_part}"
        # Fallback: inject before first host portion after scheme
        if repo_url.startswith("https://"):
            rest = repo_url[len("https://"):]
            return f"https://x-access-token:{token}@{rest}"
        return repo_url

    def push(
        self,
        repo_dir: Path,
        branch: str = "main",
        remote_url: str | None = None,
    ) -> None:
        """Push *branch* using an installation access token."""
        if remote_url is None:
            raise AppError(
                "git_push_no_url",
                "GitHubAppAuth.push() requires remote_url.",
                400,
            )
        # SECURITY (B5): pass the BARE url + the installation token via
        # GIT_ASKPASS — the token is never placed in argv (do NOT use authed_url).
        _git_push(
            repo_dir,
            remote_url,
            branch,
            username="x-access-token",
            password=self.installation_token(),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_remote_auth(config: Any) -> RemoteAuth:
    """Return the appropriate ``RemoteAuth`` implementation for *config*.

    Parameters
    ----------
    config:
        A ``Settings`` instance (or any object with the git-remote attributes).
        Reads ``GIT_REMOTE_PROVIDER``, ``GITHUB_APP_ID``,
        ``GITHUB_APP_PRIVATE_KEY``, ``GITHUB_APP_INSTALLATION_ID``,
        ``GITLAB_TOKEN``, ``GITLAB_HOST``.

    Returns
    -------
    RemoteAuth
        One of ``GitHubAppAuth``, ``GitLabTokenAuth``, or ``NullRemote``.

    Raises
    ------
    AppError
        If the provider is ``'github_app'`` or ``'gitlab'`` but required
        credentials are missing from *config*.
    """
    provider: str = (getattr(config, "GIT_REMOTE_PROVIDER", None) or "").lower()

    if provider == "github_app":
        app_id = getattr(config, "GITHUB_APP_ID", None) or ""
        private_key = getattr(config, "GITHUB_APP_PRIVATE_KEY", None) or ""
        installation_id = getattr(config, "GITHUB_APP_INSTALLATION_ID", None) or ""
        if not all([app_id, private_key, installation_id]):
            raise AppError(
                "git_remote_config_error",
                "GIT_REMOTE_PROVIDER=github_app requires GITHUB_APP_ID, "
                "GITHUB_APP_PRIVATE_KEY, and GITHUB_APP_INSTALLATION_ID.",
                500,
            )
        return GitHubAppAuth(
            app_id=app_id,
            private_key=private_key,
            installation_id=installation_id,
        )

    if provider == "gitlab":
        token = getattr(config, "GITLAB_TOKEN", None) or ""
        if not token:
            raise AppError(
                "git_remote_config_error",
                "GIT_REMOTE_PROVIDER=gitlab requires GITLAB_TOKEN.",
                500,
            )
        host = getattr(config, "GITLAB_HOST", None) or "gitlab.com"
        return GitLabTokenAuth(token=token, host=host)

    # Default: no-op
    return NullRemote()


# ---------------------------------------------------------------------------
# Internal git helper
# ---------------------------------------------------------------------------


def _git_push(
    repo_dir: Path,
    remote_url: str,
    branch: str,
    *,
    username: str,
    password: str,
) -> None:
    """Run ``git push <bare-url> <branch>`` with credentials via GIT_ASKPASS.

    SECURITY (B5): the PAT/token is delivered through the environment using the
    hardened ``GIT_ASKPASS`` helper (shared with ``app.git.remotes``) — it NEVER
    appears in the subprocess argv (``ps``/``/proc`` exposure). The URL passed to
    git is the BARE ``remote_url`` with no ``user:token@`` embedded.

    Raises
    ------
    AppError
        If the push fails (stderr is scrubbed of any leaked credentials first).
    """
    # Lazy import to keep module load order simple (remotes.py owns the hardened
    # askpass helper; no import cycle — remotes.py does not import this module).
    from app.git.remotes import _askpass_env, _scrub  # noqa: PLC0415

    with _askpass_env(username, password) as env:
        result = subprocess.run(
            ["git", "push", remote_url, branch],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    if result.returncode != 0:
        raise AppError(
            "git_push_failed",
            f"git push failed: {_scrub(result.stderr)[:400]}",
            502,
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _parse_iso_to_unix(iso_str: str) -> float:
    """Parse an ISO-8601 timestamp string to a Unix timestamp float.

    Handles the ``2024-01-15T12:34:56Z`` format returned by GitHub.
    Falls back to ``time.time() + 3600`` if parsing fails.
    """
    if not iso_str:
        return time.time() + 3600.0

    try:
        from datetime import datetime, timezone

        # GitHub returns Z-suffix; Python 3.11+ handles it natively.
        # For 3.10 compatibility replace Z with +00:00.
        normalized = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
    except Exception:
        return time.time() + 3600.0
