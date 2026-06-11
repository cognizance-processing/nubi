# Files-as-Code: Local Project Format, Secrets, CLI, and CI/CD

Status: implemented — this doc is the contract the CLI (`cli/nubi_cli/`) and
backend follow.
Audience: power users who want an "everything-as-code" workflow — edit dashboards,
queries, connectors, and flows as files locally, keep secrets out of git, and
optionally deploy local → Nubi cloud via a pipeline.

This doc is grounded in the actual source. Behaviours that ride on long-standing
endpoints are marked **NOW**; endpoints that were added for this workflow are
marked **NEW** with their current ship status in Section F.

---

## 0. What already exists (grounding)

The design reuses these, do not reinvent them:

- **Portable envelope format** — `backend/app/portability.py`. K8s-style envelope
  `{kind, apiVersion: nubi/v1, metadata: {name, id?, project?}, spec}`. Kinds:
  `dashboard` (table `boards`), `query` (table `queries`), `flow` (flow store).
  Serialises YAML (primary) or JSON; `parse_document` round-trips. Connectors are
  deliberately **not** a portable kind here.
- **Portability routes** — `backend/app/routes/portability.py`:
  `GET /api/v1/export/{kind}/{id}?format=yaml|json` and `POST /api/v1/import`
  (upsert by `metadata.id`).
- **Git on-disk shape** — `backend/app/git/env_sync.py` (`serialize_version_files`,
  `KIND_FOLDER`, `FOLDER_KIND`) + `backend/app/git/flow_files.py`
  (`serialize_flow_files`, `load_flow_files`). Every project has ONE workspace repo
  at `<workspace>/<org_id>/projects/<project_id>`. Environments are bound to
  branches (`git_branch`); checkpoints commit serialized resources to the env
  branch. The exact on-disk shape the backend already writes:
  - `queries/<id>.sql` (raw SQL) + `queries/<id>.meta.json` (`{id, name, config}`
    minus `sql`/`output_schema`) + optional `queries/<id>.json` (output-schema sidecar)
  - `dashboards/<id>.json` (`{id, name, config}`)
  - `flows/<slug>__<id8>/flow.toml` + `cells/NN_<key>.{sql,py,md}` (per-cell source)
  - top-level manifest `nubi.yaml` (allow-listed in the git file-view).
- **Git file-view routes (read-only)** — `environments.py`:
  `GET /projects/{project_id}/git/files?ref=` and `.../git/files/content?path=&ref=`,
  `GET /projects/{project_id}/git/graph`,
  `POST /environments/{env_id}/git/push`, `POST /environments/{env_id}/git/pull`.
- **Project ↔ remote binding** — `backend/app/routes/git.py`:
  `POST /git/connect {project_id, provider:'github'|'gitlab', repo_url, branch, base_path, token}`.
  The PAT is stored in the connector secret store keyed by `project_id`; the
  project's `git` jsonb stores only `token_ref` (never the token). `POST /git/sync`,
  `GET /git/history`, `POST /git/restore` operate on the workspace repo.
- **Two secret stores**:
  - Flow/org secrets — `backend/app/secrets/store.py` (`secrets` table, migration
    0015), routes `POST/GET /secrets`, `DELETE /secrets/{name}`. Resolves
    `{{ secrets.NAME }}` via `resolve_all(org_id)` in the flows runtime.
  - Connector secrets — `backend/app/connectors/secret_store.py` (`connector_secrets`
    table, one blob per `datastore_id`, AES-256-GCM). Allow-listed secret keys in
    `routes/connectors.py::_SECRET_KEYS`.
- **Connector model** — `routes/connectors.py`. Non-secret config in
  `datastores.config` (`connector_type`, host, port, database, user, sslmode, …);
  secret keys (`password`, `service_account_json`, `token`, `api_key`,
  `access_token`, `aws_secret_access_key`, `private_key`) go to the connector
  secret store. Responses are scrubbed by `_sanitise`. CRUD: `POST/GET /connectors`,
  `GET/PUT/DELETE /connectors/{id}`, `POST /connectors/{id}/test`.
