"""Git-backed version control for Nubi queries and dashboards — M20-A.

Public API
----------
serialize_resource(kind, resource) -> list[dict]
    Serialize a query or board resource to a list of ``{path, content}``
    dicts ready for writing into a git workspace.

GitSync(repo_dir)
    Wraps a local bare git repository (or initialises one on first use).
    Methods:
        commit_resources(items, message, author) -> sha
        history(path=None)  -> list[dict]
        restore(path, sha)  -> str
        read(path)          -> str

RemoteAuth
    Stub class.  Marks the seam where GitHub-App / deploy-key push will
    be wired in a future milestone.  No network calls are made today.

Environment / configuration
---------------------------
The default workspace directory is resolved as follows (first wins):

1. ``NUBI_GIT_WORKSPACE`` env var (explicit override).
2. ``<tempdir>/nubi_git_workspace``  (safe default for local dev / tests).

Callers (routes, tests) always pass ``repo_dir`` explicitly, so the env
var is only a convenience for production deployments.

Implementation notes
--------------------
We use GitPython when available (``import git``); this is the preferred
path because it gives us structured access to commit objects without
shelling out for every operation.  If GitPython is absent we fall back to
the ``git`` CLI via ``subprocess``.

All write operations are serialised through this module.  The repo is
initialised with ``git init`` (or GitPython's ``Repo.init``) if the
directory does not yet contain a valid git repository.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from app.errors import AppError

if TYPE_CHECKING:
    from app.git.remote import RemoteAuth as _RemoteAuthType


# ---------------------------------------------------------------------------
# Default workspace
# ---------------------------------------------------------------------------

def _default_workspace() -> Path:
    """Return the default git workspace directory.

    Checks ``NUBI_GIT_WORKSPACE`` first; falls back to a ``nubi_git_workspace``
    subdirectory inside the system temp dir.
    """
    env_val = os.environ.get("NUBI_GIT_WORKSPACE", "")
    if env_val:
        return Path(env_val)
    return Path(tempfile.gettempdir()) / "nubi_git_workspace"


# ---------------------------------------------------------------------------
# RemoteAuth (backward-compat shim — real implementations live in remote.py)
# ---------------------------------------------------------------------------

class RemoteAuth:
    """Backward-compatible shim kept for import compatibility with M20-A code.

    New code should import the concrete providers from ``app.git.remote``
    (``GitHubAppAuth``, ``GitLabTokenAuth``, ``NullRemote``) or use the
    ``make_remote_auth`` factory.

    Attributes
    ----------
    remote_url:
        Target remote URL.  Empty string disables pushing.
    """

    def __init__(self, remote_url: str = "") -> None:
        self.remote_url = remote_url

    def push(self, repo_dir: Path) -> None:  # noqa: ARG002
        """No-op shim — real push is implemented in ``NullRemote`` / concrete providers."""
        pass


# ---------------------------------------------------------------------------
# serialize_resource
# ---------------------------------------------------------------------------

def serialize_resource(kind: str, resource: dict[str, Any]) -> list[dict[str, str]]:
    """Serialize a query or board resource to a list of ``{path, content}`` dicts.

    Parameters
    ----------
    kind:
        ``'query'`` or ``'dashboard'``.
    resource:
        Resource dict as returned by the repo (``id``, ``name``, ``config``,
        etc.).

    Returns
    -------
    list[dict[str, str]]
        For a *query*:
            - ``queries/<id>.sql``       — raw SQL text.
            - ``queries/<id>.meta.json`` — ``{name, params, required_scope}``.
        For a *dashboard* / *board*:
            - ``dashboards/<id>.json``   — full board config (pretty-printed,
              stable key order for byte-stable round-trips).

    Raises
    ------
    ValueError
        If *kind* is not ``'query'`` or ``'dashboard'``.
    """
    resource_id: str = str(resource.get("id", ""))
    config: dict[str, Any] = resource.get("config") or {}

    if kind == "query":
        sql: str = config.get("sql", resource.get("sql", ""))
        params: list[Any] = config.get("params", resource.get("params", []))
        required_scope: str | None = config.get(
            "required_scope", resource.get("required_scope")
        )

        meta: dict[str, Any] = {
            "name": resource.get("name", ""),
            "params": params,
            "required_scope": required_scope,
        }

        return [
            {
                "path": f"queries/{resource_id}.sql",
                "content": sql,
            },
            {
                "path": f"queries/{resource_id}.meta.json",
                "content": json.dumps(meta, indent=2, sort_keys=True),
            },
        ]

    if kind == "dashboard":
        # Store the entire config dict (the board spec) as pretty-printed
        # JSON with sorted keys for byte-stable round-trips.
        board_doc: dict[str, Any] = {
            "id": resource_id,
            "name": resource.get("name", ""),
            "config": config,
        }
        return [
            {
                "path": f"dashboards/{resource_id}.json",
                "content": json.dumps(board_doc, indent=2, sort_keys=True),
            }
        ]

    raise ValueError(f"Unknown resource kind: {kind!r}. Must be 'query' or 'dashboard'.")


# ---------------------------------------------------------------------------
# Portability-envelope serialization (project layout) — M20-C
# ---------------------------------------------------------------------------

# Maps an envelope/resource kind → the folder it lives in inside base_path.
# Connectors are deliberately absent (never serialized — product decision).
KIND_FOLDER: dict[str, str] = {
    "dashboard": "dashboards",
    "query": "queries",
    "flow": "flows",
    "automation": "automations",
}


def serialize_envelope(kind: str, env: dict[str, Any], base_path: str = "") -> dict[str, str]:
    """Serialize a portability *envelope* to a single ``{path, content}`` item.

    Uses the portability YAML envelope (kind/apiVersion/metadata/spec) so the
    on-disk format round-trips through ``app.portability``.  The file lives at
    ``<base_path>/<folder>/<slug>.yaml`` where *folder* is derived from *kind*.

    Connectors are NOT supported here (no folder is registered) and any attempt
    to serialize one raises ``ValueError``.
    """
    from app.portability import dump_envelope, slug_for_envelope

    folder = KIND_FOLDER.get(kind)
    if folder is None:
        raise ValueError(
            f"Cannot serialize kind {kind!r} to git. "
            f"Supported: {sorted(KIND_FOLDER)!r} (connectors are excluded)."
        )

    slug = slug_for_envelope(env)
    rel = f"{folder}/{slug}.yaml"
    if base_path:
        rel = f"{base_path.strip('/')}/{rel}"

    return {"path": rel, "content": dump_envelope(env, format="yaml")}


def build_manifest(
    project: dict[str, Any],
    counts: dict[str, int],
    base_path: str = "",
) -> dict[str, str]:
    """Build the ``nubi.yaml`` manifest ``{path, content}`` for a project.

    The manifest records the project identity and a per-kind resource count so a
    pull can reconcile what should exist.  Connectors are intentionally omitted.
    """
    import yaml  # lazy

    doc = {
        "apiVersion": "nubi/v1",
        "kind": "project",
        "metadata": {
            "name": project.get("name", ""),
            "id": str(project.get("id", "")),
            "slug": project.get("slug", ""),
        },
        "resources": {k: counts.get(k, 0) for k in ("dashboards", "queries", "flows", "automations")},
    }
    rel = "nubi.yaml"
    if base_path:
        rel = f"{base_path.strip('/')}/{rel}"
    content = yaml.safe_dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return {"path": rel, "content": content}


# ---------------------------------------------------------------------------
# Internal git helpers (GitPython preferred, subprocess fallback)
# ---------------------------------------------------------------------------

def _try_gitpython(repo_dir: Path):  # type: ignore[return]
    """Return a GitPython Repo object, or None if GitPython is unavailable."""
    try:
        import git as gitpython  # type: ignore[import]
        try:
            return gitpython.Repo(str(repo_dir))
        except gitpython.InvalidGitRepositoryError:
            return gitpython.Repo.init(str(repo_dir))
    except ImportError:
        return None


def _run_git(repo_dir: Path, *args: str) -> str:
    """Run a git CLI command inside *repo_dir* and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed:\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return result.stdout


