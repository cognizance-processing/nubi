# SDK & CLI

![Build and automate with the @nubi/sdk and nubi CLI](illustration:ConnectorSdk)

Two tools for building on Nubi programmatically: **`@nubi/sdk`** is a framework-agnostic JavaScript library for querying data, managing resources, and mounting embedded dashboards. **`nubi`** is a Python CLI for dashboards-as-code, flow management, and secrets.

---

## JavaScript SDK — `@nubi/sdk`

### Installation

`@nubi/sdk` is not yet published to npm. For now, build it from source and consume it as a local dependency:

```bash
cd sdk
npm install
npm run build   # outputs dist/nubi-sdk.js (ESM) and dist/nubi-sdk.umd.cjs (UMD/CJS)
```

Then install it into your app via a path or `file:` dependency:

```bash
npm install /path/to/nubi/sdk
# or in package.json: "@nubi/sdk": "file:../nubi/sdk"
```

Once the package is published, installation will be the usual:

```bash
npm install @nubi/sdk
```

`apache-arrow` is a runtime dependency and is bundled into the distributed output — no separate Arrow install needed.

### Creating a client

```js
import { createNubiClient } from '@nubi/sdk'

const client = createNubiClient({
  baseUrl: 'https://api.example.com',
  getToken: async () => myGetJwt(),
})
```

Both parameters are required:

| Parameter | Type | Description |
|-----------|------|-------------|
| `baseUrl` | `string` | Base URL of the Nubi backend. Trailing slashes and a pre-included `/api/v1` path are both handled correctly. |
| `getToken` | `string \| () => Promise<string>` | Called before every authenticated request. Pass an async function that silently mints or refreshes your JWT, or a static string for development. |

### Auth

```js
const { user } = await client.auth.me()
```

Returns the currently authenticated user. Calls `GET /api/v1/auth/me`.

### Queries

`client.query(sqlOrId, options?)` returns a Promise that resolves to an Apache Arrow `Table`. The backend responds with `Content-Type: application/vnd.apache.arrow.stream`.

```js
// Registered query id (no spaces, no SQL keyword)
const table = await client.query('revenue_by_month')

// Inline SQL with positional parameters
const table = await client.query(
  'SELECT region, SUM(amount) AS total FROM sales WHERE region = $1',
  { params: ['EMEA'] }
)

console.log(table.numRows)                   // row count
console.log(table.getChild('total').get(0))  // columnar access
```

The SDK auto-detects whether the argument is a query ID or raw SQL: strings containing whitespace or starting with a SQL keyword (`SELECT`, `WITH`, `INSERT`, etc.) are sent as `{ sql }`; all others are sent as `{ query_id }`.

`params` accepts three shapes:

| Shape | Example | Sent as |
|-------|---------|---------|
| Array | `{ params: ['EMEA'] }` | Positional `params` bound to `$1`, `$2`, … (index 0 binds `$1`) |
| Object with contiguous 1-based numeric keys (`'1'..'N'`) | `{ params: { '1': 'EMEA' } }` | Converted to a positional array (`'1'` binds `$1`) and sent as `params`; sparse or 0-based keys fall back to `named_params` |
| Object with named keys | `{ params: { region: 'EMEA' } }` | Sent as `named_params` — valid only for registered query ids that declare parameters |

### Resources — CRUD

Four resources are supported: **datastores**, **boards**, **widgets**, **queries**. Each has the same five methods:

| Method | HTTP | Description |
|--------|------|-------------|
| `.list()` | `GET /<resource>` | List all resources for the org |
| `.get(id)` | `GET /<resource>/:id` | Fetch a single resource |
| `.create(fields)` | `POST /<resource>` | Create a new resource |
| `.update(id, fields)` | `PUT /<resource>/:id` | Replace fields on an existing resource |
| `.remove(id)` | `DELETE /<resource>/:id` | Delete; returns `null` on 204 |

```js
// List boards
const boards = await client.resources.boards.list()

// Get a specific board
const board = await client.resources.boards.get('board-uuid')

// Create
const newBoard = await client.resources.boards.create({
  name: 'Q1 Dashboard',
  config: { spec: { version: 1, title: 'Q1 Dashboard', layout: {}, widgets: [] } },
})

// Update (partial patch via PUT)
await client.resources.boards.update('board-uuid', { name: 'Q1 Dashboard v2' })

// Delete
await client.resources.boards.remove('board-uuid')
```

