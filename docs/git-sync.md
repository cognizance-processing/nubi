# Git Sync

Nubi's git sync feature serializes queries, dashboards (boards), and flows to a local git repository and pushes them to GitHub or GitLab. This enables dashboards-as-code workflows, audit trails, and rollback via `POST /api/v1/git/restore`.

---

## How It Works

Git sync operates at two scopes:

**Org-level sync** (`POST /api/v1/git/sync`):

1. All registered queries from the `QueryRegistry` are serialized to `.sql` + `.meta.json` file pairs.
2. All boards for the caller's org are serialized to JSON files.
3. Changes are committed to a local git repo in the workspace directory (org-scoped subdirectory).
4. Returns the commit SHA, number of files committed, and the commit message.

**Project-level sync** (`POST /api/v1/git/push`):

1. The project's local working clone is synced to the remote tip (`clone_or_pull`).
2. Dashboards, queries, and flows are serialized as portability-envelope YAML files, plus a `nubi.yaml` manifest.
3. All changes are staged, committed, and pushed to the connected remote branch.
4. Optionally opens a pull/merge request when `open_pr` is set.

Connectors are never serialized (by product design).

---

## Workspace Directory

The workspace root is set by the `NUBI_GIT_WORKSPACE` environment variable. Default: `<system-temp>/nubi_git_workspace`.

- Org-level snapshots: `<workspace>/<org_id>/`
- Project working clones: `<workspace>/<org_id>/projects/<project_id>/`

---

## Endpoints

### `POST /api/v1/git/sync`

Serialize and commit all registered queries + boards for the caller's org.

**Request body** (optional):

```json
{
  "message": "chore: sync resources",
  "author":  "Nubi Git Sync <nubi-git-sync@nubi.local>"
}
```

**Response:**

```json
{
  "sha":             "a1b2c3d4...",
  "files_committed": 7,
  "message":         "chore: sync resources"
}
```

Returns `sha: ""` and `files_committed: 0` when there is nothing to commit.

---

### `GET /api/v1/git/history`

Return commit history for the org's workspace.

**Query params:**

| Param | Description |
|-------|-------------|
| `path` | Optional relative file path (e.g. `queries/demo_all.sql`). Only commits touching that path are returned. |

**Response:**

```json
[
  {
    "sha":     "a1b2c3d4...",
    "message": "chore: sync resources",
    "author":  "Nubi Git Sync <nubi-git-sync@nubi.local>",
    "ts":      "2024-01-15T07:00:01+00:00"
  }
]
```

Commits are ordered most recent first.

---

### `POST /api/v1/git/restore`

Return the content of a file at a historical commit SHA.

**Request body:**

```json
{
  "path": "dashboards/abc123.json",
  "sha":  "a1b2c3d4..."
}
```

**Response:**

```json
{
  "path":    "dashboards/abc123.json",
  "sha":     "a1b2c3d4...",
  "content": "{ \"id\": \"abc123\", ... }"
}
```

Returns 404 if the path or SHA does not exist.

---

## Project-Scoped Remote Sync

Projects can be connected to a GitHub or GitLab repository via a personal access token (PAT) or deploy token stored securely in the secret store. Configure the connection in the app at **Settings → Project → Git**, or via the API.

### `POST /api/v1/git/connect`

Bind a project to a remote repository. The token is stored in the secret store; only a reference is recorded on the project.

**Request body:**

```json
{
  "project_id": "<uuid>",
  "provider":   "github",
  "repo_url":   "https://github.com/org/repo.git",
  "branch":     "main",
  "base_path":  "",
  "token":      "<PAT or deploy token>"
}
```

`provider` must be `"github"` or `"gitlab"`.

---

### `GET /api/v1/git/status?project_id=<uuid>`

Return the current git binding and last-sync info for a project.

---

### `POST /api/v1/git/push`

Serialize the project's resources, commit, and push to the connected branch.

**Request body:**

```json
{
  "project_id": "<uuid>",
  "message":    "chore: sync nubi resources",
  "open_pr":    false
}
```

**Response:**

```json
{
  "sha":            "a1b2c3d4...",
  "committed":      true,
  "pushed":         true,
  "files":          12,
  "change_request": null
}
```

When `open_pr` is `true` and `branch` differs from the repository default, a pull request (GitHub) or merge request (GitLab) is opened automatically. `change_request` contains `{url, number}` on success.

---

### `POST /api/v1/git/pull`

Fetch the project's remote branch and import/upsert all resource envelopes into Nubi. The database stays canonical; this hydrates it from the git mirror.

**Request body:**

```json
{ "project_id": "<uuid>" }
```

**Response:**

```json
{
  "imported": 8,
  "kinds":    { "dashboard": 5, "query": 3, "flow": 0 }
}
```

---

## Auth Model

### GitHub (PAT or fine-grained token)

The token is embedded as `https://x-access-token:<token>@github.com/<owner>/<repo>.git` for all fetch/push operations. The token requires `Contents: Read and write` permission on the target repository.

### GitLab (personal, project, or deploy token)

Uses HTTPS basic auth: `https://oauth2:<token>@<host>/<path>.git`. The token requires `write_repository` permission.

The token is **never** written into the working tree or committed to history.

---

## CLI — `nubi diff` and `nubi deploy`

The CLI provides a local diff and deploy workflow that complements the git sync API:

```bash
# Compare local board JSON files against the server
nubi diff ./dashboards

# Deploy (creates or updates based on presence of "id" key)
nubi deploy ./dashboards
nubi deploy ./dashboards --dry-run   # preview only

# Pull current server state to local files
nubi pull boards ./downloaded/boards
```

See [SDK & CLI](/docs/sdk-and-cli) for the full CLI reference.

---

## Serialized File Structure

### Org-level snapshot (POST /git/sync)

Each query produces two files under `queries/`:

```
<workspace>/<org_id>/queries/<id>.sql
<workspace>/<org_id>/queries/<id>.meta.json
```

The `.meta.json` file stores `{ name, params, required_scope }` for the query.

Each board produces one file under `dashboards/`:

```
<workspace>/<org_id>/dashboards/<id>.json
```

The JSON file stores `{ id, name, config }` with sorted keys for byte-stable round-trips.

### Project-level sync (POST /git/push)

Resources are written as portability-envelope YAML files:

```
<base_path>/dashboards/<slug>.yaml
<base_path>/queries/<slug>.yaml
<base_path>/flows/<slug>.yaml
<base_path>/nubi.yaml          # manifest with resource counts
```

`base_path` defaults to the repo root when not set in the project's git binding.
