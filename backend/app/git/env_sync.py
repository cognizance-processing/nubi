"""Environment ⇄ git-branch sync service — the GIT-ENV layer (DECISION 5).

Every project owns ONE local workspace repo (the same working clone used by
``app/routes/git.py``: ``<workspace>/<org_id>/projects/<project_id>``) and
every environment is bound to a branch in it (``environments.git_branch``).
The database stays the runtime source of truth; git is a best-effort mirror:

- **checkpoint**   → commit the new version's files to the env's branch and
  stamp ``resource_versions.git_commit_sha``.
- **promote**      → merge the from-env branch into the to-env branch
  (fast-forward preferred); a conflict NEVER rolls back the pointer copy.
- **push**         → serialize every resource pinned in the env to its branch
  in one commit, update ``last_synced_sha``, push to the project's remote
  when one is bound (``projects.git``).
- **pull**         → fast-forward imports branch changes into new pinned
  versions; divergence surfaces as data (the route turns it into a 409)
  unless a ``take_branch`` / ``take_env`` strategy is given.
- **from_branch**  → seed a brand-new environment from an existing branch.
- **graph**        → commit log per env-bound branch for the UI.

Failure model
-------------
ALL public coroutines are non-fatal: any git problem (no ``git`` binary, no
workspace repo, merge/push errors, …) degrades to a ``warning`` /
``git_warning`` field in the returned dict.  A failed or absent git layer
never blocks a data operation and never raises out of this module's async
API.  The workspace repo is created lazily on the first git-env operation
that needs to write.

On-disk layout (per branch) — CANONICAL CHOICE
-----------------------------------------------
- ``queries/<id>.sql`` + ``queries/<id>.meta.json``  (meta: name + non-SQL config)
- ``queries/<id>.json``  (OPTIONAL output-shape sidecar: the query's declared
  ``output_schema`` as ``[{name, type}]`` — emitted only when the config carries
  one; loaded back into ``config['output_schema']`` on pull/import).
- ``dashboards/<id>.json``                            (name + board config)
- ``flows/<slug>__<id8>/`` — the **flows-as-files** tree (``flow.toml`` +
  per-cell ``cells/NN_<key>.{sql,py,md}`` sidecars, see ``app/git/flow_files.py``).

Canonical flow layout decision
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The per-cell directory tree (``flows/<slug>__<id8>/flow.toml`` + ``cells/…``) is
the ONE canonical on-disk form for flows. It REPLACES the legacy single-blob
``flows/<id>.json`` envelope: a flow is the only resource whose source spans
multiple editable files (sql/python/markdown cells), so per-cell projection is
what makes git diffs reviewable. The flow's REAL uuid lives in ``flow.toml``'s
``[flow].id`` (the directory ``id8`` is only an 8-char disambiguator), so a
pull resolves the resource id from the manifest, not from the path. Legacy
``flows/<id>.json`` blobs are still *readable* on pull for back-compat but are
never written by serialization.

File stems for queries/boards are the resource uuids so a branch round-trips
losslessly through ``resource_versions``.  Like ``app/git/remotes.py`` we drive
the ``git`` CLI directly (branch checkout/merge are awkward through GitPython);
the repo itself is initialised with the same identity as :mod:`app.git.sync`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.git.flow_files import flow_dir, load_flow_files, serialize_flow_files

# ---------------------------------------------------------------------------
# Constants / kind mapping
# ---------------------------------------------------------------------------

#: version kind → folder inside the workspace repo.
KIND_FOLDER: dict[str, str] = {"query": "queries", "board": "dashboards", "flow": "flows"}
#: folder → version kind (inverse of KIND_FOLDER).
FOLDER_KIND: dict[str, str] = {v: k for k, v in KIND_FOLDER.items()}

DEFAULT_AUTHOR = "Nubi Git Sync <nubi-git-sync@nubi.local>"

#: Pull strategies accepted by :func:`pull_env`.
PULL_STRATEGIES: frozenset[str] = frozenset({"take_branch", "take_env"})


class GitEnvError(RuntimeError):
    """A git plumbing operation failed (callers convert this to a warning)."""


# ---------------------------------------------------------------------------
# Workspace resolution (mirrors app/routes/git.py so envs + the project
# remote binding share ONE working clone per project)
# ---------------------------------------------------------------------------


def workspace_root() -> Path:
    """Return the workspace root (``NUBI_GIT_WORKSPACE`` or tempdir default)."""
    env_val = os.environ.get("NUBI_GIT_WORKSPACE", "")
    if env_val:
        return Path(env_val)
    return Path(tempfile.gettempdir()) / "nubi_git_workspace"


def project_repo_dir(org_id: str, project_id: str) -> Path:
    """Return the per-project workspace repo dir (same as routes/git.py)."""
    return workspace_root() / str(org_id) / "projects" / str(project_id)


def get_project_git(org_id: str, project_id: str) -> "ProjectGit":
    """Return a :class:`ProjectGit` for the project's workspace repo."""
    return ProjectGit(project_repo_dir(org_id, project_id))


