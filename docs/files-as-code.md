# Files-as-Code: Local Project Format, Secrets, CLI, and CI/CD

Status: design contract (implementation agents follow this).
Audience: power users who want an "everything-as-code" workflow — edit dashboards,
queries, connectors, and flows as files locally, keep secrets out of git, and
optionally deploy local → Nubi cloud via a pipeline.

This doc is grounded in what already exists. Where a behaviour is buildable on
today's endpoints it is marked **NOW**; where a backend addition is required it
is marked **NEW**. Section F lists every NEW endpoint precisely.

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
  `flows_files.py`). Commands: `login`, `deploy`, `run`, `diff`, `pull`,
  `flows run/push/pull`, `secrets set/list`. Auth: Bearer token from `NUBI_TOKEN`
  env or `~/.nubi/credentials` (written by `login` → `POST /auth/login`
  `{access_token}`). API base from `NUBI_API_URL` (default
  `http://localhost:8000/api/v1`). Local flow-run secrets read from `~/.nubi/secrets`
  (JSON) and `NUBI_SECRET_<NAME>` env vars.

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

`nubi secrets materialize` (NEW CLI cmd, no backend call) does this expansion from
env vars so the pipeline step is one line. `nubi deploy` then pushes both
non-secret manifests AND secrets to the cloud (connector secrets via
`PUT /connectors/{id}`, flow secrets via `POST /secrets`).

---

## D. CLI surface

All commands hit `${NUBI_API_URL}` with the Bearer token (`load_token`). New
commands marked **NEW-CLI**; backend endpoints cited, **NEW** where they must be added.

### Top-level

| Command | Purpose | Backend endpoint(s) |
|---|---|---|
| `nubi login` | Auth, save token to `~/.nubi/credentials`. | `POST /auth/login` (NOW) |
| `nubi logout` | Clear local token. **NEW-CLI** | `POST /auth/logout` (NOW) |
| `nubi whoami` | Show current user/org. **NEW-CLI** | `GET /auth/me` (NOW) |
| `nubi init [--project <id>]` | Scaffold a local project: write `.nubi/project.json`, `nubi.yaml`, `.gitignore`. **NEW-CLI** | none (local); reads `GET /projects` (NOW) |
| `nubi pull [--env <key>] [--kinds ...]` | Download ALL project resources into the file tree (A). | `GET /export/{kind}/{id}` per resource + list endpoints (NOW for dashboards/queries/flows); connectors need **NEW** export (see F) |
| `nubi push [--dry-run] [--env <key>]` | Upload changed non-secret manifests (dashboards/queries/flows/connectors). | `POST /import` (NOW, dashboards/queries/flows); connectors via `POST/PUT /connectors` (NOW) or **NEW** connector import |
| `nubi sync [--strategy ...]` | Two-way reconcile local tree ↔ cloud via the project's git binding. | `POST /environments/{env_id}/git/push` + `.../git/pull` (NOW) |
| `nubi deploy [--env prod]` | CI-oriented: materialize secrets, push manifests + secrets, checkpoint+promote to env. | `POST /import`, `POST /secrets`, `PUT /connectors/{id}`, `POST /versions/{kind}/{id}`, `POST /environments/promote` (all NOW) |
| `nubi diff [--env <key>]` | Show local-vs-cloud differences (read-only). Extend existing `diff` to the new tree. | `GET /export/{kind}/{id}` (NOW) |
| `nubi run <query_id> [--param ...]` | Execute a registered query, print rows. | `POST /query` (NOW) |
| `nubi status` | Show project binding, env, dirty files, last sync sha. **NEW-CLI** | `GET /projects/{id}/git/graph` (NOW) |

### `nubi flows ...`

| Command | Purpose | Endpoint |
|---|---|---|
| `nubi flows run <file> [--param ...]` | Local end-to-end run (in-memory store, local secrets). | none (local runtime; NOW) |
| `nubi flows push [files] [--dry-run]` | Create/update flows in cloud. | `GET /flows`, `POST/PUT /flows` (NOW) |
| `nubi flows pull [--dir]` | Download flows as files. Switch default to the canonical `flows/<slug>__<id8>/` tree. | `GET /flows` (NOW) |

### `nubi dashboards ...` / `nubi queries ...` / `nubi connectors ...` (NEW-CLI)

Per-kind convenience wrappers over pull/push/import:

| Command | Purpose | Endpoint |
|---|---|---|
| `nubi dashboards pull/push [--dir]` | Sync just dashboards. | `GET /boards`, `GET /export/dashboard/{id}`, `POST /import` (NOW) |
| `nubi queries pull/push [--dir]` | Sync just queries (3-file form). | `GET /queries`, `GET /export/query/{id}`, `POST /import` (NOW) |
| `nubi connectors pull` | Write non-secret `connectors/<slug>.yaml`. | `GET /connectors` (NOW) |
| `nubi connectors push` | Upsert connector non-secret config + secrets from `.nubi/secrets/connectors.env`. | `POST/PUT /connectors` (NOW); upsert-by-id smoother with **NEW** connector import (F) |
| `nubi connectors test <id>` | Validate config + secret resolvable. | `POST /connectors/{id}/test` (NOW) |

### `nubi secrets ...`

