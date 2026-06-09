# SDK & CLI

---

## JavaScript SDK — `@nubi/sdk`

Framework-agnostic ESM package. Wraps authentication, Arrow query execution, REST CRUD for all four domain resources, and embed mounting — no React required.

### Installation

```bash
npm install @nubi/sdk
```

`apache-arrow` is bundled inside the SDK; no separate Arrow install is needed.

### Setup

```js
import { createNubiClient } from '@nubi/sdk'

const client = createNubiClient({
  baseUrl: 'https://api.example.com',
  getToken: async () => myGetJwt(),
})
```

`getToken` is called before every authenticated request. Pass an async function that silently mints or refreshes your JWT, or a static string for dev.

### Auth

```js
const { user } = await client.auth.me()
// { id, email, name, avatar_url, email_verified, created_at }
```

### Queries

```js
// Inline SQL with positional params
const table = await client.query(
  'SELECT * FROM sales WHERE region = $1',
  { params: { '1': 'EMEA' } }
)

// Registered query id
const table = await client.query('revenue_by_month')

console.log(table.numRows)                     // Apache Arrow Table
console.log(table.getChild('amount').get(0))   // columnar access
```

Returns an Apache Arrow `Table`. The backend responds with `Content-Type: application/vnd.apache.arrow.stream`.

### Resources — CRUD

Four resources: **datastores**, **boards**, **widgets**, **queries**. Each has `list`, `get`, `create`, `update`, `remove`:

```js
// List
const boards = await client.resources.boards.list()

// Get by id
const board = await client.resources.boards.get('board-uuid')

// Create
const newBoard = await client.resources.boards.create({
  name: 'Q1 Dashboard',
  config: { spec: { version: 1, title: 'Q1 Dashboard', layout: {}, widgets: [] } },
})

// Update (partial)
const updated = await client.resources.boards.update('board-uuid', {
  name: 'Q1 Dashboard v2',
})

// Delete (returns null; backend returns 204)
await client.resources.boards.remove('board-uuid')
```

All resources share the shape `{ id, org_id, created_by, name, config, created_at, updated_at }`.

Errors throw with `.code` (error code string), `.message`, and `.status` (HTTP status).

### embed.mount()

```js
const { unmount } = client.embed.mount(
  document.getElementById('dashboard-root'),
  { query: 'revenue_by_month' }
)

unmount()  // tear down later
```

Options accepted by `mount()`:

| Option | Type | Description |
|--------|------|-------------|
| `query` | string | SQL string or registered query id |
| `token` | string | Static JWT. Omit to let the SDK wire `get-token` automatically |
| `backend` | string | Override the backend URL; defaults to the `baseUrl` used when creating the client |

Prerequisite: load the `nubi-dashboard` bundle on the host page so the custom element is registered.

---

## CLI — `nubi`

A Python CLI for dashboards-as-code workflows, flow management, and secrets.

### Installation

```bash
cd cli
pip install -r requirements.txt
pip install -e .   # registers the `nubi` console script
```

Install the optional `pyarrow` extra for precise row counts in `nubi run`:

```bash
pip install -e ".[arrow]"
```

### Configuration

| Source | Variable | Default |
|--------|----------|---------|
| Env var | `NUBI_API_URL` | `http://localhost:8000/api/v1` |
| Env var | `NUBI_TOKEN` | — |
| File | `~/.nubi/credentials` | — |

`nubi login` writes the token to `~/.nubi/credentials`.

### Commands

**`nubi login`** — authenticate and save the access token locally.

```bash
nubi login
# Email: you@example.com
# Password: ••••••
```

**`nubi deploy <dir> [--dry-run]`** — push all `*.json` resource files in `<dir>` to the API.

```json
{ "resource": "boards", "name": "My Dashboard", "config": {} }
```

If an `"id"` key is present the resource is updated; otherwise it is created.

```bash
nubi deploy ./dashboards --dry-run   # preview
nubi deploy ./dashboards             # live
```