# ---------------------------------------------------------------------------
# Serialization (env-pinned versions ⇄ files)
# ---------------------------------------------------------------------------


def _output_schema_sidecar(config: dict[str, Any]) -> list[dict[str, str]] | None:
    """Return the ``queries/<id>.json`` output-shape sidecar payload, or None.

    The sidecar mirrors the query's declared ``output_schema`` as a list of
    ``{name, type}`` objects (the exact persisted shape ``registry`` consumes).
    Returns ``None`` when the config carries NO ``output_schema`` (no sidecar is
    emitted — the file is absent on disk); an explicit empty list is a declared
    (empty) contract and round-trips as ``[]``.
    """
    raw = config.get("output_schema")
    if raw is None or not isinstance(raw, list):
        return None
    schema: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name") is not None:
            schema.append({"name": str(item["name"]), "type": str(item.get("type") or "text")})
    return schema


def serialize_version_files(
    kind: str, resource_id: str, name: str, config: dict[str, Any]
) -> list[dict[str, str]]:
    """Serialize one pinned version to ``{path, content}`` items.

    - ``query`` → ``queries/<id>.sql`` (raw SQL) + ``queries/<id>.meta.json``
      (``{id, name, config}`` with the SQL key stripped — full fidelity), PLUS
      an optional ``queries/<id>.json`` output-shape sidecar (only when the
      config declares an ``output_schema``).
    - ``board`` → ``dashboards/<id>.json`` (``{id, name, config}``).
    - ``flow``  → the canonical flows-as-files tree under
      ``flows/<slug>__<id8>/`` (``flow.toml`` + per-cell ``cells/NN_<key>.*``).
      The flow's spec is ``config`` (env-pinned flow versions store the spec in
      the version ``config`` column).
    """
    rid = str(resource_id)
    config = config or {}
    if kind == "query":
        # The output_schema lives ONLY in the sidecar (queries/<id>.json), so it
        # is stripped from meta.json to avoid duplication / drift.
        meta = {
            "id": rid,
            "name": name or "",
            "config": {
                k: v for k, v in config.items() if k not in ("sql", "output_schema")
            },
        }
        items = [
            {"path": f"queries/{rid}.sql", "content": str(config.get("sql", ""))},
            {
                "path": f"queries/{rid}.meta.json",
                "content": json.dumps(meta, indent=2, sort_keys=True),
            },
        ]
        schema = _output_schema_sidecar(config)
        if schema is not None:
            items.append(
                {
                    "path": f"queries/{rid}.json",
                    "content": json.dumps(schema, indent=2, sort_keys=True),
                }
            )
        return items
    if kind == "board":
        doc = {"id": rid, "name": name or "", "config": config}
        return [
            {
                "path": f"dashboards/{rid}.json",
                "content": json.dumps(doc, indent=2, sort_keys=True),
            }
        ]
    if kind == "flow":
        # Canonical flow layout: the per-cell file tree (see module docstring).
        # serialize_flow_files emits flows/<slug>__<id8>/flow.toml + cells/*.
        return serialize_flow_files(rid, name or str(config.get("name") or ""), config)
    raise ValueError(f"Unknown version kind: {kind!r}.")


def refs_from_paths(paths: list[str]) -> set[tuple[str, str]]:
    """Map repo file paths to ``(kind, ref_key)`` pairs (known folders only).

    For queries/boards the ``ref_key`` IS the resource uuid (the file stem).
    For the nested flows-as-files layout (``flows/<slug>__<id8>/…``) the
    ``ref_key`` is the flow DIRECTORY name (``<slug>__<id8>``) — the path only
    carries an 8-char id, so the real uuid is recovered from ``flow.toml`` at
    load time (see :func:`load_resource_at`). A legacy ``flows/<id>.json`` blob
    still maps to ``("flow", <id>)`` for back-compat reads.
    """
    refs: set[tuple[str, str]] = set()
    for raw in paths:
        parts = str(raw).strip().split("/")
        if len(parts) < 2:
            continue
        folder = parts[0]
        kind = FOLDER_KIND.get(folder)
        if kind is None:
            continue
        if kind == "flow":
            # Nested per-cell layout: flows/<dir>/(flow.toml|cells/…) → the dir.
            if len(parts) >= 3:
                refs.add((kind, parts[1]))
            elif len(parts) == 2 and parts[1].endswith(".json"):
                # Legacy single-blob flow: flows/<id>.json.
                refs.add((kind, parts[1][: -len(".json")]))
            continue
        if len(parts) != 2:
            continue
        fname = parts[1]
        if kind == "query":
            # queries/<id>.json is the output-shape SIDECAR, not a standalone
            # resource — it is loaded alongside the .sql/.meta.json, never on
            # its own, so it is NOT treated as a ref here.
            if fname.endswith(".meta.json"):
                refs.add((kind, fname[: -len(".meta.json")]))
            elif fname.endswith(".sql"):
                refs.add((kind, fname[: -len(".sql")]))
        elif fname.endswith(".json"):
            refs.add((kind, fname[: -len(".json")]))
    return refs


