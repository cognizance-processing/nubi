# Lakehouse — Datasets, Object Storage, and DuckDB

![Data flows from object storage through DuckDB httpfs into queries, dashboards, and flows](illustration:LakehouseFlow)

Nubi's lakehouse layer stores data as **Parquet files** in object storage (S3/MinIO, GCS, Azure Blob, or local filesystem) and queries them using **DuckDB's httpfs extension** — no separate data warehouse, no ETL pipeline, no extra drivers. Every dataset is immediately queryable through the same planner that powers dashboards and flows.

There are two ways to get a lake:

- **Bring your own bucket (BYO)** — wire up a DuckDB-over-S3 connector with your own bucket and credentials. Everything in this doc applies.
- **[Managed lakehouse](#managed-lakehouse)** — a Nubi-operated, per-org isolated storage area you provision with one click from the **Connectors** page. No bucket to create, no keys to rotate.

---

## How it fits together

1. **Ingest** — a CSV upload, SQL materialise, or flow output arrives via `POST /api/v1/datasets/upload` (multipart CSV) or `POST /api/v1/datasets/materialize` (SQL → Parquet).
2. **Convert** — DuckDB turns it into Parquet: `read_csv_auto` / `COPY … TO (FORMAT PARQUET)`.
3. **Store** — the Parquet lands in object storage: `file:///tmp/nubi-datasets/…` (local dev default) or `s3://bucket/datasets/<org>/<id>/data.parquet` (when `NUBI_BUCKET_URI` is set).
4. **Catalog** — a **datasets** row plus a **datastores** row (`connector_type=duckdb`, `view_sql`) are created.
5. **Query** — dashboards, queries, and flows read it with `SELECT * FROM read_parquet('s3://…')` or via the `CREATE VIEW dataset AS …` view.

Every dataset gets two catalog entries:

- A **datasets row** — stores metadata (name, `storage_uri`, `schema_json`, `source`, timestamps).
- A **datastores row** — a DuckDB connector whose `view_sql` is `CREATE VIEW dataset AS SELECT * FROM read_parquet('<parquet-path>')`. This makes the dataset immediately queryable through the normal connector path, including RLS enforcement.

---

## Managed lakehouse

![Connectors page, where the managed lakehouse is provisioned and managed](/docs/screenshots/connectors.png)

The managed lakehouse is a Nubi-operated, per-org **isolated storage area**: you provision it, use it, and delete it without ever touching buckets or credentials yourself. You choose to provision it explicitly, and usage feeds the metering surface — you pay only for the bytes you store.

It is managed from the **Connectors** page, where it appears as a card above the connectors list (it's a managed data source, not a separate page). The **Add connector** flow also offers **Use Nubi managed lakehouse** (recommended) as a storage choice alongside bringing your own bucket.

### Provisioning

On the Connectors page, click **Provision managed lakehouse**. An optional **"Seed demo data so I can explore right away"** checkbox exports the demo datasets into the lake as Parquet so you have something to query immediately. Provisioning requires write access in the org — read-only members see a note to ask an admin.

Provisioning is idempotent and does two things:

1. Creates a datastore named **Managed lakehouse** (`connector_type=duckdb`) whose storage path is **server-pinned** to your org's isolated prefix.
2. Stores the central storage credentials **encrypted** in the connector secret store, keyed by the managed datastore — they are never written to the datastore config and never returned by any API.

Once provisioned, the card shows **storage used**, **demo data** status (with a seed / re-seed action), and a **Browse lakehouse data** link that opens the data browser for the managed datastore.

### Deleting

**Disconnect** on the card (behind a confirm dialog) deprovisions the lake: every object under your org's prefix is deleted, then the managed datastore row and its stored credentials are removed. **This cannot be undone.**

The normal connector edit and delete routes refuse managed rows — the managed lakehouse can only be re-pointed or removed through its own endpoints, never by editing the connector config.

### API

All routes live under `/api/v1`; provisioning, seeding, and deleting require write access.

| Route | Description |
|---|---|
| `GET /lakehouse` | Status: `configured`, `provisioned`, `prefix`, `uri`, `datastore_id`, `demo_seeded`, `usage_bytes` / `usage_gb` (computed on demand). |
| `POST /lakehouse/provision` | Idempotently provision the managed lake. `?seed_demo=true` also seeds demo Parquet. |
| `POST /lakehouse/demo` | Seed (or re-seed) demo Parquet into the managed lake. Idempotent — existing tables are skipped. |
| `DELETE /lakehouse` | Deprovision: delete the org's objects and the managed datastore. Idempotent (204 even when nothing was provisioned). |

### Isolation and security model

Each org's managed lake is an **isolated key prefix** inside a central bucket:

```
s3://<central-bucket>/orgs/<org_id>/lake/
```

- **Server-pinned path** — the prefix is derived purely from the server-trusted org id, never from user input, and the datastore's config is re-pinned on every (idempotent) provision call. A user can never edit the config to point at another org's prefix or an arbitrary URL.
- **Credentials never exposed** — the central credentials live only in the connector secret store (encrypted at rest, scoped by org). They are never placed in `datastores.config` and never appear in any response.
- **Org-scoped everywhere** — all operations resolve the caller's own org; another org's managed lake is simply not found (deprovision across orgs is a no-op).
- Demo data lands under `orgs/<org_id>/lake/demo/<dataset>/<table>.parquet`.

### Querying the managed lake

The managed datastore is served by the same DuckDB-over-S3 connector (`duckdb_storage`) as a BYO lake, so it works in queries, dashboards, and flows like any other connector. Reference its Parquet directly:

```sql
SELECT *
FROM read_parquet('s3://<central-bucket>/orgs/<org_id>/lake/demo/ecommerce/orders.parquet')
LIMIT 100;
```

### When it's unavailable

The managed lakehouse needs **central storage** configured on the deployment (S3/MinIO credentials plus `NUBI_BUCKET_URI` / `NUBI_BUCKET_NAME`). When it isn't, `GET /lakehouse` returns `configured: false`, provision/demo return 409, and the Connectors card shows a subtle note — BYO connectors keep working regardless.

For OSS / local development you can point the managed lake at a local directory instead by setting `NUBI_MANAGED_LAKE_DIR` to an absolute path — no cloud bucket needed.

---

## Storage backends

The backend resolves which storage client to use at runtime, checking `NUBI_BUCKET_URI` first, then S3/MinIO env vars, then falling back to local filesystem.

| Scheme | Backend | Notes |
|---|---|---|
| `s3://` | `S3StorageClient` (boto3) | AWS S3, MinIO, Cloudflare R2, any S3-compatible |
| `gs://` | `GCSStorageClient` (google-cloud-storage) | Google Cloud Storage |
| `az://` | `AzureStorageClient` (azure-storage-blob) | Azure Blob Storage |
| `file://` | `LocalStorageClient` (stdlib only) | Local filesystem; dev/CI default |

**Storage layout** (local filesystem fallback, controlled by `NUBI_BUCKET_ROOT`):

```
/tmp/nubi-datasets/
└── datasets/
    └── <org_id>/
        └── <dataset_id>/
            └── data.parquet
```

For S3 the key mirrors this structure: `datasets/<org_id>/<dataset_id>/data.parquet`.

---

## Dataset operations

### Upload a CSV

`POST /api/v1/datasets/upload` — multipart form with `file` (CSV) and `name` fields.

The server:

1. Writes the CSV to a temp file.
2. Runs `COPY (SELECT * FROM read_csv_auto('<tmp>')) TO '<parquet-path>' (FORMAT PARQUET)` — DuckDB infers the schema automatically.
3. Uploads the Parquet to object storage if `NUBI_BUCKET_URI` is set.
4. Registers a datasets catalog row (`source = "upload"`).
5. Registers a datastores row with `view_sql = "CREATE VIEW dataset AS SELECT * FROM read_parquet('<path>')"`.
6. Links both rows via `datastore_id` so the dataset is queryable immediately.

```bash
curl -X POST https://your-nubi/api/v1/datasets/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@sales.csv" \
  -F "name=sales-2024"
```

Response includes `id`, `storage_uri`, `schema_json` (inferred column names and types), `source`, and `datastore_id`.

### Materialise a query

`POST /api/v1/datasets/materialize` — JSON body `{"sql": "SELECT …", "name": "…"}`.

The server runs the SQL through the **planner** (which validates SELECT-only and injects any active RLS predicates), then executes `COPY (<planned-sql>) TO '<parquet-path>' (FORMAT PARQUET)`. The output is registered identically to an upload (`source = "materialized"`).

When the SQL references `s3://` paths, httpfs is loaded and credentials are registered on the DuckDB connection before execution. A missing httpfs extension raises a clear error rather than a cryptic DuckDB crash.

```bash
curl -X POST https://your-nubi/api/v1/datasets/materialize \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT region, SUM(revenue) AS total FROM orders GROUP BY 1", "name": "revenue-by-region"}'
```

### List and fetch datasets

```
GET  /api/v1/datasets              → {"datasets": [...]}
GET  /api/v1/datasets/<dataset_id> → {dataset row}
```

Both endpoints are scoped to the caller's org. A request for an unknown or cross-org dataset returns 404.

---

## Dataset row schema

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Dataset identifier |
| `org_id` | UUID | Owning org |
| `name` | string | Human-readable name |
| `storage_uri` | string | `file:///…` or `s3://bucket/…` |
| `format` | string | Always `"parquet"` |
| `schema_json` | `[{name, type}]` | Columns inferred at creation time |
| `source` | `"upload"` \| `"materialized"` | How the dataset was created |
| `datastore_id` | UUID \| null | Linked datastores row; null until linked |
| `created_at` | ISO-8601 | |
| `updated_at` | ISO-8601 | |

---

## How DuckDB reads S3

DuckDB's **httpfs** extension gives it native `s3://` URI support. Nubi loads it on demand via `setup_s3_httpfs(conn, cfg)`, which executes:

```sql
INSTALL httpfs;
LOAD httpfs;

CREATE OR REPLACE SECRET nubi_s3 (
    TYPE S3,
    KEY_ID     '<key-id>',
    SECRET     '<secret>',
    REGION     '<region>',
    URL_STYLE  'path',            -- 'path' for MinIO; 'vhost' for AWS
    ENDPOINT   'host:port',       -- scheme-stripped; only set when endpoint is configured
    USE_SSL    false              -- false when endpoint scheme is http://
);
```

All three statements are idempotent (`CREATE OR REPLACE SECRET`). The secret is only registered when a key ID is resolvable — if no credentials are found, DuckDB's default credential chain is used (IAM role, env vars, etc.), allowing anonymous/public-bucket access.

**Credential resolution order** (first non-empty value wins):

1. Connector config keys: `s3_key_id` / `aws_access_key_id`, `s3_secret` / `aws_secret_access_key`, `s3_endpoint` / `endpoint_url`, `s3_region` / `aws_region`
2. Environment variables: `AWS_ACCESS_KEY_ID` → `S3_ACCESS_KEY`, `AWS_SECRET_ACCESS_KEY` → `S3_SECRET_KEY`, `S3_ENDPOINT_URL` → `AWS_ENDPOINT_URL`, `AWS_REGION` → `AWS_DEFAULT_REGION` → `S3_REGION`

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `NUBI_BUCKET_URI` | *(none)* | Full bucket URI, e.g. `s3://nubi`. When set, uploads go to this bucket instead of local filesystem. |
| `NUBI_BUCKET_ROOT` | `/tmp/nubi-datasets` | Local filesystem root for the `file://` fallback. |
| `NUBI_BUCKET_NAME` | `nubi` | Bucket name used when S3/MinIO env vars are present but `NUBI_BUCKET_URI` is not set. |
| `NUBI_MANAGED_LAKE_DIR` | *(none)* | Absolute local directory for the managed lakehouse on OSS / local dev — lets the managed lake work without any cloud bucket. Also read as `NUBI_LOCAL_LAKE_DIR`. |
| `AWS_ACCESS_KEY_ID` | *(none)* | S3 / MinIO access key ID. Also read as `S3_ACCESS_KEY`. |
| `AWS_SECRET_ACCESS_KEY` | *(none)* | S3 / MinIO secret access key. Also read as `S3_SECRET_KEY`. |
| `S3_ENDPOINT_URL` | *(none)* | Custom endpoint for MinIO or S3-compatible stores, e.g. `http://localhost:9000`. Also read as `AWS_ENDPOINT_URL`. |
| `AWS_REGION` | `us-east-1` | AWS/MinIO region. Also read as `AWS_DEFAULT_REGION` or `S3_REGION`. |
| `S3_URL_STYLE` | `path` when endpoint set, else `vhost` | `"path"` for MinIO/self-hosted; `"vhost"` for AWS S3. |
| `NUBI_DUCKDB_MEMORY_LIMIT` | *(DuckDB default)* | Per-connection memory cap for query execution, e.g. `2GB`. One tenant's heavy scan cannot OOM the process. |
| `NUBI_DUCKDB_THREADS` | *(DuckDB default)* | Per-connection thread cap. |
| `NUBI_DUCKDB_TEMP_DIR` | *(DuckDB default)* | Spill directory for larger-than-memory queries (give Fly machines a volume and point this at it). |

---

## Local development with MinIO

### Docker Compose (recommended)

```bash
cp .env.compose .env.compose.local   # already has safe MinIO defaults
make up                               # or: docker compose up -d
```

The compose stack starts MinIO on port 9000 (S3 API) and 9001 (web console), and a one-shot `minio-init` service that creates the `nubi` bucket on first boot.

### Standalone script

```bash
./scripts/minio-dev.sh          # start MinIO + create 'nubi' bucket
./scripts/minio-dev.sh stop
```

The script prints the env vars to export:

```bash
export AWS_ACCESS_KEY_ID="minioadmin"
export AWS_SECRET_ACCESS_KEY="minioadmin"
export S3_ENDPOINT_URL="http://localhost:9000"
export AWS_REGION="us-east-1"
export NUBI_BUCKET_URI="s3://nubi"
```

Then start the backend:

```bash
cd backend && uvicorn main:app --reload
```

MinIO's web console is available at **http://localhost:9001**.

---

## Querying datasets

Because each uploaded or materialised dataset is registered as a `duckdb` datastore with a `view_sql`, it appears in the data browser and can be used in any query, dashboard, or flow — no special handling needed.

You can also reference Parquet files directly with `read_parquet()`:

```sql
-- Read a dataset from object storage
SELECT *
FROM read_parquet('s3://nubi/datasets/<org_id>/<dataset_id>/data.parquet')
LIMIT 100;

-- Glob over a prefix (multiple files)
SELECT region, SUM(revenue) AS total
FROM read_parquet('s3://nubi/raw/orders/*.parquet')
GROUP BY region;
```

### Writing query results as Parquet

Use DuckDB's `COPY … TO` syntax (works in both browser kernel and server kernel):

```sql
COPY (
  SELECT region, SUM(revenue) AS total_revenue
  FROM read_parquet('s3://nubi/raw/orders/*.parquet')
  GROUP BY region
) TO 's3://nubi/agg/revenue_by_region.parquet'
(FORMAT 'parquet', CODEC 'zstd');
```

Or use the materialise API to register the result as a named dataset (see above).

---

## Browsing data: the Data explorer

![Data explorer browsing a connector's tables](/docs/screenshots/data-explorer.png)

The **Data explorer** (route `/data`) is a Supabase-style browser over your connectors — the quickest way to eyeball lake data without writing SQL:

- **Left rail** — a connector picker (your org's connectors plus the built-in **Demo** entry) and a searchable table list. On mobile the rail collapses into a dropdown.
- **Data tab** — a paginated grid of the first 500 rows, fetched as Arrow IPC, with a row count in the header and an export action.
- **Schema tab** — column name, type, nullable, and a primary-key badge per column.
- **Refresh** — re-fetches both rows and schema for the selected table.

The managed lakehouse's datastore is a system row, so it doesn't appear in the raw connector list; browse it via **Connectors → Browse lakehouse data**, which opens the data browser for the managed datastore.

---

## RLS and security

Datasets are org-scoped. The `datasets` catalog enforces `org_id` on every read, list, and write. The `datastores` row created for each dataset is also org-scoped.

When querying through the normal connector path, RLS predicates are injected into the SQL by the planner **before** the connector executes it. The connector receives `plan.sql` verbatim and never touches RLS logic — this is verified by the conformance suite (test A6 in `test_duckdb_storage.py`).

All dataset endpoints require a valid first-party Bearer token.

### Engine-layer tenant isolation (secret SCOPE)

RLS is not the only wall. When a dataset's Parquet lives in object storage, the registered datastore config carries `s3_scope` = the org's prefix (`s3://<bucket>/datasets/<org_id>/`), and the DuckDB S3 secret is bound to that prefix with a `SCOPE` clause:

```sql
CREATE OR REPLACE SECRET nubi_s3 (
    TYPE S3,
    KEY_ID '…', SECRET '…',
    SCOPE 's3://nubi-data/datasets/<org_id>/'
);
```

A query through that datastore has **no credentials** for paths outside the scope — even SQL that names another org's object verbatim gets a permission error from DuckDB, independent of RLS and path validation.

### Connection hardening

Tenant SQL runs verbatim on the DuckDB connection, so every per-query connection is hardened before execution (`harden_connection` in `duckdb_conn.py`):

- **Cloud (s3-only) connections** — `disabled_filesystems='LocalFileSystem'`: queries can never touch the host filesystem.
- **Read-only local file sources** — `enable_external_access=false`: no file, URL, or extension access at query time.
- **All connections** — extension autoinstall/autoload disabled, optional memory/thread/spill limits (see environment variables), then `lock_configuration=true` freezes all of the above for the connection's lifetime.

Note: blocking the local filesystem also disables disk spill on that connection, so a larger-than-memory query fails rather than spilling. Size `NUBI_DUCKDB_MEMORY_LIMIT` accordingly.

---

## Storage usage metering

![Storage usage under Settings → Usage](/docs/screenshots/settings-usage.png)

Nubi meters lakehouse storage as `kind="storage"` usage events (units = GB):

- **Datasets** — after each upload or materialise operation the server takes a best-effort snapshot of the org's total dataset storage.
- **Managed lakehouse** — `GET /lakehouse` (and provision / seed) sums the bytes under the org's prefix on demand and records a snapshot, so usage reflects the managed lake without walking the bucket on every request.

Aggregation takes the **peak (max) GB** over the period. Metering is best-effort — a metering failure never blocks an upload or a status read. Storage usage is visible under **Settings → Usage**.

Usage metering itself is open-core. **Billing** on top of it (plans, invoices, payments) is EE/Cloud — see [Billing and Usage](/docs/billing-and-usage).

---

## Production checklist

- Replace `minioadmin` / `minioadmin` with strong random credentials before exposing any endpoint.
- Enable TLS on the MinIO endpoint (use `https://` in `S3_ENDPOINT_URL`; DuckDB's `USE_SSL` is inferred automatically).
- Set `S3_URL_STYLE` appropriately: `path` for MinIO/self-hosted, `vhost` for AWS S3.
- Store credentials in a secrets manager or Nubi's named-secrets store (`nubi secrets set`). See [Secrets](/docs/secrets).
- Configure a lifecycle policy on the bucket to expire temporary upload objects.
- Back up the `datasets` Postgres table alongside your object storage — losing either one orphans the other.

## Related docs

- [Connectors](/docs/connectors) — how datastores and connector configs work
- [Queries and Params](/docs/queries-and-params) — SQL planner, RLS, params
- [Flows](/docs/flows) — automating materialise operations
- [Secrets](/docs/secrets) — storing S3 credentials securely
- [Self-host](/docs/self-host) — deploying Nubi with Postgres and object storage
- [Billing and Usage](/docs/billing-and-usage) — how metered storage feeds plans and billing (EE/Cloud)
