# Core resources

> Part of the [API reference](/docs/api-reference) — see it for conventions, error codes, and the audit log.

## Projects

A project groups a workspace's connectors, queries, dashboards, and flows and
is the unit of files-as-code export/import. All routes are org-scoped.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/projects` | Member | List projects for the caller's org. |
| `POST` | `/projects` | Writer | Create a project. **`201`.** |
| `GET` | `/projects/{id}` | Member | Fetch one project. |
| `PATCH` | `/projects/{id}` | Writer | Partial update (name, settings). |
| `PUT` | `/projects/{id}` | Writer | Replace project fields. |
| `DELETE` | `/projects/{id}` | Writer | Delete a project. **`204`.** |
| `GET` | `/projects/{id}/deletion-impact` | Member | Preview what a delete would cascade. |
| `GET` | `/projects/{id}/export` | Member | Export the whole project as a files-as-code bundle. |
| `POST` | `/projects/{id}/import` | Writer | Bulk-upsert a project bundle; returns a per-resource result. |

`GET /projects/{id}/export` and `POST /projects/{id}/import` are the one
round-trip the CLI's [`nubi pull` / `nubi push`](/docs/sdk-and-cli) use when a
project is bound. The bundle format is specified in
[Files-as-Code](/docs/files-as-code).

---

## Connectors

Manage data source connections. Secrets (`password`, `token`, `api_key`, …) are
AES-256-GCM encrypted at rest and **never** returned by any read — connector
credentials do not round-trip. See [Connector security](/docs/connector-security).

### `POST /connectors`

Create a connector. **Auth:** Writer. **`201`.**

**Body (`CreateConnectorIn`):**
```json
{
  "name": "prod-postgres",
  "type": "postgres",
  "config": { "host": "db.example.com", "port": 5432, "database": "app", "user": "readonly", "sslmode": "require" },
  "secret": { "password": "s3cr3t" },
  "seed": "blank"
}
```

- `config` holds **non-secret** connection params (extra fields allowed, e.g. a
  `base_url` for HTTP sources, `network_mode`, `bridge_id`). Putting a secret
  key in `config` is rejected.
- `secret` holds sensitive fields only: `password`, `service_account_json`,
  `token`, `api_key`, `access_token`, `aws_secret_access_key`, `private_key`.
- `seed`: `"demo"` seeds a read-only copy of the demo parquet dataset;
  `"blank"` (default) creates an empty connector.

### `GET /connectors`

List connectors for the org. Secret fields come back blank.

### `GET /connectors/{id}`

Fetch one connector (no secrets).

### `PUT /connectors/{id}`

Update `name`, `config`, and/or `secret` (all optional). **Auth:** Writer.

### `DELETE /connectors/{id}`

Delete a connector. **Auth:** Writer. **`204`.**

### `POST /connectors/{id}/test`

Validate config + secret resolvability against the live source. Backs
`nubi connectors test`.

---

## Queries

Ad-hoc SQL and the server-side query registry. Query results stream Apache
Arrow IPC — see [Notes on Arrow IPC responses](/docs/api-reference#notes-on-arrow-ipc-responses).

### `POST /query`

Execute a query and stream the result as an Arrow IPC stream.

**Auth:** Any valid token. First-party callers may send raw `sql` (requires the
`author:sql` scope); **embed tokens must send `query_id`** — raw SQL is rejected
on the embed path.

**Body (`QueryIn`):**
```json
{
  "sql": "SELECT region, SUM(amount) AS revenue FROM orders WHERE region = $1 GROUP BY region",
  "query_id": null,
  "params": ["EMEA"],
  "named_params": null,
  "datastore_id": null
}
```

| Field | Description |
|---|---|
| `sql` | SELECT statement. Non-SELECT is rejected. Ignored for embed tokens and when `query_id` is set. |
| `query_id` | Id of a registered query. Required for embed tokens. |
| `params` | Positional params bound to `$1`, `$2`, … (index 0 → `$1`). |
| `named_params` | Named param values, resolved against a registered query's declared params. Token/RLS claim names are locked and win over `named_params`. |
| `datastore_id` | Optional connector to route to; otherwise the built-in DuckDB demo dataset. |

**Response `200`:** `Content-Type: application/vnd.apache.arrow.stream`. Header
`X-Nubi-Cache: HIT | MISS`. RLS policies come exclusively from the verified
token — any `policies` in the request body is ignored.

### `POST /query/estimate`

Run the identical auth/scope/allowlist/RLS resolution as `/query` but call the
connector's `estimate()` instead of executing — returns a cost/row estimate
without scanning data.

### `GET /query/registry`

List registered queries (ids, declared params, output schemas).

### `POST /query/registry`

Register a query in the server-side registry. **`201`.** Registered queries are
what embed tokens and `client.query('<id>')` reference by id.

> The generic CRUD in [Resources](#resources-generic-crud) also manages queries as a
> resource kind (with `.sql` + metadata) — use that for board-authoring
> workflows and the registry endpoints for the execution registry.

---

## Resources (generic CRUD)

A uniform CRUD surface over four resource kinds: **`datastores`**, **`boards`**,
**`queries`**, **`widgets`**. This is what `@nubi/sdk`'s
`client.resources.*` wraps.

**Auth:** First-party Bearer token. Writes require the Writer role. Unknown
resource names return **404**.

| Method | Path | Description |
|---|---|---|
| `GET` | `/{resource}` | List all resources of that kind for the org. |
| `POST` | `/{resource}` | Create. **`201`.** Writer. |
| `GET` | `/{resource}/{id}` | Fetch one. Supports `?env=<key>` to resolve an environment override (adds `resolved_version`). |
| `PUT` | `/{resource}/{id}` | Update fields. Writer. |
| `DELETE` | `/{resource}/{id}` | Delete. **`204`.** Writer. |

All rows share the shape `{ id, org_id, project_id, created_by, name, config, created_at, updated_at }`. A `queries` row whose `config` carries a `metric` block is validated as a governed [metric](/docs/api-analytics#metrics-semantic-layer) on write.

---

## Boards & dashboards

Boards store a `DashboardSpec` in `config.spec` (CRUD via [Resources](#resources-generic-crud)
with `resource = boards`). Additional board-specific endpoints:

### `POST /dashboards/validate`

Validate a `DashboardSpec` and return structured, repair-oriented issues
(chart encodings, filter/text requirements, var refs, tab refs, query-id
registry lookups). Useful for grounding an AI author before saving.

**Body:** `{ "spec": { ... } }`
**Response `200`:** `{ "valid": true, "issues": [] }`

### `POST /boards/{board_id}/providers/{provider_id}/data`

Resolve a `DataProvider` declared in a board's spec and return its named
result-sets as a multi-table Arrow IPC frame. See
[DataProvider boards](/docs/api-analytics#dataprovider-boards) below for the framing and cache-key
details.

---

## Export & share

Board export and the embed-share descriptor. **Auth:** first-party Bearer token.

| Method | Path | Description |
|---|---|---|
| `GET` | `/boards/{board_id}/export.json` | Export a board's spec + data as JSON. |
| `GET` | `/boards/{board_id}/export.csv` | Export the board's data as CSV. |
| `GET` | `/boards/{board_id}/export.pdf` | Render the board to PDF. |
| `POST` | `/boards/{board_id}/share` | Return the embed descriptor: embed URL, a ready-to-paste `<nubi-dashboard>` snippet, the exact claim shape the host must RS256/ES256-sign, and the RLS policy summary. |

`POST /boards/{board_id}/share` never mints a token — **Nubi does not sign embed
JWTs**; the host signs them with its own key. See [Embedding](/docs/embedding).

---

## Embedding

Embedded-dashboard support. The trust boundary and RLS model are in
[Embedding](/docs/embedding); token verification is covered under
[Scope & access grants](/docs/api-auth#scope-access-grants).

### `GET /embed/config/{dashboard_id}`

Read-only descriptor the host page fetches at runtime to render an embedded
board (spec, provider ids, embed conventions). Auth is the host-signed embed
JWT.

### `POST /embed/embed-token`

**Development only.** Mints a backend-verified HS256 embed token for local
testing. **Disabled by default** — returns `503` unless
`EMBED_DEV_TOKEN_ENABLED=true`. Never enable in production; real embed tokens
are signed by the host's own key.

**Response `200`:** `{ "token": "<jwt>", "expires_in": <seconds> }`

---

## Scheduled jobs

Scheduled exports and recurring jobs (distinct from [Flows](/docs/api-flows#flows), which are
DAGs). **Auth:** Writer for writes.

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Create a job. **`201`.** |
| `GET` | `/jobs` | List jobs for the org. |
| `GET` | `/jobs/{id}` | Fetch one job. |
| `DELETE` | `/jobs/{id}` | Delete a job. **`204`.** |
| `POST` | `/jobs/{id}/run` | Trigger a run now. |
| `GET` | `/jobs/{id}/runs` | List a job's runs. |

---
