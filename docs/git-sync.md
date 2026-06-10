# Git Sync

Git sync version-controls your Nubi resources — queries, dashboards, flows, and automations — as committed files in a GitHub or GitLab repository. The Nubi database stays canonical; git is the mirror.

Connectors and their secrets are never serialized, by product design.

---

## Two sync scopes

| Scope | What it does | Remote push? |
|---|---|---|
| **Org-level snapshot** (`POST /git/sync`) | Commits all registered queries and boards for the caller's org to a local workspace repo | No — local only |
| **Project-level sync** (`POST /git/push`) | Serializes all project resources as portability-envelope YAML, commits, and pushes to a connected GitHub or GitLab remote | Yes |

The org-level snapshot is useful for audit trails and rollback without a remote. The project-level sync is the dashboards-as-code workflow.

---

## Setting up project sync

Open **Settings → Project** (in-app route `/settings/project`). The Git panel is embedded in the project settings page and is visible to users with write access.

Fields:

| Field | Notes |
|---|---|
| **Provider** | `github` or `gitlab` |
| **Repository URL** | Full HTTPS clone URL, e.g. `https://github.com/owner/repo.git` |
| **Branch** | Target branch; defaults to `main` |
| **Base path** | Optional subdirectory inside the repo (e.g. `nubi/`). Leave blank for the repo root. |
| **Access token** | A personal access token (PAT) or deploy token. Required on first connect; leave blank on subsequent edits to keep the current token. |

The token is encrypted and stored server-side. It is never returned by the API and never written into the repository.

### Token permissions required

**GitHub** — the token needs `Contents: Read and write` on the target repository. A fine-grained PAT scoped to the repo works. Classic `repo` scope also works.

**GitLab** — personal access token, project access token, or CI_JOB_TOKEN with `write_repository` permission. Uses HTTPS basic auth with the `oauth2` username convention: `https://oauth2:<token>@gitlab.com/<group>/<repo>.git`.

---

## Push and pull

Once connected, the Git panel shows two actions:

**Push** — serializes the project's resources, commits any changes, and pushes the branch to the remote. If the working tree is already up to date, nothing is committed and nothing is pushed.

**Pull** — clones or fetches the remote branch and imports/upserts all YAML envelopes found under `base_path` into Nubi. Dashboards and queries are upserted by their `metadata.id`. Flows are not imported via pull (only push is supported for flows).

### Pull/merge request creation

When `open_pr: true` is set in the API request and the configured branch differs from the repository's default branch, Nubi automatically opens a pull request (GitHub) or merge request (GitLab) after a successful push.

---

## Serialized file layout

### Project-level sync (portability envelopes)

Resources are written as YAML portability envelopes:

```
<base_path>/dashboards/<slug>.yaml
<base_path>/queries/<slug>.yaml
<base_path>/flows/<slug>.yaml
<base_path>/automations/<slug>.yaml
<base_path>/nubi.yaml          # manifest with project identity and resource counts
```

`base_path` is stripped of leading/trailing slashes and defaults to the repo root when not configured.

The `nubi.yaml` manifest records the project name, id, slug, and per-kind resource counts:

```yaml
apiVersion: nubi/v1
kind: project
metadata:
  name: my-project
  id: <uuid>
  slug: my-project
resources:
  dashboards: 3
  queries: 5
  flows: 1
  automations: 0
```

### Org-level snapshot (local only)

The org-level `POST /git/sync` writes to a local workspace directory and makes no network calls. Each query produces two files:

```
<workspace>/<org_id>/queries/<id>.sql
<workspace>/<org_id>/queries/<id>.meta.json
```

The `.meta.json` stores `{ name, params, required_scope }`. Each dashboard produces:

```
<workspace>/<org_id>/dashboards/<id>.json
```

Dashboard JSON is pretty-printed with sorted keys for byte-stable diffs.

The workspace root is set by `NUBI_GIT_WORKSPACE` (default: `<system-temp>/nubi_git_workspace`). Project working clones live under `<workspace>/<org_id>/projects/<project_id>/`.

---

## API reference

All endpoints require a valid Bearer token. Operations are org-scoped.

### `POST /api/v1/git/sync`

Serialize and commit all registered queries and boards for the caller's org to the local workspace. No remote push.

Request body (optional):

```json
{
  "message": "chore: sync resources",
  "author":  "Nubi Git Sync <nubi-git-sync@nubi.local>"
}
```

Response:

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

Return commit history for the org's local workspace.

Query params:

| Param | Description |
|---|---|
| `path` | Optional relative file path (e.g. `queries/demo_all.sql`). Only commits touching that path are returned. |

Response — ordered most recent first:

