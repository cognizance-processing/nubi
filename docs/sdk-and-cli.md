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

A Python CLI for dashboards-as-code workflows, flow management, and secrets. Python 3.11+ required.

### Installation

```bash
cd cli
pip install -e .
```

Install the optional `pyarrow` extra for precise row counts in `nubi run`:

```bash
pip install -e ".[arrow]"
```

Core dependencies (from `setup.py`): `typer>=0.12.0`, `httpx>=0.27.0`, `rich>=13.0.0`, `pyyaml>=6.0`.

### Configuration

The CLI reads the API URL and Bearer token from environment variables or local files. Environment variables take precedence.

| Source | Variable / Path | Default |
|--------|----------------|---------|
| Env var | `NUBI_API_URL` | `http://localhost:8000/api/v1` |
| Env var | `NUBI_TOKEN` | — |
| File | `~/.nubi/credentials` | Written by `nubi login` |

### Commands

#### `nubi login`

Authenticate and save the access token to `~/.nubi/credentials`.

```bash
nubi login
# Email: you@example.com
# Password: ••••••
```

#### `nubi deploy <dir> [--dry-run]`

Push all `*.json` resource files in `<dir>` to the API. Each JSON file must have at minimum a `"resource"` field (one of `datastores`, `boards`, `widgets`, `queries`) and a `"name"` field — the CLI validates this before deploying, and files missing either field or naming an unknown resource type are skipped with an error. If an `"id"` is present the resource is updated (PUT); otherwise it is created (POST).

```json
{ "resource": "boards", "name": "My Dashboard", "config": {} }
```

```bash
nubi deploy ./dashboards --dry-run   # preview plan; no API calls
nubi deploy ./dashboards             # live deploy
```

#### `nubi run <query_id>`

Execute a registered query and print the row count. Requires `pyarrow` for a precise count; without it the raw byte length of the Arrow IPC response is reported.

```bash
nubi run 3fa85f64-5717-4562-b3fc-2c963f66afa6
# Query '3fa85f64-...' returned 1,234 row(s).
```

#### `nubi diff <dir>`

Compare local resource files against the server state. Read-only — no writes are made. Resources without an `"id"` are reported as NEW.

```bash
nubi diff ./dashboards
# board.json (id=board-123)
#   - name: 'Old Name'
#   + name: 'New Name'
```

#### `nubi pull <resource> <dir>`

Download all server resources of a given type to local JSON files, one file per resource named `<id>.json`. Valid resource types: `datastores`, `boards`, `widgets`, `queries`.

```bash
nubi pull boards ./downloaded/boards
# Wrote ./downloaded/boards/board-123.json
# Pulled 3 boards.
```

---

### Flows sub-commands

#### `nubi flows run <file> [--param key=value ...]`

Execute a flow locally end-to-end using an in-memory store and `file://` storage. Resolves parameters from `--param` flags and the local secrets file (`~/.nubi/secrets`), then drives the flows runtime to completion — same executor and registry as the cloud. Prints each task's state and result when done.

The backend package must be importable. Run from a nubi checkout with `backend/` present at the repo root.

```bash
nubi flows run flows/my_flow.yaml --param region=us --param date=2024-01-01
```

Secrets can also be supplied as `NUBI_SECRET_<NAME>` environment variables; they override the local secrets file.

#### `nubi flows push [file ...] [--dry-run]`

Create or update flows in the cloud from local YAML or JSON files. Matches by flow name: if a flow with the same name already exists it is updated (PUT); otherwise it is created (POST). If no files are specified, all `*.yaml`/`*.yml`/`*.json` files in the current directory are used.

```bash
nubi flows push flows/my_flow.yaml flows/other_flow.yaml
nubi flows push --dry-run
```

Requires an active login (`nubi login` or `NUBI_TOKEN`).

#### `nubi flows pull [--dir <dir>]`

Fetch all flows from the API and write them as YAML files (falls back to JSON if PyYAML is not installed). Defaults to the `flows/` directory.

```bash
nubi flows pull --dir flows/
# Wrote flows/my_flow.yaml
# Pulled 2 flow(s) to flows/.
```

Requires an active login.

---

### Secrets sub-commands

#### `nubi secrets set <name> <value> [--local-only]`

Store a secret in `~/.nubi/secrets` and, when logged in, also via the cloud API (`POST /secrets`). Use `--local-only` to write only to the local file. Local secrets are available to `nubi flows run` as `TaskContext.secrets` and via `{{ secrets.NAME }}` template interpolation in flow configs.

```bash
nubi secrets set MY_API_KEY secret123
nubi secrets set MY_API_KEY secret123 --local-only
```

Secrets can also be provided as `NUBI_SECRET_<NAME>` environment variables for `nubi flows run`.

#### `nubi secrets list [--local-only]`

List secret names from both local storage and the API. Values are never shown. Use `--local-only` to skip the API call.

```bash
nubi secrets list
# ┌──────────────┬────────────────┬──────┐
# │ Name         │ Stored locally │ API? │
# │ MY_API_KEY   │ yes            │ yes  │
# └──────────────┴────────────────┴──────┘
```

---

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