def _load_flow_at(
    git: "ProjectGit", ref: str, ref_key: str
) -> tuple[dict[str, Any], str, str] | None:
    """Reconstruct ``(spec, name, real_id)`` for a flow at *ref*.

    *ref_key* is either the nested flow DIRECTORY name (``<slug>__<id8>``) or a
    legacy ``<id>`` stem. The real resource uuid is read from ``flow.toml``'s
    ``[flow].id`` (falling back to the path stem for legacy blobs).
    """
    # Legacy single-blob flow: flows/<id>.json.
    legacy = git.read_file(ref, f"flows/{ref_key}.json")
    if legacy is not None:
        doc = json.loads(legacy)
        spec = doc.get("spec") or {}
        if not isinstance(spec, dict):
            return None
        return spec, str(doc.get("name") or ""), str(doc.get("id") or ref_key)

    # Nested per-cell layout: read every file under flows/<ref_key>/ relative
    # to the flow dir so load_flow_files can re-merge sidecars.
    base = f"flows/{ref_key}"
    prefix = base + "/"
    rel_files: dict[str, str] = {}
    for path in git.list_known_files(ref):
        if path.startswith(prefix):
            content = git.read_file(ref, path)
            if content is not None:
                rel_files[path[len(prefix):]] = content
    if "flow.toml" not in rel_files:
        return None
    spec = load_flow_files(rel_files)
    import toml  # noqa: PLC0415 — only needed to recover [flow].id

    meta = (toml.loads(rel_files["flow.toml"]).get("flow") or {})
    real_id = str(meta.get("id") or "").strip()
    return spec, str(spec.get("name") or ""), real_id or ref_key


