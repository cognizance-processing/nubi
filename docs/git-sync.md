# Git Sync

Git sync version-controls your Nubi resources — queries, dashboards, and flows (scheduled automations live inside flows) — as committed files in a GitHub or GitLab repository. The Nubi database stays canonical; git is the mirror.

Connectors and their secrets are never serialized on push, by product design.

---

## Three sync scopes

| Scope | What it does | Remote push? |
|---|---|---|
| **Org-level snapshot** (`POST /git/sync`) | Commits all registered queries and boards for the caller's org to a local workspace repo | No — local only |
| **Project-level sync** (`POST /git/push`) | Serializes all project resources as portability-envelope YAML, commits, and pushes to a connected GitHub or GitLab remote | Yes |
| **Environment ⇄ branch sync** (`POST /environments/{env_id}/git/push` / `pull`) | Pins one resource *version* per environment branch — checkpoints, promotes, and explicit env push/pull | Yes, when the project has a remote connected |

The org-level snapshot is useful for audit trails and rollback without a remote. The project-level sync is the dashboards-as-code workflow. The environment sync is what the in-app **Git / Versions** panel drives — see [Environment ⇄ branch sync](#environment-branch-sync).

---

## Setting up project sync

Open **Settings → Project › General** (in-app route `/settings/project`). The Git panel is embedded in the project settings page and is visible to users with write access. It hosts the connect form below plus the project-level **Push** / **Pull** actions; day-to-day environment push/pull lives in the [Git / Versions panel](#environment-branch-sync) instead.

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

## Push and pull (project-level)

Once connected, the Git panel in project settings shows two actions:

**Push** — serializes the project's resources, commits any changes, and pushes the branch to the remote. If the working tree is already up to date, nothing is committed and nothing is pushed.

**Pull** — clones or fetches the remote branch and imports/upserts all YAML envelopes found under `base_path` into Nubi. Resources are upserted by their `metadata.id`. Flows are imported too, gated by hard validation: an envelope that fails flow-spec validation is skipped, so a hand-edited or corrupt file can never register a broken flow (soft `[warn]` issues are allowed through). A `connectors/` folder of envelopes — written by the CLI files-as-code path, never by the in-app push — is also imported; connector envelopes are spec-only by design, secrets are never serialized into envelopes.

### Pull/merge request creation

When `open_pr: true` is set in the API request and the configured branch differs from the repository's default branch, Nubi automatically opens a pull request (GitHub) or merge request (GitLab) after a successful push.

---

## Environment ⇄ branch sync

Every environment in a project is bound to a branch (`git_branch`) in the project's workspace repo. The creation default maps `prod` → `main` and every other environment to its own key (`dev` → `dev`, `staging` → `staging`, …). The whole layer is best-effort: a missing `git` binary, an absent workspace repo, or a merge/push failure degrades to a `warning` / `git_warning` field in the response — it never blocks the data operation and never returns a 5xx.

- **Checkpoint** — saving a new resource version commits that version's files to the active environment's branch and stamps `resource_versions.git_commit_sha`.
- **Promote** — promoting env A to env B also merges branch A into branch B (fast-forward preferred). A merge conflict is reported as `git_conflict: {files, from_sha, to_sha}` but the version-pointer copies are **not** rolled back.
- **Push** (`POST /environments/{env_id}/git/push`) — serializes every resource pinned in the environment to its branch as one commit, updates `last_synced_sha`, and pushes to the project's remote when one is connected.
- **Pull** (`POST /environments/{env_id}/git/pull`) — syncs the environment from its branch. If the branch head equals `last_synced_sha`, the result is `{pulled: false, up_to_date: true}`. If the branch fast-forwards from the last sync, changed files become new pinned versions (parent = current pin) and `last_synced_sha` advances. If the two sides diverged, the call returns **409** `{diverged: true, files, env_sha, branch_sha}` until you retry with a `strategy`:

| Strategy | Effect |
|---|---|
| `take_branch` | Import the branch state into the environment |
| `take_env` | Overwrite the branch from the environment's pinned state (force-with-lease) |

- **Branch graph** (`GET /projects/{project_id}/git/graph`) — `{branches: [...]}`, one commit log per env-bound branch.

### The Git / Versions panel

The right-edge rail on every authenticated page has a **Git / Versions** button that opens a slide-in panel — the app-wide git surface:

- **Sync tab** — the connected repo / branch / last-sync status, plus **Push** and **Pull** for the *active environment's* branch. A diverged pull surfaces an inline resolver with **Use branch** / **Use environment** buttons (the two strategies above).
- **Branch graph** — opens the per-environment commit graph.
- **Files tab** — a read-only browser of the synced files at a ref (backed by the `GET /projects/{project_id}/git/files` endpoints below).

---

## Serialized file layout

### Project-level sync (portability envelopes)

Resources are written as YAML portability envelopes:

```
<base_path>/dashboards/<slug>.yaml
<base_path>/queries/<slug>.yaml
<base_path>/flows/<slug>.yaml
<base_path>/nubi.yaml          # manifest with project identity and resource counts
```

`base_path` is stripped of leading/trailing slashes and defaults to the repo root when not configured.

There is no separate `automations/` folder — scheduled automations are flow-native, so they travel inside `flows/<slug>.yaml`. The manifest still records an `automations` count.

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

## Two on-disk layouts (and which path uses which)

Nubi has **two** serialization formats that write resources to a branch. They are not interchangeable — each belongs to a different sync path:

| Format | Written by | Used by | Status |
|---|---|---|---|
| **`.yaml`** portability envelopes | `git/sync.py` `serialize_envelope` | Project-level `POST /git/push` (and `POST /git/pull` imports them back) | **Live** — the dashboards-as-code path. One file per resource: `dashboards/<slug>.yaml`, `queries/<slug>.yaml`, `flows/<slug>.yaml`. |
| **Version files** (`.sql` / `.meta.json` / `.json` + the flows-as-files tree) | `git/env_sync.py` `serialize_version_files` (+ `git/flow_files.py` for flows) | Environment ⇄ branch sync (checkpoint / promote / env push & pull) — pins one resource *version* per branch | **Live** — the env-sync path. `queries/<id>.sql` + `queries/<id>.meta.json` (plus an optional `queries/<id>.json` output-schema sidecar when the query declares one), `dashboards/<id>.json`, and a per-cell `flows/<slug>__<id8>/` directory per flow. Query/dashboard file stems are resource uuids. |

The two formats differ deliberately: the `.yaml` envelope path keys files by **slug** (human-readable, one current version per resource), while the env-sync path keys files by **uuid** and pins a specific **version** per environment branch. Within the env-sync path, flows are the one resource that gets a directory instead of a single file — the per-cell tree below is their canonical on-disk form. Legacy single-blob `flows/<id>.json` files are still *readable* on pull for back-compat, but are never written anymore.

### Flows on disk

`git/flow_files.py` projects a single `FlowSpec` onto a small, reviewable directory tree — the **file persona** of the [one flow, three views](/docs/flows#one-flow-three-views) model:

```
flows/<slug>__<id8>/
    flow.toml                 # flow metadata + ordered cells + [layout] table
    cells/01_<key>.sql        # source of an SQL cell      (config.sql)
    cells/02_<key>.py         # source of a Python cell    (config.code)
    cells/03_<key>.md         # source of a Markdown cell  (config.markdown)
```

- The directory is `flows/<slug>__<id8>`, where `<id8>` is the first 8 hex chars of the flow id (disambiguates same-named flows).
- **Lossless.** `flow.toml` stores the *full* cell dict minus two things peeled into separate places: the editable **source** text (moved to the sidecar file) and the canvas **ui** coordinates (moved to a `[layout]` table, so dragging a node on the canvas never dirties a cell's diff). Loading re-merges both, so any current or future cell field survives the round-trip.
- **Stable order.** Cells are written in spec order with a zero-padded `NN` prefix; the `[[cells]]` array in `flow.toml` is the authoritative load order.
- **Only three cell kinds get a sidecar file** — SQL (`.sql` from `config.sql`), Python (`.py` from `config.code`), and Markdown (`.md` from `config.markdown`). Every other kind (map, branch, materialize, bucket_load, agent, plain noop) has no single "source" to extract, so its whole config stays inline in `flow.toml`.

This layout is pure (no I/O) and is the **canonical on-disk form for flows in the env-sync path**: environment checkpoints, promotes, and env push/pull all read and write it. The flow's real uuid lives in `flow.toml`'s `[flow].id` (the directory's `<id8>` is only a disambiguator), so a pull resolves the resource id from the manifest, not from the path.

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

### `GET /api/v1/projects/{project_id}/git/files`

Read-only listing of the resource files tracked at a ref in the project's workspace repo. Used by the in-app file viewer; makes no network call and never returns a 5xx on git problems.

Query params:

| Param | Description |
|---|---|
| `ref` | Optional git ref. Defaults to the **production environment's bound branch**, falling back to the first env that carries a branch, then to `main` (see `_default_git_ref`). |

Response:

```json
{
  "ref":   "main",
  "files": ["queries/<id>.sql", "dashboards/<id>.json", "nubi.yaml"]
}
```

Lists the tracked files under the known resource folders (`queries`, `dashboards`, `flows`) plus the `nubi.yaml` manifest when present. Returns an **empty `files` list** when the project has no workspace repo yet, or on any git error — never an error status.

---

### `GET /api/v1/projects/{project_id}/git/files/content?path=<path>&ref=<ref>`

Read-only content of a single tracked file at a ref.

Query params:

| Param | Description |
|---|---|
| `path` | **Required.** Repo-relative path to the file. |
| `ref` | Optional; same default as the listing endpoint (prod env's bound branch → first branch → `main`). |

The `path` is **allowlisted**: it must live under one of the known resource folders (`queries`, `dashboards`, `flows`) or be exactly `nubi.yaml`. A path that is empty, absolute, contains a backslash, or has a `.`/`..` segment is rejected with **400** (`invalid_path`). A path that resolves to no tracked file at the ref returns **404** (`not_found`).

Response:

```json
{
  "path":    "queries/<id>.sql",
  "ref":     "main",
  "content": "SELECT 1"
}
```

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

Fetch the project's remote branch and import/upsert all resource envelopes (dashboards, queries, flows, and connector envelopes) into Nubi. The database stays canonical; git hydrates it. Flow envelopes that fail hard validation are skipped.

Request body:

```json
{ "project_id": "<uuid>" }
```

Response:

```json
{
  "imported": 9,
  "kinds":    { "dashboard": 5, "query": 3, "flow": 1, "connector": 0 }
}
```

---

### `POST /api/v1/environments/{env_id}/git/push`

Serialize every resource pinned in the environment to its bound branch as one commit, update `last_synced_sha`, and push to the project's remote when one is connected. Best-effort: an absent git layer returns `{committed: false, warnings: [...]}`, never a 5xx.

Request body (optional): `{ "message": "..." }`

Response:

```json
{
  "branch":          "dev",
  "sha":             "a1b2c3d4...",
  "committed":       true,
  "files":           12,
  "pushed":          true,
  "last_synced_sha": "a1b2c3d4...",
  "warnings":        []
}
```

---

### `POST /api/v1/environments/{env_id}/git/pull`

Sync the environment from its bound branch (fetching the remote when one is connected). Returns `{pulled: false, up_to_date: true, sha}` when nothing changed, `{pulled: true, sha, updated: {kind: n}}` on a fast-forward, and **409** `{diverged: true, files, env_sha, branch_sha}` on divergence.

Request body (optional):

```json
{ "strategy": "take_branch" }
```

`strategy` must be `"take_branch"` (import the branch state into the env) or `"take_env"` (overwrite the branch from the env's pinned state, force-with-lease); any other value is a 400 `invalid_strategy`.

---

### `GET /api/v1/projects/{project_id}/git/graph`

Return the project's commit graph: `{branches: [...]}` with one entry per env-bound branch — `{branch, env_key, head_sha, commits: [{sha, parents, message, author, date}]}`. Read-only; powers the in-app branch graph.

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

## Deploy from local / CI

The git-sync API above (Push / Pull in the in-app Git panel) keeps the database canonical and git as the mirror. The complementary **everything-as-code** path inverts that: you `nubi pull` the whole project to a real git repo, edit dashboards / queries / flows / connectors as files, and ship them back to the cloud — either by hand or from a CI pipeline on every push.

```bash
nubi login
nubi init --project <id>      # scaffold nubi.yaml + .nubi/ + .gitignore
nubi pull                     # download the full file tree
# …edit files, commit to git…
nubi push                     # upload changed NON-SECRET manifests
```

### CI deploy on push

`nubi init --ci github` (or `--ci gitlab`) copies a ready-made pipeline from `cli/templates/{github,gitlab}/` into your repo:

- **GitHub** → `.github/workflows/nubi-deploy.yml`
- **GitLab** → `.gitlab-ci.yml`

Seed the repo's CI secret store once with `nubi secrets push --target github|gitlab` (flow secrets upload as `NUBI_SECRET__*`, connector secrets as `NUBI_CONNECTOR__<SLUG>__<FIELD>`). On every push to `main` the pipeline then:

1. installs the CLI and authenticates with the `NUBI_TOKEN` repo secret;
2. runs `nubi secrets materialize` to expand the injected CI secrets back into the gitignored `.nubi/secrets/*.env` files;
3. runs `nubi deploy --env prod`, which pushes non-secret manifests **and** secrets to the target environment (idempotent ordering: secrets → connectors → flow secrets → import dashboards/queries/flows).

Plaintext secret values are never committed to git — only sealed/masked into the CI secret store. See [SDK & CLI](/docs/sdk-and-cli) for the full command tree and [Files-as-Code §C/§E](/docs/files-as-code) for the secret-sync and pipeline design.

> **Legacy.** `nubi deploy-files <dir>` and `nubi pull-raw <resource> <dir>` are the older flat-JSON workflows (resource types `datastores`, `boards`, `widgets`, `queries`); they are retained but superseded by `nubi push` / `nubi pull` on the canonical file tree.