- **CLI today** — `cli/nubi_cli/` (`main.py`, `client.py`, `config.py`,
  `project.py`, `flows_files.py`, `secrets_files.py`, `vcs_secrets.py`).
  Top-level commands: `login`, `logout`, `whoami`, `init`, `pull`, `push`,
  `sync`, `deploy`, `diff`, `run`, `status` (plus legacy `deploy-files` /
  `pull-raw`). Sub-apps: `flows run/push/pull`, `dashboards pull/push`,
  `queries pull/push`, `connectors pull/push/test`,
  `secrets set/list/pull/push/materialize/delete`, `git connect/graph`,
  `auth create-key/list-keys/revoke-key`. Auth: Bearer token from `NUBI_TOKEN`
  env or `~/.nubi/credentials` (written by `login` → `POST /auth/login`
  `{access_token}`); the token can also be a long-lived API key (`nubi_ak_…`,
  see D). API base from `NUBI_API_URL` (default
  `http://localhost:8000/api/v1`). Local flow-run secrets read from the
  project's `.nubi/secrets/flow.env`, the legacy `~/.nubi/secrets` (JSON), and
  `NUBI_SECRET_<NAME>` env vars.

---

## A. On-disk project format

A checked-out Nubi project is a normal git repo. The layout mirrors exactly what
`env_sync.serialize_version_files` already writes, so `nubi pull`, the in-app git
file-view, and CI all agree on one shape. The one extension is a top-level
`connectors/` folder (connectors are tracked as **non-secret manifests** — see B).

```
my-project/
├─ nubi.yaml                         # project manifest (committed)
├─ .gitignore                        # generated; ignores .nubi/secrets/
├─ .nubi/
│  ├─ project.json                   # local pointer: {project_id, org_id, api_url, default_env}
│  └─ secrets/                       # GITIGNORED — never committed
│     ├─ connectors.env              # connector secrets, KEY=VALUE per (connector, field)
│     └─ flow.env                    # org/flow secrets ({{ secrets.NAME }} values)
├─ connectors/                       # NON-SECRET connector manifests (committed)
│  └─ <slug>.yaml                    # one file per connector
├─ queries/                          # one resource = 3 files (matches backend)
│  ├─ <slug>.sql                     # raw SQL (authoritative source)
│  ├─ <slug>.meta.json               # {id, name, config-minus-sql}
│  └─ <slug>.schema.json             # optional output-shape sidecar (only if declared)
├─ dashboards/
│  └─ <slug>.json                    # {id, name, config} (config.spec = DashboardSpec)
└─ flows/
   └─ <slug>__<id8>/
      ├─ flow.toml                   # metadata + ordered [[cells]] + [layout]
      └─ cells/
         ├─ 01_<key>.sql
         ├─ 02_<key>.py
         └─ 03_<key>.md
```

### File naming

- **One file/folder per resource.** Filenames use a **slug** derived from the
  resource name (see `portability.slug_for_envelope`: lowercase, non-alnum → `-`).
  The resource UUID lives **inside** the file (`meta.json.id`, `dashboard.json.id`,
  `flow.toml [flow].id`), never only in the filename — so renames are safe and
  upsert keys off the embedded id.
- **Backend compatibility note.** The backend's git serializer currently names
  files by `<id>` (`queries/<id>.sql`, `dashboards/<id>.json`,
  `flows/<slug>__<id8>/`). The local CLI format prefers human slugs for queries/
  dashboards but **must accept both** on read (id-named and slug-named). The flow
  folder already encodes both (`<slug>__<id8>`); reuse that convention. To keep
  one source of truth, the CLI's serializer is a thin wrapper over
  `app.git.env_sync.serialize_version_files` / `flow_files.serialize_flow_files`
  (importable in the dev checkout exactly like `flows_files._ensure_backend_on_path`
  does today) — see Phasing for the standalone-package path.

### Mapping to existing specs

| Resource  | On disk                                   | Spec source                                   | Envelope kind |
|-----------|-------------------------------------------|-----------------------------------------------|---------------|
| Dashboard | `dashboards/<slug>.json`                  | `config.spec` = DashboardSpec (`dashboards/spec.py`) | `dashboard` |
| Query     | `queries/<slug>.sql` + `.meta.json` (+ `.schema.json`) | `{sql, params, datastore_id, name, output_schema?}` (`portability._query_*`) | `query` |
| Flow      | `flows/<slug>__<id8>/flow.toml` + `cells/*` | FlowSpec (`flows/spec.py`), via `flow_files` | `flow` |
| Connector | `connectors/<slug>.yaml` (non-secret only) | `datastores.config` shape (`routes/connectors.py`) | `connector` (NEW kind, see F) |