All resources share the shape: `{ id, org_id, project_id, created_by, name, config, created_at, updated_at }`. When fetching a single resource with an environment override (`GET /<resource>/:id?env=<key>`), the response additionally includes `resolved_version`.

### Error handling

Failed requests throw an `Error` with two extra properties:

| Property | Type | Description |
|----------|------|-------------|
| `.code` | `string` | Backend error code from `{ error: { code, message } }` envelope, or `"http_error"` for non-JSON responses |
| `.message` | `string` | Human-readable error message |
| `.status` | `number` | HTTP status code |

```js
try {
  await client.resources.boards.get('nonexistent')
} catch (err) {
  console.error(err.code)    // e.g. "not_found"
  console.error(err.status)  // e.g. 404
}
```

### embed.mount()

Mounts a `<nubi-dashboard>` custom element inside a container. The host page must have already loaded the `nubi-dashboard` bundle so the custom element is registered.

```js
const { unmount } = client.embed.mount(
  document.getElementById('dashboard-root'),
  { query: 'revenue_by_month' }
)

// Tear down when done
unmount()
```

Options accepted by `mount()`:

| Option | Type | Description |
|--------|------|-------------|
| `query` | `string` | SQL string or registered query id |
| `token` | `string` | Static JWT. If omitted, the SDK wires up `getToken` automatically via a `window` bridge on the element. |
| `backend` | `string` | Override the backend URL; defaults to the `baseUrl` used when creating the client |

When wiring the element's `backend` attribute, the SDK normalizes a `baseUrl` that includes `/api/v1` — passing either the bare origin or the full API base works the same.

`unmount()` removes the element from the DOM and cleans up the `window` bridge if one was created.

### Re-exported Arrow utilities

The SDK re-exports `tableFromIPC` from `apache-arrow` so callers working directly with Arrow buffers do not need a separate dependency:

```js
import { tableFromIPC } from '@nubi/sdk'
```

### Building from source

```bash
cd sdk
npm install
npm run build   # outputs dist/nubi-sdk.js (ESM) and dist/nubi-sdk.umd.cjs (UMD/CJS)
npm test        # node --test src/index.test.mjs
```

---

## CLI — `nubi`

A Python CLI for the **everything-as-code** workflow: pull your whole Nubi project to a git repo, edit dashboards / queries / flows / connectors as files, keep secrets out of git, and push or deploy back to the cloud. Python 3.11+ required.

The CLI is the local companion to the in-app VS Code-style code view. The on-disk project format, the secret model, and the CI/CD design are specified in full in [Files-as-Code](/docs/files-as-code) — this page is the user-facing reference for the commands themselves.

### Installation

```bash
cd cli
pip install -e .
```

Install the optional `pyarrow` extra for precise row counts in `nubi run`:

```bash
pip install -e ".[arrow]"
```

Core dependencies (from `setup.py`): `typer>=0.12.0`, `httpx>=0.27.0`, `rich>=13.0.0`, `pyyaml>=6.0`. GitHub Actions secret sync additionally needs `PyNaCl` for libsodium sealing.

### Configuration

The CLI reads the API URL and Bearer token from environment variables or local files. Environment variables take precedence.

| Source | Variable / Path | Default |
|--------|----------------|---------|
| Env var | `NUBI_API_URL` | `http://localhost:8000/api/v1` |
| Env var | `NUBI_TOKEN` | — |
| File | `~/.nubi/credentials` | Written by `nubi login` |

### Quickstart

```bash
nubi login                      # authenticate; token saved to ~/.nubi/credentials
nubi init --project <id>        # scaffold nubi.yaml + .nubi/ + .gitignore here
nubi pull                       # download all resources into the local file tree

# …edit dashboards/, queries/, flows/, connectors/ as files; commit to git…

nubi push                       # upload changed NON-SECRET manifests
nubi deploy --env prod          # CI-style: materialize secrets + push manifests + secrets
```