def load_resource_at(
    git: "ProjectGit", ref: str, kind: str, resource_id: str
) -> tuple[dict[str, Any], str, str] | None:
    """Deserialize ``(config, name, resource_id)`` for a resource at *ref*.

    Returns None when the resource is absent. The third element is the CANONICAL
    resource uuid: identical to the passed *resource_id* for queries/boards, but
    recovered from ``flow.toml``'s ``[flow].id`` for flows (the ref key for a
    flow is its directory name, which only carries an 8-char id).
    """
    rid = str(resource_id)
    try:
        if kind == "flow":
            return _load_flow_at(git, ref, rid)
        if kind == "query":
            sql = git.read_file(ref, f"queries/{rid}.sql")
            meta_raw = git.read_file(ref, f"queries/{rid}.meta.json")
            if sql is None and meta_raw is None:
                return None
            meta = json.loads(meta_raw) if meta_raw else {}
            config = dict(meta.get("config") or {})
            config["sql"] = sql if sql is not None else config.get("sql", "")
            # Output-shape sidecar (queries/<id>.json): load it back into the
            # config when present; absent → leave output_schema untouched.
            schema_raw = git.read_file(ref, f"queries/{rid}.json")
            if schema_raw is not None:
                try:
                    schema = json.loads(schema_raw)
                    if isinstance(schema, list):
                        config["output_schema"] = schema
                except json.JSONDecodeError:
                    pass
            return config, str(meta.get("name") or ""), rid
        folder = KIND_FOLDER[kind]
        raw = git.read_file(ref, f"{folder}/{rid}.json")
        if raw is None:
            return None
        doc = json.loads(raw)
        config = doc.get("config") or {}
        if not isinstance(config, dict):
            return None
        return config, str(doc.get("name") or ""), rid
    except (json.JSONDecodeError, GitEnvError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# ProjectGit — branch-aware CLI plumbing over the workspace repo
# ---------------------------------------------------------------------------


def _parse_author(author: str) -> tuple[str, str]:
    """Parse ``"Name <email>"`` into ``(name, email)``."""
    if "<" in author and author.endswith(">"):
        name, _, rest = author.rpartition("<")
        return name.strip(), rest.rstrip(">").strip()
    return author.strip(), "nubi-git-sync@nubi.local"


def _scrub(text: str) -> str:
    """Redact ``user:token@`` credentials before surfacing an error string."""
    import re

    return re.sub(r"(https://)[^/@\s]+@", r"\1***@", text or "")


class ProjectGit:
    """Branch-aware helper over one project workspace repo (git CLI).

    All methods raise :class:`GitEnvError` on plumbing failures; the async
    service layer below converts those into response warnings.  The repo is
    initialised lazily via :meth:`ensure` (same identity as ``GitSync``).
    """

    def __init__(self, repo_dir: Path | str) -> None:
        self.repo_dir = Path(repo_dir)

    # -- lifecycle ----------------------------------------------------------

    def exists(self) -> bool:
        """True when the workspace repo has been initialised on disk."""
        return (self.repo_dir / ".git").exists()

    def ensure(self) -> None:
        """Initialise the repo (with the Nubi sync identity) if missing."""
        if self.exists():
            return
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self._git("init")
        self._git("config", "user.email", "nubi-git-sync@nubi.local")
        self._git("config", "user.name", "Nubi Git Sync")

    # -- plumbing -----------------------------------------------------------

    def _git(
        self,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        except (OSError, FileNotFoundError) as exc:  # git binary / cwd missing
            raise GitEnvError(f"git unavailable: {exc}") from exc
        if check and result.returncode != 0:
            raise GitEnvError(
                f"git {' '.join(_scrub(a) for a in args)} failed: "
                f"{_scrub(result.stderr).strip()[:300]}"
            )
        return result

    def _author_env(self, author: str) -> dict[str, str]:
        name, email = _parse_author(author)
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }

    # -- refs / branches ----------------------------------------------------

    def branch_head(self, branch: str) -> str | None:
        """Return the branch tip sha, or None when the branch has no commits."""
        if not self.exists():
            return None
        result = self._git(
            "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        )
        sha = result.stdout.strip()
        return sha if result.returncode == 0 and sha else None

    def _checkout(self, branch: str) -> None:
        """Check out *branch*, creating it when missing.

        New branches start at the current HEAD (shared history keeps later
        merges fast-forwardable); in a commit-less repo the unborn HEAD is
        simply re-pointed.
        """
        if self.branch_head(branch) is not None:
            self._git("checkout", "--quiet", branch)
            return
        has_head = self._git("rev-parse", "--verify", "--quiet", "HEAD", check=False)
        if has_head.returncode == 0:
            self._git("checkout", "--quiet", "-b", branch)
        else:
            self._git("symbolic-ref", "HEAD", f"refs/heads/{branch}")

    def create_branch_at(self, branch: str, sha: str) -> None:
        """Create *branch* pointing at *sha* (no-op when it already exists)."""
        if self.branch_head(branch) is None:
            self._git("branch", branch, sha)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """True when *ancestor* is reachable from *descendant*."""
        result = self._git(
            "merge-base", "--is-ancestor", ancestor, descendant, check=False
        )
        return result.returncode == 0

    # -- content ------------------------------------------------------------

    def list_known_files(self, ref: str) -> list[str]:
        """Return all tracked files at *ref* under the known resource folders."""
        out = self._git("ls-tree", "-r", "--name-only", ref).stdout
        return [
            line
            for line in out.splitlines()
            if line.strip() and line.split("/", 1)[0] in FOLDER_KIND
        ]

    def read_file(self, ref: str, path: str) -> str | None:
        """Return the file content at ``ref:path``, or None when absent."""
        result = self._git("show", f"{ref}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None

    def changed_known_files(self, base: str, head: str) -> list[str]:
        """Files under known folders that differ between *base* and *head*."""
        out = self._git("diff", "--name-only", base, head).stdout
        return [
            line
            for line in out.splitlines()
            if line.strip() and line.split("/", 1)[0] in FOLDER_KIND
        ]

    # -- write operations ----------------------------------------------------

    def commit_files(
        self,
        branch: str,
        items: list[dict[str, str]],
        message: str,
        author: str = DEFAULT_AUTHOR,
        replace_known: bool = False,
    ) -> str | None:
        """Write *items* onto *branch* and commit; return the branch tip sha.

        When the working tree ends up clean (content already committed) the
        current tip is returned without creating an empty commit.  With
        ``replace_known`` the known resource folders are cleared first so the
        branch mirrors exactly the given items (push / take_env semantics).
        """
        self.ensure()
        self._checkout(branch)
        if replace_known:
            for folder in FOLDER_KIND:
                self._git("rm", "-r", "-q", "--ignore-unmatch", "--", folder, check=False)
                shutil.rmtree(self.repo_dir / folder, ignore_errors=True)
        for item in items:
            fp = self.repo_dir / item["path"]
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(item["content"], encoding="utf-8")
        self._git("add", "-A")
        if not self._git("status", "--porcelain").stdout.strip():
            return self.branch_head(branch)
        self._git("commit", "-m", message, env=self._author_env(author))
        return self.branch_head(branch)

    def merge_branches(
        self, from_branch: str, to_branch: str, message: str
    ) -> dict[str, Any]:
        """Merge *from_branch* into *to_branch* (fast-forward preferred).

        Returns ``{merged: True, sha, ff}`` on success or
        ``{conflict: {files, from_sha, to_sha}}`` after aborting a conflicted
        merge.  A missing *to_branch* is created at the from tip (pure ff).
        """
        from_sha = self.branch_head(from_branch)
        if from_sha is None:
            raise GitEnvError(f"branch {from_branch!r} has no commits to merge")
        to_sha = self.branch_head(to_branch)
        if to_sha is None:
            self.create_branch_at(to_branch, from_sha)
            return {"merged": True, "sha": from_sha, "ff": True}
        if to_sha == from_sha or self.is_ancestor(from_sha, to_sha):
            return {"merged": True, "sha": to_sha, "ff": True}

        self._checkout(to_branch)
        result = self._git(
            "merge", "--ff", "--no-edit", "--allow-unrelated-histories",
            "-m", message, from_branch,
            check=False, env=self._author_env(DEFAULT_AUTHOR),
        )
        if result.returncode == 0:
            sha = self.branch_head(to_branch)
            return {"merged": True, "sha": sha, "ff": sha == from_sha}

        files = [
            line.strip()
            for line in self._git(
                "diff", "--name-only", "--diff-filter=U", check=False
            ).stdout.splitlines()
            if line.strip()
        ]
        self._git("merge", "--abort", check=False)
        return {"conflict": {"files": sorted(files), "from_sha": from_sha, "to_sha": to_sha}}

    def log_branch(self, branch: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return up to *limit* commits on *branch*, newest first."""
        if self.branch_head(branch) is None:
            return []
        fmt = "%H%x1f%P%x1f%s%x1f%an <%ae>%x1f%aI"
        out = self._git("log", branch, f"-n{int(limit)}", f"--format={fmt}").stdout
        commits: list[dict[str, Any]] = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) < 5:
                continue
            commits.append(
                {
                    "sha": parts[0].strip(),
                    "parents": parts[1].split(),
                    "message": parts[2].strip(),
                    "author": parts[3].strip(),
                    "date": parts[4].strip(),
                }
            )
        return commits

    # -- remote --------------------------------------------------------------

    def fetch_branch(self, remote_url: str, branch: str) -> None:
        """Fast-forward the local branch ref from the remote (best-effort ff)."""
        self._git("fetch", remote_url, f"{branch}:{branch}")

    def push_branch(
        self, remote_url: str, branch: str, force_with_lease: bool = False
    ) -> None:
        """Push *branch* to *remote_url* (``--force-with-lease`` optional)."""
        args = ["push"]
        if force_with_lease:
            args.append("--force-with-lease")
        args += [remote_url, f"refs/heads/{branch}:refs/heads/{branch}"]
        self._git(*args)


# ---------------------------------------------------------------------------
# Internal async helpers
# ---------------------------------------------------------------------------


async def _remote_authed_url(org_id: str, project_id: str) -> str | None:
    """Return the project's token-authed remote URL, or None when unbound.

    Reads the ``projects.git`` binding + the secret-store token exactly like
    ``app/routes/git.py``.  Best-effort: any failure returns None.
    """
    try:
        from app.connectors.secret_store import get_secret_store  # noqa: PLC0415
        from app.git.remotes import make_provider  # noqa: PLC0415
        from app.repos import projects as projects_repo  # noqa: PLC0415

        project = await projects_repo.get_project(org_id, project_id)
        binding = (project or {}).get("git")
        if not binding or not isinstance(binding, dict) or not binding.get("provider"):
            return None
        secret = await get_secret_store().get(project_id, org_id)
        token = (secret or {}).get("token", "")
        if not token:
            return None
        provider = make_provider(
            binding["provider"], binding["repo_url"], binding.get("branch", "main"), token
        )
        return provider.authed_url()
    except Exception:  # noqa: BLE001 — remote binding is optional
        return None


async def _resource_name(kind: str, resource_id: str, org_id: str, repo: Any) -> str:
    """Best-effort lookup of a resource's display name ('' on any failure)."""
    try:
        if kind == "flow":
            from app.flows.store import get_flow_store  # noqa: PLC0415

            flow = await get_flow_store().get_flow(str(resource_id))
            return str((flow or {}).get("name") or "")
        resource = "boards" if kind == "board" else "queries"
        row = await repo.get(resource, org_id, str(resource_id))
        return str((row or {}).get("name") or "")
    except Exception:  # noqa: BLE001
        return ""


async def _serialize_env_pins(
    env: dict[str, Any], org_id: str, repo: Any
) -> tuple[list[dict[str, str]], int]:
    """Serialize every resource pinned in *env*; return ``(items, count)``."""
    from app.environments.store import get_env_store  # noqa: PLC0415

    store = get_env_store()
    items: list[dict[str, str]] = []
    count = 0
    for ptr in await store.list_env_pointers(env["id"]):
        version = await store.get_version_by_id(ptr["version_id"])
        if version is None:
            continue
        name = await _resource_name(ptr["kind"], ptr["resource_id"], org_id, repo)
        items.extend(
            serialize_version_files(
                ptr["kind"], ptr["resource_id"], name, version.get("config") or {}
            )
        )
        count += 1
    return items, count


async def _import_refs(
    git: ProjectGit,
    head: str,
    refs: set[tuple[str, str]],
    *,
    org_id: str,
    project_id: str,
    env: dict[str, Any],
    user_id: str | None,
    message: str,
) -> dict[str, int]:
    """Create + pin a version for each ``(kind, resource_id)`` at *head*.

    The new version's parent is the env's currently pinned version (when one
    exists) and its ``git_commit_sha`` is *head*.  Per-ref failures (bad file,
    non-uuid stem on Pg, …) are skipped; returns ``{kind: imported_count}``.
    """
    from app.environments.store import get_env_store  # noqa: PLC0415

    store = get_env_store()
    counts: dict[str, int] = {}
    for kind, ref_key in sorted(refs):
        loaded = load_resource_at(git, head, kind, ref_key)
        if loaded is None:
            continue
        config, _name, rid = loaded  # rid is the CANONICAL uuid (flow.toml id)
        try:
            pointer = await store.get_pointer(kind, rid, env["id"])
            version = await store.create_version(
                org_id=org_id,
                project_id=project_id,
                kind=kind,
                resource_id=rid,
                config=config,
                created_by=user_id,
                message=message,
                parent_version_id=(pointer or {}).get("version_id"),
                git_commit_sha=head,
            )
            await store.set_pointer(
                kind, rid, env["id"], version["id"], promoted_by=user_id
            )
        except Exception:  # noqa: BLE001 — skip unimportable files, keep going
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Public async API (everything below is non-fatal)
# ---------------------------------------------------------------------------


async def import_branch_into_env(
    *,
    org_id: str,
    project_id: str,
    env: dict[str, Any],
    branch: str,
    user_id: str | None,
) -> dict[str, Any]:
    """Seed a freshly created environment from an existing branch.

    Deserializes every known file at the branch tip into resource versions
    (``git_commit_sha`` = branch head, pinned to *env*) and points the env's
    own branch at the same tip when it does not exist yet.  Missing repo or
    branch → ``{imported: {}, warning}`` (the env stays empty).
    """
    try:
        git = get_project_git(org_id, project_id)
        if not git.exists():
            return {
                "imported": {},
                "warning": "No git workspace repo for this project; "
                "environment created empty.",
            }
        head = git.branch_head(branch)
        if head is None:
            return {
                "imported": {},
                "warning": f"Branch {branch!r} not found in the project workspace "
                "repo; environment created empty.",
            }
        refs = refs_from_paths(git.list_known_files(head))
        counts = await _import_refs(
            git, head, refs,
            org_id=org_id, project_id=project_id, env=env, user_id=user_id,
            message=f"Imported from branch '{branch}'",
        )
        # Start the env's own branch at the imported tip (shared history).
        env_branch = str(env.get("git_branch") or "")
        if env_branch and env_branch != branch:
            try:
                git.create_branch_at(env_branch, head)
            except GitEnvError:
                pass
        return {"imported": counts, "git_commit_sha": head}
    except Exception as exc:  # noqa: BLE001 — git layer is optional
        return {"imported": {}, "warning": f"git import skipped: {exc}"}


async def commit_checkpoint(
    *,
    org_id: str,
    project_id: str,
    env: dict[str, Any],
    kind: str,
    resource_id: str,
    name: str,
    version: dict[str, Any],
    user_message: str | None = None,
) -> dict[str, Any]:
    """Commit a freshly checkpointed version to the env's branch.

    Lazily creates the workspace repo, commits the serialized resource to
    ``env.git_branch``, and stamps ``git_commit_sha`` on the version row.
    Returns ``{git_commit_sha}`` or ``{git_warning}`` — never raises.
    """
    if version.get("deduped") and version.get("git_commit_sha"):
        return {"git_commit_sha": version["git_commit_sha"]}
    try:
        from app.environments.store import get_env_store  # noqa: PLC0415

        git = get_project_git(org_id, project_id)
        message = f"checkpoint {kind} '{name or resource_id}' v{version.get('version')}"
        if user_message:
            message = f"{message} — {user_message}"
        sha = git.commit_files(
            str(env.get("git_branch") or "dev"),
            serialize_version_files(kind, resource_id, name, version.get("config") or {}),
            message,
        )
        if not sha:
            return {"git_warning": "git commit produced no sha"}
        await get_env_store().set_version_git_commit(version["id"], sha)
        return {"git_commit_sha": sha}
    except Exception as exc:  # noqa: BLE001 — never fail a checkpoint on git
        return {"git_warning": f"git commit skipped: {exc}"}


async def merge_env_branches(
    *,
    org_id: str,
    project_id: str,
    from_env: dict[str, Any],
    to_env: dict[str, Any],
) -> dict[str, Any]:
    """Best-effort merge of the from-env branch into the to-env branch.

    Returns ``{git_merge: {merged, sha, ff, from_branch, to_branch}}`` on
    success, ``{git_conflict: {files, from_sha, to_sha}}`` on a conflict
    (pointers are NOT rolled back by the caller), or ``{git_warning}`` when
    git is unavailable.  Same-branch envs are a no-op.
    """
    from_branch = str(from_env.get("git_branch") or "")
    to_branch = str(to_env.get("git_branch") or "")
    if not from_branch or not to_branch or from_branch == to_branch:
        return {}
    try:
        git = get_project_git(org_id, project_id)
        if not git.exists():
            return {"git_warning": "No git workspace repo for this project; merge skipped."}
        result = git.merge_branches(
            from_branch,
            to_branch,
            f"promote {from_env.get('key')} -> {to_env.get('key')}",
        )
        if "conflict" in result:
            return {"git_conflict": result["conflict"]}
        return {
            "git_merge": {
                "merged": True,
                "sha": result.get("sha"),
                "ff": bool(result.get("ff")),
                "from_branch": from_branch,
                "to_branch": to_branch,
            }
        }
    except Exception as exc:  # noqa: BLE001 — never fail a promote on git
        return {"git_warning": f"git merge skipped: {exc}"}


async def push_env(
    *,
    org_id: str,
    project_id: str,
    env: dict[str, Any],
    repo: Any,
    message: str | None = None,
) -> dict[str, Any]:
    """Serialize ALL resources pinned in *env* to its branch in one commit.

    Updates ``environments.last_synced_sha`` to the resulting tip and pushes
    the branch to the project's remote when one is bound.  Returns
    ``{branch, sha, committed, files, pushed, last_synced_sha, warnings}``.
    """
    from app.environments.store import get_env_store  # noqa: PLC0415

    branch = str(env.get("git_branch") or "")
    warnings: list[str] = []
    items, count = await _serialize_env_pins(env, org_id, repo)
    if not items:
        return {
            "branch": branch, "sha": None, "committed": False, "files": 0,
            "pushed": False, "last_synced_sha": env.get("last_synced_sha"),
            "warnings": ["Nothing pinned in this environment; nothing to push."],
        }

    try:
        git = get_project_git(org_id, project_id)
        before = git.branch_head(branch)
        sha = git.commit_files(
            branch, items, message or f"push environment '{env.get('key')}'",
            replace_known=True,
        )
    except Exception as exc:  # noqa: BLE001 — git layer is optional
        return {
            "branch": branch, "sha": None, "committed": False, "files": count,
            "pushed": False, "last_synced_sha": env.get("last_synced_sha"),
            "warnings": [f"git commit failed: {exc}"],
        }

    if sha:
        try:
            await get_env_store().update_environment(env["id"], {"last_synced_sha": sha})
        except Exception:  # noqa: BLE001
            warnings.append("could not record last_synced_sha")

    pushed = False
    remote_url = await _remote_authed_url(org_id, project_id)
    if remote_url and sha:
        try:
            git.push_branch(remote_url, branch)
            pushed = True
        except Exception as exc:  # noqa: BLE001 — remote push is best-effort
            warnings.append(f"remote push failed: {_scrub(str(exc))}")

    return {
        "branch": branch,
        "sha": sha,
        "committed": bool(sha and sha != before),
        "files": count,
        "pushed": pushed,
        "last_synced_sha": sha or env.get("last_synced_sha"),
        "warnings": warnings,
    }


async def pull_env(
    *,
    org_id: str,
    project_id: str,
    env: dict[str, Any],
    repo: Any,
    user_id: str | None,
    strategy: str | None = None,
) -> dict[str, Any]:
    """Sync *env* from its branch (fetching the remote first when bound).

    - branch tip == ``last_synced_sha``      → ``{pulled: False, up_to_date: True}``
    - fast-forwardable (or first sync)       → changed files become new pinned
      versions (parent = current pin, ``git_commit_sha`` = tip) and
      ``last_synced_sha`` advances.
    - DIVERGED                                → ``{diverged: True, files,
      env_sha, branch_sha}`` (the route answers 409) unless *strategy* is
      ``'take_branch'`` (import the branch wholesale) or ``'take_env'``
      (overwrite the branch from env state, force-with-lease semantics).
    - no repo / missing branch / git failure  → ``{pulled: False, warning}``.
    """
    from app.environments.store import get_env_store  # noqa: PLC0415

    store = get_env_store()
    branch = str(env.get("git_branch") or "")
    warnings: list[str] = []
    try:
        git = get_project_git(org_id, project_id)
        if not git.exists():
            return {
                "pulled": False,
                "warning": "No git workspace repo for this project; nothing to pull.",
            }

        remote_url = await _remote_authed_url(org_id, project_id)
        if remote_url:
            try:
                git.fetch_branch(remote_url, branch)
            except Exception as exc:  # noqa: BLE001 — fetch is best-effort
                warnings.append(f"remote fetch failed: {_scrub(str(exc))}")

        head = git.branch_head(branch)
        if head is None:
            return {
                "pulled": False,
                "warning": f"Branch {branch!r} has no commits; nothing to pull.",
                "warnings": warnings,
            }

        last = env.get("last_synced_sha")
        if last == head:
            return {"pulled": False, "up_to_date": True, "sha": head, "warnings": warnings}

        diverged = bool(last) and not git.is_ancestor(str(last), head)
        if diverged and strategy is None:
            try:
                files = git.changed_known_files(str(last), head)
            except GitEnvError:
                files = git.list_known_files(head)
            return {
                "diverged": True,
                "files": files,
                "env_sha": last,
                "branch_sha": head,
            }

        if diverged and strategy == "take_env":
            # Overwrite the branch from the env's pinned state (force-with-lease).
            items, count = await _serialize_env_pins(env, org_id, repo)
            sha = git.commit_files(
                branch, items,
                f"pull --take_env: overwrite '{branch}' from environment "
                f"'{env.get('key')}'",
                replace_known=True,
            )
            if sha:
                await store.update_environment(env["id"], {"last_synced_sha": sha})
            if remote_url and sha:
                try:
                    git.push_branch(remote_url, branch, force_with_lease=True)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"remote push failed: {_scrub(str(exc))}")
            return {
                "pulled": True, "strategy": "take_env", "sha": sha,
                "updated": {}, "files": count, "warnings": warnings,
            }

        # Fast-forward (or first sync, or take_branch): import branch content.
        if last and not diverged:
            changed = git.changed_known_files(str(last), head)
        else:
            changed = git.list_known_files(head)
        refs = refs_from_paths(changed)
        counts = await _import_refs(
            git, head, refs,
            org_id=org_id, project_id=project_id, env=env, user_id=user_id,
            message=f"git pull '{branch}' @ {head[:8]}",
        )
        await store.update_environment(env["id"], {"last_synced_sha": head})
        out: dict[str, Any] = {
            "pulled": True, "sha": head, "updated": counts, "warnings": warnings,
        }
        if strategy == "take_branch":
            out["strategy"] = "take_branch"
        return out
    except Exception as exc:  # noqa: BLE001 — never 5xx a pull on git problems
        return {"pulled": False, "warning": f"git pull failed: {exc}", "warnings": warnings}


async def project_git_graph(*, org_id: str, project_id: str) -> dict[str, Any]:
    """Return the commit graph for every env-bound branch in the project.

    ``{branches: [{branch, env_key, head_sha, commits: [{sha, parents,
    message, author, date}]}]}`` capped at ~100 commits per branch; an empty
    structure (``{branches: []}``) when no workspace repo exists.
    """
    from app.environments.store import get_env_store  # noqa: PLC0415

    try:
        envs = await get_env_store().list_environments(project_id)
        git = get_project_git(org_id, project_id)
        if not git.exists():
            return {"branches": []}
        branches: list[dict[str, Any]] = []
        for env in envs:
            branch = str(env.get("git_branch") or "")
            if not branch:
                continue
            head = git.branch_head(branch)
            branches.append(
                {
                    "branch": branch,
                    "env_key": env.get("key"),
                    "head_sha": head or "",
                    "commits": git.log_branch(branch, limit=100) if head else [],
                }
            )
        return {"branches": branches}
    except Exception:  # noqa: BLE001 — graph is read-only sugar
        return {"branches": []}