Dashboards/queries/flows reuse the **portability envelope** for transport (export/
import) and the **git serializer** for on-disk layout. They are the same spec, two
serialisations: the envelope is the wire format; the file tree is the disk format.
The CLI converts between them.

### In-app code views (the same files, in the browser)

The file tree above is also how the app itself projects resources, so what you
edit locally is exactly what you see in the UI:

- **Flows** — the flow editor has a third view alongside canvas and notebook
  (`src/flows/FlowCodeView.jsx`): a VS Code-style explorer showing
  `flow.py` plus one file per cell (`cells/NN_<key>.sql`, `.py`, `.md`;
  kinds with no single source render as a read-only `.json` config dump).
  Cell edits write straight back to the FlowSpec; `flow.py` is the generated
  **nubi.flows Python SDK** source and round-trips through two endpoints:
  - `POST /flows/codegen` `{spec}` (or `POST /flows/{id}/codegen` for a saved
    flow) → `{source}` — FlowSpec → Python, a pure transformation that
    persists nothing.
  - `POST /flows/compile` `{code}` → `{spec, issues}` — Python → FlowSpec.
    The source runs in a sandboxed subprocess (15-second timeout, minimal
    env); your code must end with a `@flow`-decorated function whose
    `.compile()` result is assigned to `spec`. The **Apply** button in the
    code view calls this and syncs the compiled spec back to the canvas and
    notebook.

  ![Flow code view — flow.py and per-cell files](/docs/screenshots/flows-code.png)

- **Queries** — the query workspace (`src/pages/app/QueryWorkspace.jsx`) has a
  matching "Code / Files view (.sql + .meta.json)" toggle
  (`src/pages/app/QueryCodeView.jsx`) that projects the query as the 3-file
  shape from this doc: an editable `<slug>.sql` (the authoritative source)
  and a read-only `<slug>.meta.json` sidecar (`{id, name, datastore_id,
  params, output_schema?}`). Params are derived from `{{placeholders}}` in
  the SQL; id/name/datastore are edited via the toolbar, so the sidecar is a
  faithful read-only projection.

`flow.py` is a generated *projection*, not part of the committed tree — on disk
the canonical flow format stays `flow.toml + cells/*` (serialised by
`flow_files.py`), and the Python source is regenerated from the spec on demand.

### `nubi.yaml` (project manifest, committed)

```yaml
apiVersion: nubi/v1
kind: project
metadata:
  name: My Project
  id: <project_uuid>
  org: <org_uuid>
spec:
  default_env: dev
  environments: [dev, prod]          # informational mirror of backend envs
  git:
    provider: github                 # github | gitlab (informational)
    repo_url: https://github.com/acme/my-project
```

---

## B. Secret model

### What is secret vs not (connectors)

Authoritative split already lives in `routes/connectors.py`:

- **Non-secret** (committed in `connectors/<slug>.yaml`): everything in
  `ConnectorConfig` — `connector_type`, `host`, `port`, `database`, `user`,
  `sslmode`, `network_mode`, `bridge_id`, plus any extra non-secret fields
  (`http_json` `base_url`, `timeout`, …). Plus `name` and `id`.
- **Secret** (NEVER committed; lives in `.nubi/secrets/connectors.env`): the
  `_SECRET_KEYS` allow-list — `password`, `service_account_json`, `token`,
  `api_key`, `access_token`, `aws_secret_access_key`, `private_key`.

`connectors/<slug>.yaml` (committed):

```yaml
apiVersion: nubi/v1
kind: connector
metadata:
  name: Prod Postgres
  id: <datastore_uuid>
spec:
  connector_type: postgres
  host: db.internal
  port: 5432
  database: analytics
  user: readonly
  sslmode: require
  # secret fields referenced, never inlined:
  secrets: [password]                # which secret keys this connector expects
```