`nubi init` writes a `.gitignore` that excludes `.nubi/secrets/` and `~/.nubi/credentials`, so credentials and secret values never enter git. Non-secret config (manifests) is committed; secrets stay in gitignored local `.env` files. See [the secret model](#secret-model) below.

### On-disk project layout

`nubi pull` writes a normal git repo:

```
my-project/
├─ nubi.yaml                 # project manifest (committed)
├─ .gitignore                # generated; ignores .nubi/secrets/
├─ .nubi/
│  ├─ project.json           # local pointer: {project_id, org_id, api_url, default_env}
│  └─ secrets/               # GITIGNORED — never committed
│     ├─ connectors.env      # connector secrets, <SLUG>__<FIELD>=value
│     └─ flow.env            # flow/org secrets ({{ secrets.NAME }} values)
├─ connectors/<slug>.yaml    # NON-SECRET connector manifests (committed)
├─ queries/<slug>.sql        # raw SQL + <slug>.meta.json (+ optional .schema.json)
├─ dashboards/<slug>.json    # {id, name, config}
└─ flows/<slug>__<id8>/      # flow.toml + cells/NN_<key>.{sql,py,md}
```

The resource UUID lives **inside** each file, so renames are safe and upserts key off the embedded id. See [Files-as-Code §A](/docs/files-as-code) for the authoritative format.

### Top-level commands

| Command | What it does |
|---|---|
| `nubi login` | Authenticate (`POST /auth/login`); save the token to `~/.nubi/credentials`. |
| `nubi logout` | Clear the local token (best-effort `POST /auth/logout`). |
| `nubi whoami` | Show the current user / org (`GET /auth/me`). |
| `nubi init [--project <id>] [--name <n>] [--env <key>] [--ci github\|gitlab] [dir]` | Scaffold `nubi.yaml`, `.nubi/project.json`, and `.gitignore`. `--ci` also copies the matching pipeline template. |
| `nubi pull [--env <key>] [--kinds <list>] [dir]` | Download ALL resources into the file tree. `--kinds` is a comma list of `dashboard,query,flow,connector`. Connectors write non-secret manifests only. |
| `nubi push [--dry-run] [--env <key>] [--kinds <list>] [dir]` | Upload changed non-secret manifests (`POST /import`; connectors upsert via `/connectors`). Secrets are NEVER pushed here. |
| `nubi sync --env-id <id> [--strategy take_branch\|take_env]` | Two-way reconcile local tree ↔ cloud via the project's git binding. |
| `nubi deploy [--env <key>] [dir]` | CI deploy: materialize secrets → push connectors + secrets → import dashboards/queries/flows. Idempotent. |
| `nubi diff <dir>` | Compare local resource files vs the server (read-only). Resources without an `id` are marked NEW. |
| `nubi run <query_id>` | Execute a registered query; print the row count (needs `pyarrow` for a precise count). |
| `nubi status [dir]` | Show the project binding, env, and last-sync commit graph. |

> **Legacy commands.** `nubi deploy-files <dir>` and `nubi pull-raw <resource> <dir>` are the older flat-JSON workflows (resource types: `datastores`, `boards`, `widgets`, `queries`). They are retained but superseded by `nubi push` / `nubi pull` on the canonical file tree.

```bash
# A typical edit/ship loop
nubi pull
$EDITOR dashboards/revenue.json
nubi push --dry-run        # preview the plan; no API calls
nubi push
```

### `nubi flows ...`

| Command | What it does |
|---|---|
| `nubi flows run <file> [--param k=v ...]` | Execute a flow LOCALLY end-to-end (in-memory store, `file://` storage). Same executor and registry as the cloud. |
| `nubi flows push [files ...] [--dry-run]` | Create / update flows in the cloud from YAML/JSON files (matched by name). Defaults to all `*.yaml`/`*.yml`/`*.json` in the cwd. |
| `nubi flows pull [--dir <dir>]` | Download flows as YAML files (falls back to JSON without PyYAML). Defaults to `flows/`. |

```bash
nubi flows run flows/my_flow.yaml --param region=us --param date=2024-01-01
```

`nubi flows run` resolves `{{ secrets.NAME }}` from the project's `.nubi/secrets/flow.env`, the legacy `~/.nubi/secrets`, and `NUBI_SECRET_<NAME>` env vars (env vars win). The backend package must be importable — run from a nubi checkout with `backend/` at the repo root.

### `nubi dashboards ... / queries ... / connectors ...`

Per-kind convenience wrappers over pull/push. Each takes `--dir/-d` (default: cwd).

| Command | What it does |
|---|---|
| `nubi dashboards pull` / `push` | Sync just dashboards (`GET /boards` + `/export/dashboard/{id}`; `POST /import`). |
| `nubi queries pull` / `push` | Sync just queries in the 3-file form (`.sql` + `.meta.json` + optional `.schema.json`). |
| `nubi connectors pull` | Write non-secret `connectors/<slug>.yaml` (`GET /connectors`). |
| `nubi connectors push` | Upsert connector non-secret config **plus** secrets read from `.nubi/secrets/connectors.env`. |
| `nubi connectors test <id>` | Validate config + secret resolvability (`POST /connectors/{id}/test`). |

### `nubi git ...`

| Command | What it does |
|---|---|
| `nubi git connect --provider github\|gitlab --repo-url <url> --token <pat> [--branch] [--base-path] [dir]` | Bind the project to a remote (`POST /git/connect`). The PAT is stored server-side; `nubi.yaml` records only the non-secret `provider`/`repo_url`. |
| `nubi git graph [dir]` | Print the env-branch commit graph (`GET /projects/{id}/git/graph`). |

<a id="secret-model"></a>

### `nubi secrets ...` — the secret model

Two stores, mirrored locally as gitignored `.env` files and never committed:

- **Flow / org secrets** → `.nubi/secrets/flow.env`, resolve as `{{ secrets.NAME }}` (cloud: `POST /secrets`).
- **Connector secrets** → `.nubi/secrets/connectors.env`, keyed `<CONNECTOR_SLUG>__<FIELD>` (e.g. `PROD_POSTGRES__PASSWORD`).

| Command | What it does |
|---|---|
| `nubi secrets set <name> <value> [--local-only] [--connector <slug>]` | Set a flow secret (also `POST /secrets` when logged in) or, with `--connector`, a connector secret field written to `connectors.env`. `--local-only` skips the API. |
| `nubi secrets list [--local-only]` | List secret names locally and from the API. Values are NEVER shown. |
| `nubi secrets pull [--dir]` | Scaffold empty `.env` keys from the cloud secret NAMES (values stay remote; existing values are never overwritten). |
| `nubi secrets push --target github\|gitlab [--token] [--env-scope]` | Seal local secrets into the repo's GitHub Actions / GitLab CI secret store. Keys are prefixed `NUBI_SECRET__*` / `NUBI_CONNECTOR__*`. GitHub values are libsodium-sealed (PyNaCl); GitLab are masked CI variables. Token falls back to `GITHUB_TOKEN`/`GITLAB_TOKEN`. |
| `nubi secrets materialize [--dir]` | Pipeline use (no API call): expand `NUBI_SECRET__*` / `NUBI_CONNECTOR__*` env vars back into the `.env` files. |
| `nubi secrets delete <name> [--dir]` | Delete a cloud flow/org secret (`DELETE /secrets/{name}`). |

```bash
# Seed the repo's CI secret store once, then deploy on every push (see below)
nubi secrets set STRIPE_API_KEY sk_live_...
nubi secrets set PROD_POSTGRES password s3cr3t --connector "Prod Postgres"
nubi secrets push --target github
```

### Deploying from CI/CD

`nubi init --ci github` (or `gitlab`) scaffolds a pipeline that, on every push to `main`, materializes the repo's secrets and runs `nubi deploy --env prod` — shipping your local edits to the cloud. The pipeline templates live in `cli/templates/{github,gitlab}/`. The full design (secret prefixes, `nubi deploy` ordering) is in [Files-as-Code §C/§E](/docs/files-as-code); a short operator walkthrough is in [Git Sync → Deploy from local / CI](/docs/git-sync#deploy-from-local--ci).

### Running CLI tests

```bash
cd cli && python -m pytest tests -q
```

---

## API overview

All Nubi REST endpoints are served under `/api/v1/` from the FastAPI backend.

- Every endpoint requires a valid Bearer token — either a first-party token minted by the backend, or an RS256/ES256 embed token from a registered issuer. See [Embedding](/docs/embedding) for embed token details.
- Resources are org-scoped; cross-org access returns 404 (not 403) to avoid information leakage.
- Query endpoints return `Content-Type: application/vnd.apache.arrow.stream`; all other endpoints return JSON.
- In development, FastAPI's interactive Swagger UI is available at `http://localhost:8000/docs` (disabled in `ENV=production`).