**`nubi run <query_id>`** — execute a registered query and print the row count. Requires `pyarrow` for a precise row count; without it the raw byte length of the Arrow response is shown instead.

```bash
nubi run 3fa85f64-5717-4562-b3fc-2c963f66afa6
# Query '3fa85f64-...' returned 1,234 row(s).
```

**`nubi diff <dir>`** — compare local files against the server (read-only). Resources without an `"id"` are reported as NEW.

```bash
nubi diff ./dashboards
# board.json (id=board-123)
#   - name: 'Old Name'
#   + name: 'New Name'
```

**`nubi pull <resource> <dir>`** — download all server resources of a type to local JSON files.

```bash
nubi pull boards ./downloaded/boards
# Wrote ./downloaded/boards/board-123.json
# Pulled 3 boards.
```

Valid resource types: `datastores`, `boards`, `widgets`, `queries`.

### Flows sub-commands

**`nubi flows run <file> [--param key=value ...]`** — execute a flow locally end-to-end using an in-memory store and file-based storage. Reads a YAML or JSON flow spec, resolves parameters from `--param` flags and the local secrets file (`~/.nubi/secrets`), then drives the flows runtime to completion. Prints each task's state and result on completion.

The backend package must be importable — run from a nubi checkout with the `backend/` directory present.

```bash
nubi flows run flows/my_flow.yaml --param region=us --param date=2024-01-01
```

**`nubi flows push [file ...] [--dry-run]`** — create or update flows in the cloud from local YAML/JSON files. Matches by flow name: an existing flow with the same name is updated; otherwise a new flow is created. If no files are specified, all `*.yaml`/`*.json` files in the current directory are pushed.

```bash
nubi flows push flows/my_flow.yaml flows/other_flow.yaml
nubi flows push --dry-run
```

**`nubi flows pull [--dir <dir>]`** — fetch all flows from the API and write them as YAML files (falls back to JSON if PyYAML is unavailable). Defaults to the `flows/` directory.

```bash
nubi flows pull --dir flows/
# Wrote flows/my_flow.yaml
# Pulled 2 flow(s) to flows/.
```

### Secrets sub-commands

**`nubi secrets set <name> <value> [--local-only]`** — store a secret in `~/.nubi/secrets` and, when logged in, via the cloud API. Use `--local-only` to skip the API call. Local secrets are used by `nubi flows run` to populate `TaskContext.secrets` and to resolve `{{ secrets.NAME }}` templates in flow configs. Secrets can also be provided as `NUBI_SECRET_<NAME>` environment variables.

```bash
nubi secrets set MY_API_KEY secret123
nubi secrets set MY_API_KEY secret123 --local-only
```

**`nubi secrets list [--local-only]`** — list secret names (values are never shown) from both local storage and the API. Use `--local-only` to skip the API.

```bash
nubi secrets list
```

### Running CLI Tests

```bash
cd cli && python -m pytest tests -q
```

---

## Building the SDK from Source

```bash
cd sdk
npm install
npm run build    # dist/nubi-sdk.js (ESM) + dist/nubi-sdk.umd.cjs (UMD)
npm test         # node --test src/index.test.mjs
```

`apache-arrow` is bundled into the output; no peer dependencies required.

---

## API Architecture Notes

The Nubi REST API is served at `/api/v1/` from the FastAPI backend. All endpoints:

- Require a valid Bearer token (HS256 first-party tokens minted by the backend, or RS256/ES256 embed tokens from registered issuers).
- Are org-scoped — resources belong to an org; cross-org access returns 404 (not 403, to avoid information leakage).
- Return Arrow IPC for query data (`Content-Type: application/vnd.apache.arrow.stream`); JSON for everything else.

FastAPI's `/docs` endpoint (Swagger UI) is disabled in `ENV=production`. In development it provides a full interactive API explorer at `http://localhost:8000/docs`.