`.nubi/secrets/connectors.env` (gitignored). Key convention
`<CONNECTOR_SLUG>__<FIELD>` upper-snake:

```dotenv
PROD_POSTGRES__PASSWORD=s3cr3t
ANALYTICS_BQ__SERVICE_ACCOUNT_JSON={"type":"service_account",...}
```

### Flow / org secrets

Flow secrets resolve via `{{ secrets.NAME }}` against the org `secrets` table
(`app/secrets/store.py`). Locally they live in `.nubi/secrets/flow.env`:

```dotenv
STRIPE_API_KEY=sk_live_...
SLACK_WEBHOOK=https://hooks.slack.com/...
```

These map 1:1 to `POST /secrets {name, value}` and are surfaced (names only) at
`GET /secrets`. They power the in-app flow secrets UI (`SecretsPage.jsx`,
`SqlCell.jsx`, `SecretsMenu.jsx`) and `{{ secrets.NAME }}` interpolation.

### How `{{ secrets.NAME }}` resolves

- **Locally** (`nubi flows run`, `nubi run`): the CLI loads `.nubi/secrets/flow.env`
  + `NUBI_SECRET_<NAME>` env vars and injects them through the runtime's secret
  seam (`app.secrets.store.set_secret_store`) — this is exactly what
  `main.py::_patch_secrets_store` already does, just sourced from the project's
  `.nubi/secrets/flow.env` instead of `~/.nubi/secrets`.
- **In cloud**: the flows runtime calls `get_secret_store().resolve_all(org_id)`
  against the encrypted `secrets` table. Connector secrets resolve via the
  connector secret store keyed by `datastore_id`. Nothing changes here.

### Generated `.gitignore`

`nubi init` / `nubi pull` writes (idempotently appends if present):

```gitignore
# Nubi local secrets — never commit
.nubi/secrets/
.nubi/credentials
*.local.env
```

### Local credentials

CLI auth token stays in `~/.nubi/credentials` (machine-global, as today) — NOT in
the repo. `.nubi/project.json` (committed-or-not is the user's choice; default
committed minus any token) carries only non-secret pointers: `project_id`,
`org_id`, `api_url`, `default_env`.

---

## C. GitHub / GitLab secret sync

Goal: `nubi secrets push --target github` writes the local `.nubi/secrets/*.env`
values into the repo's Actions/CI secret store so pipelines can consume them
WITHOUT the plaintext ever being committed.

### Auth

Reuse the PAT already bound to the project via `POST /git/connect` where possible,
but secret-store APIs need broader scopes than read/write repo, so the CLI accepts
an explicit admin token:

- GitHub: `--token $GITHUB_TOKEN` (PAT with `repo` + `admin:repo_hook`/secrets
  scope, or fine-grained "Secrets: read/write"). Falls back to `gh auth token`.
- GitLab: `--token $GITLAB_TOKEN` (PAT with `api` scope). Falls back to `glab`.

The CLI resolves `repo_url`/`provider` from `nubi.yaml` `spec.git`.

### GitHub Actions secrets (libsodium-sealed)

GitHub requires each secret value be encrypted with the repo public key before
upload (`PUT /repos/{owner}/{repo}/actions/secrets/{name}`):

1. `GET /repos/{owner}/{repo}/actions/secrets/public-key` → `{key, key_id}`.
2. Seal each value with libsodium (`PyNaCl`) using the repo public key.
3. `PUT .../actions/secrets/{SECRET_NAME}` with
   `{encrypted_value, key_id}`.

Secret names are uppercased; the CLI prefixes connector secrets as
`NUBI_CONNECTOR__<NAME>` and flow secrets as `NUBI_SECRET__<NAME>` to avoid
collisions and to make pipeline consumption predictable.

### GitLab CI/CD variables

Simpler (no sealing):
`POST /projects/:id/variables {key, value, protected, masked, environment_scope}`
(create) / `PUT /projects/:id/variables/:key` (update). The CLI sets
`masked: true` and an `environment_scope` matching the Nubi env (`dev`/`prod`).

### How pipelines consume them

The pipeline regenerates the gitignored `.nubi/secrets/*.env` from the injected
CI secrets before `nubi deploy`:

