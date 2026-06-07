"""Project-scoped remote git providers — M20-C.

Where ``app.git.remote`` provides org-level GitHub-App / GitLab-token auth keyed
off process settings, this module provides **per-project** providers keyed off a
project's ``git`` binding and a project-scoped **PAT / deploy-token** held in the
secret store.

Each provider knows how to drive a local working clone against a single remote
branch:

- ``clone_or_pull(repo_dir)`` — clone the branch into *repo_dir* (or fetch +
  hard-reset it to the remote tip if a clone already exists).
- ``push(repo_dir, message)`` — stage everything, commit (if there is a diff),
  and push the branch to the remote.
- ``open_change_request(title, body)`` — *optional* — open a PR / MR (only used
  when the configured branch differs from the repo default; best-effort).

Auth model (MVP)
----------------
HTTPS basic auth with the PAT embedded in the URL:

- GitHub: ``https://x-access-token:<token>@github.com/<owner>/<repo>.git``
- GitLab: ``https://oauth2:<token>@gitlab.com/<group>/<repo>.git``

The token is **never** written into the working tree or committed; it only ever
appears in the transient remote URL passed to ``git`` for fetch/push.

``requests``/``httpx`` are imported lazily inside ``open_change_request`` so the
clone/pull/push path has no third-party dependency beyond the ``git`` CLI.
"""

from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.errors import AppError

# Author used for sync commits when none is supplied.
DEFAULT_AUTHOR_NAME = "Nubi Git Sync"
DEFAULT_AUTHOR_EMAIL = "nubi-git-sync@nubi.local"


# ---------------------------------------------------------------------------
# git CLI helper
# ---------------------------------------------------------------------------


