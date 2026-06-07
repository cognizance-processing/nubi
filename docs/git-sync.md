# Git Sync

Nubi's git sync feature lets you commit all registered queries and dashboards (boards) to a local git repository, then push to GitHub or GitLab. This enables dashboards-as-code workflows, audit trails, and rollback via `POST /api/v1/git/restore`.

---

## How It Works

On `POST /api/v1/git/sync`:

1. All registered queries from the `QueryRegistry` are serialized to SQL files.
2. All boards for the caller's org are serialized to JSON files.
3. Changes are committed to a local bare git repo in the workspace directory (org-scoped subdirectory).
4. Returns the commit SHA, number of files committed, and the commit message.

On `POST /api/v1/git/push`:

1. Same serialization + commit as `/git/sync`.
2. Then pushes to the configured remote (GitHub App or GitLab token).
3. `pushed: false` when `GIT_REMOTE_PROVIDER` is `none` (the default).

---

## Workspace Directory

The workspace root is set by the `NUBI_GIT_WORKSPACE` environment variable. Default: `<system-temp>/nubi_git_workspace`.

Each org gets its own subdirectory: `<workspace>/<org_id>/`.

---

## Endpoints

### `POST /api/v1/git/sync`

Serialize and commit all registered queries + boards.

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

### `POST /api/v1/git/push`

Commit all resources and push to the configured remote.

**Request body** (optional):

```json
{
  "message":    "chore: sync resources",
  "author":     "Nubi Git Sync <nubi-git-sync@nubi.local>",
  "branch":     "main",
  "remote_url": "https://github.com/org/repo.git"
}
```

**Response:**

```json
{
  "sha":             "a1b2c3d4...",
  "files_committed": 7,
  "message":         "chore: sync resources",
  "branch":          "main",
  "pushed":          true
}
```

`pushed: false` when `NullRemote` is active (no remote configured).

---

## Remote Providers

Configure via environment variables:

### GitHub App

```
GIT_REMOTE_PROVIDER=github_app
GITHUB_APP_ID=<numeric app id>
GITHUB_APP_PRIVATE_KEY=<PEM-encoded RSA private key>
GITHUB_APP_INSTALLATION_ID=<installation id>
```

Flow: Nubi mints a short-lived RS256 JWT (10 min), exchanges it for a GitHub App installation access token (valid ~1 hour, cached in-process), and pushes via `https://x-access-token:<token>@github.com/<org>/<repo>.git`.

### GitLab Token

```
GIT_REMOTE_PROVIDER=gitlab
GITLAB_TOKEN=<personal or project access token>
GITLAB_HOST=gitlab.com        # override for self-hosted
```

Uses HTTPS basic authentication: `https://oauth2:<token>@<host>/<path>.git`.

The token needs `write_repository` permission.

### No Remote (default)

```
GIT_REMOTE_PROVIDER=none   # or unset
```

`POST /api/v1/git/push` commits locally but does not push. `pushed` is `false` in the response.

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

Queries are serialized as `.sql` files under `queries/`:

```
<workspace>/<org_id>/queries/demo_all.sql
<workspace>/<org_id>/queries/revenue_by_month.sql
```

Boards are serialized as `.json` files under `dashboards/`:

```
<workspace>/<org_id>/dashboards/board-abc123.json
```

Each JSON file matches the `boards` resource shape: `{ id, org_id, name, config, ... }`.