- Connector secrets: every `NUBI_CONNECTOR__<SLUG>__<FIELD>` → line in
  `connectors.env`.
- Flow secrets: every `NUBI_SECRET__<NAME>` → line in `flow.env`.

`nubi secrets materialize` (no backend call) does this expansion from
env vars so the pipeline step is one line. `nubi deploy` then pushes both
non-secret manifests AND secrets to the cloud (connector secrets via
`PUT /connectors/{id}`, flow secrets via `POST /secrets`).

---

## D. CLI surface

All commands hit `${NUBI_API_URL}` with the Bearer token (`load_token`). Every
command below is implemented in `cli/nubi_cli/main.py`.

### Top-level

| Command | Purpose | Backend endpoint(s) |
|---|---|---|
| `nubi login` | Auth, save token to `~/.nubi/credentials`. | `POST /auth/login` |
| `nubi logout` | Clear local token. | `POST /auth/logout` (best-effort) |
| `nubi whoami` | Show current user/org. | `GET /auth/me` |
| `nubi init [--project <id>] [--ci github\|gitlab]` | Scaffold a local project: write `.nubi/project.json`, `nubi.yaml`, `.gitignore` (+ CI template). | none (local); reads `GET /projects/{id}` when bound |
| `nubi pull [--env <key>] [--kinds ...]` | Download ALL project resources into the file tree (A). | One-shot `GET /projects/{id}/export` bundle when bound; falls back to per-resource `GET /export/{kind}/{id}` + list endpoints on a 404/405 (older backend) |
| `nubi push [--dry-run] [--env <key>]` | Upload changed non-secret manifests (dashboards/queries/flows/connectors). | Bulk `POST /projects/{id}/import` when bound; falls back to per-resource `POST /import` (connectors via `POST/PUT /connectors`) |
| `nubi sync --env-id <id> [--strategy ...]` | Two-way reconcile local tree ↔ cloud via the project's git binding. | `POST /environments/{env_id}/git/push` + `.../git/pull` |
| `nubi deploy [--env prod]` | CI-oriented: materialize secrets, push connector manifests + secrets, flow secrets, then import dashboards/queries/flows. | `PUT /connectors/{id}`, `POST /secrets`, `POST /projects/{id}/import` (fallback `POST /import`) |
| `nubi diff <dir>` | Show local-vs-cloud differences (read-only). | per-resource `GET` endpoints |
| `nubi run <query_id>` | Execute a registered query, print the row count. | `POST /query` |
| `nubi status` | Show project binding, env, and env-branch heads. | `GET /projects/{id}/git/graph` |

### `nubi flows ...`

| Command | Purpose | Endpoint |
|---|---|---|
| `nubi flows run <file> [--param ...]` | Local end-to-end run (in-memory store, local secrets). | none (local runtime) |
| `nubi flows push [files] [--dry-run]` | Create/update flows in cloud from YAML/JSON FlowSpec files (matched by name). | `GET /flows`, `POST/PUT /flows` |
| `nubi flows pull [--dir]` | Download flows as one YAML file each (JSON without PyYAML). The canonical `flows/<slug>__<id8>/` tree is written by `nubi pull --kinds flow`. | `GET /flows` |

### `nubi dashboards ...` / `nubi queries ...` / `nubi connectors ...`

Per-kind convenience wrappers over pull/push/import:

| Command | Purpose | Endpoint |
|---|---|---|
| `nubi dashboards pull/push [--dir]` | Sync just dashboards. | `GET /boards`, `GET /export/dashboard/{id}`, `POST /import` |
| `nubi queries pull/push [--dir]` | Sync just queries (3-file form). | `GET /queries`, `GET /export/query/{id}`, `POST /import` |
| `nubi connectors pull` | Write non-secret `connectors/<slug>.yaml`. | `GET /export/connector/{id}`, falling back to the scrubbed `GET /connectors` list row |
| `nubi connectors push` | Upsert connector non-secret config + secrets from `.nubi/secrets/connectors.env`. | `POST/PUT /connectors` |
| `nubi connectors test <id>` | Validate config + secret resolvable. | `POST /connectors/{id}/test` |

### `nubi secrets ...`