def _run_git(repo_dir: Path | None, *args: str, allow_fail: bool = False) -> subprocess.CompletedProcess[str]:
    """Run ``git *args`` (optionally inside *repo_dir*) and return the result.

    Raises ``AppError('git_command_failed', 502)`` on a non-zero exit unless
    *allow_fail* is set, in which case the ``CompletedProcess`` is returned so
    the caller can inspect ``returncode``.
    """
    cmd = ["git", *args]
    result = subprocess.run(
        cmd,
        cwd=str(repo_dir) if repo_dir is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and not allow_fail:
        # Scrub any token that may have leaked into stderr (defence in depth).
        stderr = _scrub(result.stderr)
        raise AppError(
            "git_command_failed",
            f"git {' '.join(_scrub(a) for a in args)} failed: {stderr[:400]}",
            502,
        )
    return result


def _scrub(text: str) -> str:
    """Redact ``user:token@`` credentials from a string before surfacing it."""
    return re.sub(r"(https://)[^/@\s]+@", r"\1***@", text or "")


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------


class RemoteProvider(ABC):
    """Abstract per-project remote provider (PAT / deploy-token auth)."""

    def __init__(self, repo_url: str, branch: str, token: str) -> None:
        self.repo_url = repo_url.strip()
        self.branch = (branch or "main").strip() or "main"
        self.token = token

    # -- credential URL ----------------------------------------------------

    @abstractmethod
    def authed_url(self) -> str:
        """Return ``self.repo_url`` with the PAT embedded for HTTPS auth."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """Provider id (``'github'`` | ``'gitlab'``)."""

    # -- working-tree operations ------------------------------------------

    def clone_or_pull(self, repo_dir: Path) -> None:
        """Clone the branch into *repo_dir*, or fetch + reset if it exists.

        After this returns, *repo_dir* is a checkout of ``self.branch`` at the
        remote tip (creating an empty branch locally when the remote branch
        does not exist yet).
        """
        repo_dir = Path(repo_dir)
        authed = self.authed_url()

        if (repo_dir / ".git").exists():
            # Existing clone: point origin at the (possibly refreshed) authed
            # URL, fetch, and hard-reset to the remote branch tip.
            _run_git(repo_dir, "remote", "set-url", "origin", authed, allow_fail=True)
            fetched = _run_git(repo_dir, "fetch", "origin", self.branch, allow_fail=True)
            if fetched.returncode == 0:
                _run_git(repo_dir, "checkout", "-B", self.branch, "FETCH_HEAD")
            else:
                # Remote branch does not exist yet — ensure we are on it locally.
                _run_git(repo_dir, "checkout", "-B", self.branch)
            self._ensure_identity(repo_dir)
            return

        repo_dir.mkdir(parents=True, exist_ok=True)
        # Try a branch-scoped clone first; fall back to init for empty remotes.
        cloned = _run_git(
            None,
            "clone",
            "--branch",
            self.branch,
            "--single-branch",
            authed,
            str(repo_dir),
            allow_fail=True,
        )
        if cloned.returncode != 0:
            # Empty remote or missing branch: init a fresh repo + add origin.
            _run_git(repo_dir, "init")
            _run_git(repo_dir, "remote", "add", "origin", authed, allow_fail=True)
            _run_git(repo_dir, "checkout", "-B", self.branch)
        self._ensure_identity(repo_dir)

    def push(
        self,
        repo_dir: Path,
        message: str,
        author_name: str = DEFAULT_AUTHOR_NAME,
        author_email: str = DEFAULT_AUTHOR_EMAIL,
    ) -> dict[str, Any]:
        """Stage all changes, commit (if any), and push the branch.

        Returns ``{committed: bool, sha: str, pushed: bool}``.  When the working
        tree is clean (nothing to commit) ``committed`` is ``False`` and no push
        is attempted.
        """
        repo_dir = Path(repo_dir)
        self._ensure_identity(repo_dir, author_name, author_email)

        _run_git(repo_dir, "add", "-A")

        # Anything staged?
        status = _run_git(repo_dir, "status", "--porcelain")
        if not status.stdout.strip():
            head = _run_git(repo_dir, "rev-parse", "HEAD", allow_fail=True)
            sha = head.stdout.strip() if head.returncode == 0 else ""
            return {"committed": False, "sha": sha, "pushed": False}

        _run_git(repo_dir, "commit", "-m", message)
        sha = _run_git(repo_dir, "rev-parse", "HEAD").stdout.strip()

        authed = self.authed_url()
        _run_git(repo_dir, "push", authed, f"HEAD:{self.branch}")
        return {"committed": True, "sha": sha, "pushed": True}

    def open_change_request(self, title: str, body: str = "") -> dict[str, Any] | None:
        """Optionally open a PR / MR for ``self.branch``.

        Default implementation is a no-op (returns ``None``).  Providers that
        support it override this.  Best-effort: failures raise ``AppError``.
        """
        return None

    # -- internals --------------------------------------------------------

    def _ensure_identity(
        self,
        repo_dir: Path,
        name: str = DEFAULT_AUTHOR_NAME,
        email: str = DEFAULT_AUTHOR_EMAIL,
    ) -> None:
        """Set a local commit identity so commits work without global config."""
        _run_git(repo_dir, "config", "user.name", name, allow_fail=True)
        _run_git(repo_dir, "config", "user.email", email, allow_fail=True)

    def _owner_repo(self) -> tuple[str, str]:
        """Parse ``(owner, repo)`` from ``self.repo_url`` (``.git`` stripped)."""
        path = urlparse(self.repo_url).path.strip("/")
        path = re.sub(r"\.git$", "", path)
        parts = path.split("/")
        if len(parts) < 2:
            raise AppError(
                "git_repo_url_invalid",
                f"Could not parse owner/repo from {_scrub(self.repo_url)!r}.",
                400,
            )
        owner = "/".join(parts[:-1])  # GitLab supports nested groups
        repo = parts[-1]
        return owner, repo


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


class GitHubProvider(RemoteProvider):
    """Push/pull a GitHub repo using a personal access / deploy token."""

    @property
    def provider(self) -> str:
        return "github"

    def authed_url(self) -> str:
        return _inject_token(self.repo_url, "x-access-token", self.token)

    def open_change_request(self, title: str, body: str = "") -> dict[str, Any] | None:
        """Open a pull request from ``self.branch`` into the repo default branch.

        Best-effort: returns ``None`` when the branch is the default (no PR
        needed) and raises ``AppError`` on an API failure.
        """
        host = urlparse(self.repo_url).netloc or "github.com"
        api_base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        owner, repo = self._owner_repo()

        default_branch = self._default_branch(api_base, owner, repo)
        if default_branch is None or default_branch == self.branch:
            return None

        data = _http_json(
            "POST",
            f"{api_base}/repos/{owner}/{repo}/pulls",
            token=self.token,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json_body={
                "title": title,
                "body": body,
                "head": self.branch,
                "base": default_branch,
            },
            ok=(200, 201),
            allow=(422,),  # PR already exists
        )
        if data is None:
            return None
        return {"url": data.get("html_url"), "number": data.get("number")}

    def _default_branch(self, api_base: str, owner: str, repo: str) -> str | None:
        data = _http_json(
            "GET",
            f"{api_base}/repos/{owner}/{repo}",
            token=self.token,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            },
            ok=(200,),
            allow=(404,),
        )
        if data is None:
            return None
        return data.get("default_branch")


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


class GitLabProvider(RemoteProvider):
    """Push/pull a GitLab repo using a personal/project/deploy token."""

    @property
    def provider(self) -> str:
        return "gitlab"

    def authed_url(self) -> str:
        return _inject_token(self.repo_url, "oauth2", self.token)

    def open_change_request(self, title: str, body: str = "") -> dict[str, Any] | None:
        """Open a merge request from ``self.branch`` into the project default."""
        host = urlparse(self.repo_url).netloc or "gitlab.com"
        api_base = f"https://{host}/api/v4"
        owner, repo = self._owner_repo()
        project_path = f"{owner}/{repo}"
        # GitLab wants the project path URL-encoded.
        from urllib.parse import quote

        pid = quote(project_path, safe="")

        default_branch = self._default_branch(api_base, pid)
        if default_branch is None or default_branch == self.branch:
            return None

        data = _http_json(
            "POST",
            f"{api_base}/projects/{pid}/merge_requests",
            token=self.token,
            headers={"PRIVATE-TOKEN": self.token},
            json_body={
                "source_branch": self.branch,
                "target_branch": default_branch,
                "title": title,
                "description": body,
            },
            ok=(200, 201),
            allow=(409,),  # MR already exists
        )
        if data is None:
            return None
        return {"url": data.get("web_url"), "number": data.get("iid")}

    def _default_branch(self, api_base: str, pid: str) -> str | None:
        data = _http_json(
            "GET",
            f"{api_base}/projects/{pid}",
            token=self.token,
            headers={"PRIVATE-TOKEN": self.token},
            ok=(200,),
            allow=(404,),
        )
        if data is None:
            return None
        return data.get("default_branch")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_provider(provider: str, repo_url: str, branch: str, token: str) -> RemoteProvider:
    """Return the provider implementation for *provider* (``github``|``gitlab``).

    Raises ``AppError('git_provider_unknown', 400)`` for any other value.
    """
    key = (provider or "").strip().lower()
    if key == "github":
        return GitHubProvider(repo_url=repo_url, branch=branch, token=token)
    if key == "gitlab":
        return GitLabProvider(repo_url=repo_url, branch=branch, token=token)
    raise AppError(
        "git_provider_unknown",
        f"Unknown git provider: {provider!r}. Supported: 'github', 'gitlab'.",
        400,
    )


# ---------------------------------------------------------------------------
# URL + HTTP helpers
# ---------------------------------------------------------------------------


def _inject_token(repo_url: str, user: str, token: str) -> str:
    """Return *repo_url* with ``<user>:<token>@`` injected after ``https://``.

    Only HTTPS URLs are supported (SSH URLs are returned unchanged so the caller
    surfaces the underlying git error).
    """
    url = repo_url.strip()
    if not url.startswith("https://"):
        return url
    rest = url[len("https://"):]
    # Drop any pre-existing credentials in the URL.
    if "@" in rest.split("/", 1)[0]:
        rest = rest.split("@", 1)[1]
    return f"https://{user}:{token}@{rest}"


def _http_json(
    method: str,
    url: str,
    *,
    token: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    ok: tuple[int, ...] = (200,),
    allow: tuple[int, ...] = (),
) -> dict[str, Any] | None:
    """Make a JSON HTTP request via httpx (lazy import).

    Returns the parsed JSON on an *ok* status, ``None`` on an *allow*-listed
    status (treated as a soft no-op), and raises ``AppError`` otherwise.
    """
    try:
        import httpx  # lazy import
    except ImportError as exc:  # pragma: no cover - depends on env
        raise AppError(
            "httpx_missing",
            "httpx is required to open pull/merge requests. Install: pip install httpx",
            500,
        ) from exc

    try:
        resp = httpx.request(method, url, headers=headers, json=json_body, timeout=20.0)
    except Exception as exc:  # pragma: no cover - network
        raise AppError("git_api_error", f"HTTP request failed: {exc}", 502) from exc

    if resp.status_code in ok:
        try:
            return resp.json()
        except Exception:
            return {}
    if resp.status_code in allow:
        return None
    raise AppError(
        "git_api_error",
        f"Remote API returned {resp.status_code}: {resp.text[:200]}",
        502,
    )