| Command | Purpose | Endpoint |
|---|---|---|
| `nubi secrets set <name> <value> [--local-only] [--connector <slug>]` | Set a flow or connector secret locally (+ cloud). Extend to write the right `.nubi/secrets/*.env`. | `POST /secrets` (NOW); connector secret via `PUT /connectors/{id}` (NOW) |
| `nubi secrets list [--local-only]` | List secret names (local + cloud). | `GET /secrets` (NOW), `GET /connectors` (NOW) |
| `nubi secrets pull` | Download secret NAMES from cloud, scaffold empty `.env` keys (values never leave the cloud). **NEW-CLI** | `GET /secrets`, `GET /connectors` (NOW) |
| `nubi secrets push [--target github\|gitlab] [--token]` | Write local secrets to GH Actions / GitLab CI stores (C). **NEW-CLI** | GitHub/GitLab REST (external); no Nubi endpoint |
| `nubi secrets materialize` | Expand `NUBI_SECRET__*` / `NUBI_CONNECTOR__*` env vars into `.nubi/secrets/*.env` (pipeline use). **NEW-CLI** | none |
| `nubi secrets delete <name>` | Delete a cloud secret. **NEW-CLI** | `DELETE /secrets/{name}` (NOW) |

### `nubi git ...` (NEW-CLI)

| Command | Purpose | Endpoint |
|---|---|---|
| `nubi git connect --provider --repo-url --token` | Bind project to a remote. | `POST /git/connect` (NOW) |
| `nubi git graph` | Print env-branch commit graph. | `GET /projects/{id}/git/graph` (NOW) |

---

## E. CI/CD pipelines

Both pipelines: install the CLI, auth via a repo secret (`NUBI_TOKEN`),
materialize secrets from CI vars, deploy to the target Nubi env. Pre-seed the
repo secrets once with `nubi secrets push` (C).

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
(4) `POST /import` dashboards/queries/flows; (5) checkpoint each resource
(`POST /versions/{kind}/{id}`) and `POST /environments/promote` to the target env.

---

## F. Phasing — buildable NOW vs NEW backend endpoints

### Phase 1 — buildable NOW (no backend changes)

- New CLI scaffolding: `init`, `whoami`, `logout`, `status`, `git connect/graph`,
  `secrets pull/materialize/delete`, per-kind wrappers.
- `pull`/`push`/`diff` for **dashboards, queries, flows** via existing
  `GET /export/{kind}/{id}` + `POST /import` + list endpoints.
- The full file-tree (A) for dashboards/queries/flows by reusing
  `env_sync.serialize_version_files` / `flow_files` (import in dev checkout).
- Connector pull/push via existing `GET/POST/PUT /connectors`.
- `secrets push` to GitHub/GitLab (pure client-side; external REST).
- `sync` via existing `POST /environments/{env_id}/git/push|pull`.
- Both CI pipeline templates.
- Generated `.gitignore`, `.nubi/secrets/*.env`, `nubi.yaml`.

### Phase 2 — requires NEW backend endpoints

1. **`GET /export/connector/{id}`** — export a connector's NON-SECRET config as a
   `kind: connector` envelope (scrubbed exactly like `routes/connectors.py::_sanitise`).
   Needed so `nubi pull` treats connectors uniformly with other kinds. Add a
   `connector` handler to `KIND_REGISTRY` in `backend/app/portability.py` (with
   `spec_from_row` = non-secret config, NO secret material) and have the export
   route accept it. *Note:* `portability.py` currently documents connectors as
   out-of-scope; this is an intentional, scoped expansion limited to non-secret config.

2. **`POST /import` connector support** — extend the import upsert path to handle
   `kind: connector` (upsert into `datastores` via the connectors create/update
   logic, never touching the secret store). Lets `nubi push` import a
   `connectors/<slug>.yaml` by embedded `metadata.id` like every other kind.

3. **`GET /projects/{project_id}/export` (project bundle)** — return the full
   project as one tarball/zip or a JSON list of envelopes (all dashboards, queries,
   flows, connector-non-secret) in ONE call, so `nubi pull` is one round-trip
   instead of N. Optional optimisation; Phase 1 works with per-resource calls.

4. **`POST /projects/{project_id}/import` (project bundle apply)** — counterpart
   bulk upsert for `nubi deploy`, applying a tree atomically and returning a
   per-resource result. Optional optimisation.

5. **`PUT /connectors/{id}/secret` (granular secret rotation)** — currently
   `PUT /connectors/{id}` accepts `{secret}` and rotates the whole blob. A
   field-level set endpoint would let `nubi secrets set --connector` rotate a
   single field without resending the rest. Optional; Phase 1 sends the full
   secret blob on `PUT /connectors/{id}`.

6. **CLI auth token without password (optional)** — a long-lived
   **`POST /auth/api-keys`** (mint a CLI/CI token) + **`DELETE /auth/api-keys/{id}`**.
   Today CI uses a `NUBI_TOKEN` minted from `POST /auth/login` (short-lived access
   token + refresh cookie) — fine for Phase 1, but a dedicated non-expiring CI
   token store is cleaner for pipelines. Optional.

### NEW backend endpoints — precise list

- `GET /api/v1/export/connector/{id}` — export non-secret connector config envelope. **(F-1, required for uniform connector pull)**
- `POST /api/v1/import` — extend to accept `kind: connector` (non-secret upsert into `datastores`). **(F-2, required for uniform connector push)**
- `GET /api/v1/projects/{project_id}/export` — whole-project bundle export. **(F-3, optional optimisation)**
- `POST /api/v1/projects/{project_id}/import` — whole-project bundle apply. **(F-4, optional optimisation)**
- `PUT /api/v1/connectors/{id}/secret` — field-level connector secret set/rotate. **(F-5, optional)**
- `POST /api/v1/auth/api-keys` + `DELETE /api/v1/auth/api-keys/{id}` — long-lived CLI/CI tokens. **(F-6, optional)**

Everything in Section D not depending on the above is buildable on today's API.