```json
[
  {
    "sha":     "a1b2c3d4...",
    "message": "chore: sync resources",
    "author":  "Nubi Git Sync <nubi-git-sync@nubi.local>",
    "ts":      "2025-01-15T07:00:01+00:00"
  }
]
```

---

### `POST /api/v1/git/restore`

Return the content of a file at a historical commit SHA.

Request body:

```json
{
  "path": "dashboards/abc123.json",
  "sha":  "a1b2c3d4..."
}
```

Response:

```json
{
  "path":    "dashboards/abc123.json",
  "sha":     "a1b2c3d4...",
  "content": "{ \"id\": \"abc123\", ... }"
}
```

Returns 404 if the path or SHA does not exist.

---

### `POST /api/v1/git/connect`

Bind a project to a remote repository. The token is stored encrypted in the secret store; only a reference is recorded on the project.

Request body:

```json
{
  "project_id": "<uuid>",
  "provider":   "github",
  "repo_url":   "https://github.com/owner/repo.git",
  "branch":     "main",
  "base_path":  "",
  "token":      "<PAT or deploy token>"
}
```

`provider` must be `"github"` or `"gitlab"`. Call this endpoint again to update the connection; the token field is optional on updates (omit to keep the stored token).

---

### `GET /api/v1/git/status?project_id=<uuid>`

Return the current git binding and last-sync info for a project. The token is never returned.

---

### `POST /api/v1/git/push`

Clone or fetch the remote branch tip, serialize the project's resources, commit any changes, and push to the connected remote. Optionally opens a pull/merge request.

Request body:

```json
{
  "project_id": "<uuid>",
  "message":    "chore: sync nubi resources",
  "open_pr":    false
}
```

Response:

```json
{
  "sha":            "a1b2c3d4...",
  "committed":      true,
  "pushed":         true,
  "files":          12,
  "change_request": null
}
```

When `open_pr` is `true` and `branch` differs from the repository default, a pull request (GitHub) or merge request (GitLab) is opened. `change_request` is `{ "url": "...", "number": 42 }` on success, `null` otherwise.

---

### `POST /api/v1/git/pull`

Fetch the project's remote branch and import/upsert all resource envelopes (dashboards and queries) into Nubi. The database stays canonical; git hydrates it.

Request body:

```json
{ "project_id": "<uuid>" }
```

Response:

```json
{
  "imported": 8,
  "kinds":    { "dashboard": 5, "query": 3, "flow": 0 }
}
```

---

## Auth model

### GitHub (PAT or fine-grained token)

The token is embedded as `https://x-access-token:<token>@github.com/<owner>/<repo>.git` for all clone, fetch, and push operations. The token is never written to the working tree or committed to history.

### GitLab (personal, project, or deploy token)

Uses HTTPS basic auth: `https://oauth2:<token>@<host>/<path>.git`. Override the host for self-hosted GitLab instances via the `repo_url` field (the host is inferred from the URL). The token requires `write_repository` permission.

> Credentials are scrubbed from error messages before they are surfaced to the caller.

---

## Server-side remote push (org-level, advanced)

For deployments that want an org-level automated push (not project-scoped), configure `GIT_REMOTE_PROVIDER` and the matching credentials as environment variables. This uses a GitHub App JWT flow or a static GitLab token, independent of the per-project PAT model above.

| Variable | Description |
|---|---|
| `GIT_REMOTE_PROVIDER` | `github_app`, `gitlab`, or unset/`none` |
| `GITHUB_APP_ID` | Numeric GitHub App ID |
| `GITHUB_APP_PRIVATE_KEY` | PEM-encoded RSA private key for the App |
| `GITHUB_APP_INSTALLATION_ID` | Installation ID for the target org |
| `GITLAB_TOKEN` | GitLab personal or project access token |
| `GITLAB_HOST` | GitLab host (default `gitlab.com`) |
| `NUBI_GIT_WORKSPACE` | Local workspace root directory |

---

## CLI — `nubi diff` and `nubi deploy`

The CLI provides a local diff and deploy workflow that complements the git sync API. These operate on local JSON files, not git envelopes.

```bash
# Compare local board JSON files against the server
nubi diff ./dashboards

# Deploy (UPDATE if "id" is present, CREATE otherwise)
nubi deploy ./dashboards
nubi deploy ./dashboards --dry-run   # preview without making API calls

# Download server resources to local JSON files
nubi pull boards ./dashboards
nubi pull queries ./queries
```

Accepted resource types for `nubi pull`: `datastores`, `boards`, `widgets`, `queries`.

See [SDK & CLI](/docs/sdk-and-cli) for the full CLI reference.