| Command | Purpose | Endpoint |
|---|---|---|
| `nubi secrets set <name> <value> [--local-only] [--connector <slug>]` | Set a flow secret (or, with `--connector`, a connector secret field) in the right `.nubi/secrets/*.env` (+ cloud). | `POST /secrets`; connector secret rotated via `PUT /connectors/{id}` on next push |
| `nubi secrets list [--local-only]` | List secret names (local + cloud). | `GET /secrets` |
| `nubi secrets pull` | Download secret NAMES from cloud, scaffold empty `.env` keys (values never leave the cloud). | `GET /secrets`, `GET /connectors` |
| `nubi secrets push --target github\|gitlab [--token]` | Write local secrets to GH Actions / GitLab CI stores (C). | GitHub/GitLab REST (external); no Nubi endpoint |
| `nubi secrets materialize` | Expand `NUBI_SECRET__*` / `NUBI_CONNECTOR__*` env vars into `.nubi/secrets/*.env` (pipeline use). | none |
| `nubi secrets delete <name>` | Delete a cloud secret. | `DELETE /secrets/{name}` |

### `nubi git ...`

| Command | Purpose | Endpoint |
|---|---|---|
| `nubi git connect --provider --repo-url --token [--branch] [--base-path]` | Bind project to a remote; mirrors the non-secret binding into `nubi.yaml spec.git`. | `POST /git/connect` |
| `nubi git graph` | Print env-branch commit graph. | `GET /projects/{id}/git/graph` |

### `nubi auth ...` — API keys for CI

Long-lived API keys replace password-minted JWTs in pipelines. The raw key is
`nubi_ak_<43-char-base64url>` (256 bits of entropy, `backend/app/auth/api_keys.py`);
the `nubi_ak_` prefix lets the backend tell an API key from a JWT, and the key
authenticates as a normal Bearer token — set it as `NUBI_TOKEN` in CI.

| Command | Purpose | Endpoint |
|---|---|---|
| `nubi auth create-key [--name <label>]` | Mint a key and print it **once** — it is never retrievable again (only a hash + the last 4 chars are stored). | `POST /auth/api-keys` |
| `nubi auth list-keys` | List keys: id, name, last 4, created/last-used/revoked timestamps. Secrets never shown. | `GET /auth/api-keys` |
| `nubi auth revoke-key <id>` | Revoke a key. | `DELETE /auth/api-keys/{id}` |

---

## E. CI/CD pipelines

Both pipelines: install the CLI, auth via a repo secret (`NUBI_TOKEN` — best
minted as a long-lived API key with `nubi auth create-key`, see D), materialize
secrets from CI vars, deploy to the target Nubi env. Pre-seed the repo secrets
once with `nubi secrets push` (C). `nubi init --ci github|gitlab` scaffolds the
matching template from `cli/templates/`.

### GitHub Actions — `.github/workflows/nubi-deploy.yml`

```yaml
name: Deploy to Nubi
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: prod
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - name: Install Nubi CLI
        run: pip install nubi-cli   # or: pip install -e ./cli (monorepo)
      - name: Materialize secrets from repo secrets
        env:
          NUBI_SECRET__STRIPE_API_KEY: ${{ secrets.NUBI_SECRET__STRIPE_API_KEY }}
          NUBI_CONNECTOR__PROD_POSTGRES__PASSWORD: ${{ secrets.NUBI_CONNECTOR__PROD_POSTGRES__PASSWORD }}
        run: nubi secrets materialize
      - name: Deploy
        env:
          NUBI_API_URL: ${{ vars.NUBI_API_URL }}
          NUBI_TOKEN: ${{ secrets.NUBI_TOKEN }}
        run: nubi deploy --env prod
```

### GitLab CI — `.gitlab-ci.yml`

```yaml
stages: [deploy]
deploy_nubi:
  stage: deploy
  image: python:3.11-slim
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  variables:
    NUBI_API_URL: $NUBI_API_URL          # CI/CD variable
  script:
    - pip install nubi-cli
    - nubi secrets materialize           # reads NUBI_SECRET__* / NUBI_CONNECTOR__* CI vars
    - nubi deploy --env prod             # uses $NUBI_TOKEN (masked CI variable)
```