def _ensure_git_repo(repo_dir: Path) -> None:
    """Initialise a git repo at *repo_dir* if one does not exist."""
    if (repo_dir / ".git").exists():
        return
    repo_dir.mkdir(parents=True, exist_ok=True)
    _run_git(repo_dir, "init")
    # Configure minimal identity so commits work without a global git config.
    _run_git(repo_dir, "config", "user.email", "nubi-git-sync@nubi.local")
    _run_git(repo_dir, "config", "user.name", "Nubi Git Sync")


# ---------------------------------------------------------------------------
# GitSync
# ---------------------------------------------------------------------------

class GitSync:
    """Version-control helper that wraps a local git repository.

    Parameters
    ----------
    repo_dir:
        Path to the local repository directory.  The directory (and its
        ```.git`` sub-directory) are created and initialised on first use.
        Defaults to :func:`_default_workspace` when not supplied.

    Usage
    -----
    ::

        sync = GitSync(tmp_path / "workspace")
        sha = sync.commit_resources(
            items=[{"path": "queries/q1.sql", "content": "SELECT 1"}],
            message="add q1",
            author="Alice <alice@example.com>",
        )
        entries = sync.history()
        old_content = sync.restore("queries/q1.sql", sha)
    """

    def __init__(
        self,
        repo_dir: Path | str | None = None,
        remote: "_RemoteAuthType | None" = None,
    ) -> None:
        if repo_dir is None:
            repo_dir = _default_workspace()
        self.repo_dir = Path(repo_dir)
        self.remote = remote  # optional RemoteAuth provider for push

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def commit_resources(
        self,
        items: list[dict[str, str]],
        message: str,
        author: str = "Nubi Git Sync <nubi-git-sync@nubi.local>",
    ) -> str:
        """Write *items* to disk, stage, and commit them.

        Parameters
        ----------
        items:
            List of ``{path, content}`` dicts.  *path* is relative to the
            repo root.  *content* is plain UTF-8 text.
        message:
            Commit message.
        author:
            Author string in ``Name <email>`` format.

        Returns
        -------
        str
            The SHA-1 hex digest of the new commit.

        Raises
        ------
        ValueError
            If *items* is empty.
        RuntimeError
            If the underlying git operation fails.
        """
        if not items:
            raise ValueError("items must not be empty")

        self._ensure_init()

        # Write files
        for item in items:
            file_path = self.repo_dir / item["path"]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(item["content"], encoding="utf-8")

        # Try GitPython first
        try:
            import git as gitpython  # type: ignore[import]

            repo = gitpython.Repo(str(self.repo_dir))
            repo.index.add([item["path"] for item in items])

            # Parse author name/email
            author_obj = gitpython.Actor(*self._parse_author(author))
            commit = repo.index.commit(message, author=author_obj, committer=author_obj)
            return str(commit.hexsha)
        except ImportError:
            pass

        # Fallback: subprocess
        for item in items:
            _run_git(self.repo_dir, "add", item["path"])

        # Build author env for the commit
        author_name, author_email = self._parse_author(author)
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git commit failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )

        sha = _run_git(self.repo_dir, "rev-parse", "HEAD").strip()
        return sha

    def history(self, path: str | None = None) -> list[dict[str, str]]:
        """Return the commit history for the repo (or for a specific file).

        Parameters
        ----------
        path:
            Optional relative file path.  When supplied, only commits that
            touched that file are returned.

        Returns
        -------
        list[dict]
            Ordered list of commit dicts (most recent first), each with keys:
            ``sha``, ``message``, ``author``, ``ts`` (ISO-8601 UTC timestamp).
        """
        self._ensure_init()

        # Try GitPython
        try:
            import git as gitpython  # type: ignore[import]

            repo = gitpython.Repo(str(self.repo_dir))
            if not repo.head.is_valid():
                return []

            kwargs: dict[str, Any] = {}
            if path:
                kwargs["paths"] = path

            entries: list[dict[str, str]] = []
            for commit in repo.iter_commits(**kwargs):
                entries.append(
                    {
                        "sha": str(commit.hexsha),
                        "message": str(commit.message).strip(),
                        "author": f"{commit.author.name} <{commit.author.email}>",
                        "ts": commit.authored_datetime.isoformat(),
                    }
                )
            return entries
        except ImportError:
            pass

        # Fallback: subprocess
        # Check if any commits exist
        check = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if check.returncode != 0:
            return []

        fmt = "%H%x1f%s%x1f%an <%ae>%x1f%aI"
        cmd = ["git", "log", f"--format={fmt}"]
        if path:
            cmd.extend(["--", path])

        raw = _run_git(self.repo_dir, *cmd[1:])
        entries = []
        for line in raw.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\x1f")
            if len(parts) < 4:
                continue
            entries.append(
                {
                    "sha": parts[0].strip(),
                    "message": parts[1].strip(),
                    "author": parts[2].strip(),
                    "ts": parts[3].strip(),
                }
            )
        return entries

    def restore(self, path: str, sha: str) -> str:
        """Return the contents of *path* at commit *sha*.

        Parameters
        ----------
        path:
            Relative file path inside the repo.
        sha:
            Commit SHA to read from.

        Returns
        -------
        str
            UTF-8 file contents at the given commit.

        Raises
        ------
        RuntimeError
            If the path or sha does not exist.
        """
        self._ensure_init()

        # Try GitPython
        try:
            import git as gitpython  # type: ignore[import]

            try:
                repo = gitpython.Repo(str(self.repo_dir))
                commit = repo.commit(sha)
                blob = commit.tree / path
                return blob.data_stream.read().decode("utf-8")
            except Exception as exc:
                raise RuntimeError(
                    f"Could not restore {path!r} at {sha!r}: {exc}"
                ) from exc
        except ImportError:
            pass

        # Fallback: subprocess
        return _run_git(self.repo_dir, "show", f"{sha}:{path}")

    def read(self, path: str) -> str:
        """Return the current working-tree contents of *path*.

        Parameters
        ----------
        path:
            Relative file path inside the repo.

        Returns
        -------
        str
            UTF-8 file contents from the working tree.

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        AppError
            (``invalid_path``, 400) if *path* escapes ``self.repo_dir``
            (absolute path, contains a ``..`` segment, or otherwise
            resolves outside the repo directory).
        """
        # Reject obviously unsafe inputs up front: absolute paths and any
        # path that contains a parent-directory ('..') segment.
        candidate_raw = PurePosixPath(path)
        if candidate_raw.is_absolute() or Path(path).is_absolute() or ".." in candidate_raw.parts:
            raise AppError(
                "invalid_path",
                "Path must be a relative path inside the repository.",
                400,
            )

        base = self.repo_dir.resolve()
        candidate = (self.repo_dir / path).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise AppError(
                "invalid_path",
                "Path resolves outside the repository.",
                400,
            ) from exc

        return candidate.read_text(encoding="utf-8")

    def push(
        self,
        branch: str = "main",
        remote_url: str | None = None,
    ) -> None:
        """Push *branch* to the configured remote.

        Delegates to ``self.remote.push()``.  If no remote is configured
        (``self.remote`` is ``None``) the call is a no-op (matching the
        ``NullRemote`` behaviour).

        Parameters
        ----------
        branch:
            Branch to push (default ``'main'``).
        remote_url:
            Override URL passed through to the provider.  Most providers
            require this when they do not store a URL internally.
        """
        if self.remote is None:
            return  # no-op: equivalent to NullRemote
        self.remote.push(repo_dir=self.repo_dir, branch=branch, remote_url=remote_url)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_init(self) -> None:
        """Initialise the repo on disk if it does not exist yet."""
        if not (self.repo_dir / ".git").exists():
            self.repo_dir.mkdir(parents=True, exist_ok=True)

            # GitPython init if available
            try:
                import git as gitpython  # type: ignore[import]

                repo = gitpython.Repo.init(str(self.repo_dir))
                with repo.config_writer() as cw:
                    cw.set_value("user", "email", "nubi-git-sync@nubi.local")
                    cw.set_value("user", "name", "Nubi Git Sync")
                return
            except ImportError:
                pass

            # Subprocess fallback
            _ensure_git_repo(self.repo_dir)

    @staticmethod
    def _parse_author(author: str) -> tuple[str, str]:
        """Parse ``"Name <email>"`` into ``(name, email)``."""
        if "<" in author and author.endswith(">"):
            name, _, rest = author.rpartition("<")
            email = rest.rstrip(">").strip()
            return name.strip(), email.strip()
        return author.strip(), "nubi-git-sync@nubi.local"