`nubi deploy` ordering (idempotent): (1) `secrets materialize`; (2) push connector
manifests + connector secrets; (3) push flow/org secrets (`POST /secrets`);
(4) import dashboards/queries/flows — bulk via `POST /projects/{id}/import` when
the project is bound, per-resource `POST /import` otherwise. Checkpointing and
environment promotion stay explicit (in-app or via the versions/environments
APIs) — `deploy` does not promote for you.

---

## F. Phasing — ship status

### Phase 1 — shipped on long-standing endpoints

- CLI scaffolding: `init`, `whoami`, `logout`, `status`, `git connect/graph`,
  `secrets pull/materialize/delete`, per-kind wrappers.
- `pull`/`push`/`diff` for **dashboards, queries, flows** via
  `GET /export/{kind}/{id}` + `POST /import` + list endpoints.
- The full file-tree (A) for dashboards/queries/flows by reusing
  `env_sync.serialize_version_files` / `flow_files` (import in dev checkout),
  with a self-contained fallback in `cli/nubi_cli/project.py` for the
  standalone-package path.
- Connector pull/push via `GET/POST/PUT /connectors`.
- `secrets push` to GitHub/GitLab (pure client-side; external REST —
  `cli/nubi_cli/vcs_secrets.py`).
- `sync` via `POST /environments/{env_id}/git/push|pull`.
- Both CI pipeline templates (`cli/templates/{github,gitlab}/`).
- Generated `.gitignore`, `.nubi/secrets/*.env`, `nubi.yaml`.

### Phase 2 — the NEW backend endpoints, and where they stand

1. **`GET /export/connector/{id}`** — **shipped.** A `connector` handler lives in
   `KIND_REGISTRY` (`backend/app/portability.py`) whose `spec_from_row` carries
   NON-SECRET config only, scrubbed with the same `_SECRET_KEYS` allow-list as
   `routes/connectors.py::_sanitise`. `nubi connectors pull` prefers it and
   falls back to the scrubbed list row on older backends.

2. **`POST /import` connector support** — **shipped.** The import upsert path
   handles `kind: connector` (upsert into `datastores`, never touching the
   secret store); envelopes containing secret fields are rejected at validation.

3. **`GET /projects/{project_id}/export` (project bundle)** — **shipped**
   (`backend/app/routes/projects_bundle.py`): the full project as one JSON list
   of envelopes, so `nubi pull` is one round-trip. The CLI falls back to the
   per-resource loop on 404/405 (older backend).

4. **`POST /projects/{project_id}/import` (project bundle apply)** — **shipped**
   (same module): bulk upsert used by `nubi push` / `nubi deploy`, returning a
   per-resource results table. Same 404/405 fallback.

5. **`PUT /connectors/{id}/secret` (granular secret rotation)** — **not built.**
   `PUT /connectors/{id}` accepts `{secret}` and rotates the whole blob; the
   CLI sends the full secret blob on push. A field-level endpoint remains a
   possible refinement.

6. **Long-lived API keys** — **shipped** (`backend/app/auth/api_keys.py` +
   `routes/auth.py`): `POST /auth/api-keys` mints a `nubi_ak_…` key returned
   exactly once (only a hash + last four chars are stored), `GET /auth/api-keys`
   lists, `DELETE /auth/api-keys/{id}` revokes. CLI: `nubi auth
   create-key/list-keys/revoke-key` (see D). Use one as the `NUBI_TOKEN` CI
   secret instead of a short-lived login JWT.

### Backend endpoints added for this workflow — precise list

- `GET /api/v1/export/connector/{id}` — non-secret connector config envelope. **(F-1, shipped)**
- `POST /api/v1/import` accepting `kind: connector` — non-secret upsert into `datastores`. **(F-2, shipped)**
- `GET /api/v1/projects/{project_id}/export` — whole-project bundle export. **(F-3, shipped)**
- `POST /api/v1/projects/{project_id}/import` — whole-project bundle apply. **(F-4, shipped)**
- `PUT /api/v1/connectors/{id}/secret` — field-level connector secret set/rotate. **(F-5, not built)**
- `POST /api/v1/auth/api-keys` + `GET` + `DELETE /api/v1/auth/api-keys/{id}` — long-lived CLI/CI tokens. **(F-6, shipped)**
