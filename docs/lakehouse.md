# Lakehouse — Object Storage + DuckDB httpfs

Nubi's lakehouse layer lets every DuckDB connector read **and write** Parquet
files stored in S3-compatible object storage.  In local development MinIO
simulates S3; in production point the same env vars at a real bucket (AWS S3,
GCS HMAC, Cloudflare R2, etc.).

---

## Architecture overview

```
┌────────────────────────────────────────┐
│  Query / Dashboard / Flow              │
│  (SQL via planner.py + sqlglot/jinja2) │
└───────────────────┬────────────────────┘
                    │
          ┌─────────▼──────────┐
          │  DuckDBConnector   │
          │  (connectors/)     │
          │                    │
          │  INSTALL httpfs;   │
          │  LOAD httpfs;      │
          │  CREATE SECRET ... │
          └─────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │  Object Storage     │
         │  MinIO  /  S3       │
         │  bucket: nubi       │
         └─────────────────────┘
```

DuckDB's built-in **httpfs** extension lets any SQL statement reference
`s3://nubi/path/to/file.parquet` directly — no extra drivers or ETL processes.

---

## Environment variables

The backend reads these variables from the process environment.  Set them in
`.env.compose.local`, your shell, or a secrets manager.  Both the `S3_*` family
(used by the Docker Compose defaults) and the `AWS_*` family (boto3/standard)
are accepted; `AWS_*` takes precedence when both are set.

| Variable | Required | Default | Description |
|---|---|---|---|
| `S3_ENDPOINT_URL` | yes (non-AWS) | *(none)* | Full URL of the S3-compatible endpoint, e.g. `http://localhost:9000` for local MinIO. Also read as `AWS_ENDPOINT_URL`. |
| `S3_ACCESS_KEY` | yes | *(none)* | Access key ID (MinIO root user, etc.). Also read as `AWS_ACCESS_KEY_ID`. |
| `S3_SECRET_KEY` | yes | *(none)* | Secret access key. Also read as `AWS_SECRET_ACCESS_KEY`. |
| `S3_REGION` | no | `us-east-1` | AWS/MinIO region. Also read as `AWS_REGION` / `AWS_DEFAULT_REGION`. |
| `S3_BUCKET` | no | `nubi` | Default bucket name used by the seed bundle and CSV-upload pipeline. Also read as `NUBI_BUCKET_NAME`. |
| `S3_FORCE_PATH_STYLE` | no | `true` | Must be `true` for MinIO and most self-hosted S3 clones. Set to `false` only for real AWS S3 virtual-hosted style. |
| `NUBI_BUCKET_URI` | no | *(none)* | Full bucket URI, e.g. `s3://nubi`. When set, uploaded datasets are written to this bucket instead of the local filesystem. |
| `NUBI_BUCKET_ROOT` | no | `/tmp/nubi-datasets` | Local filesystem root for the file:// storage fallback (used when `NUBI_BUCKET_URI` is not set). |

### How Nubi configures DuckDB for S3

Nubi uses DuckDB's modern **secrets API** (not the legacy `SET s3_*` variables).
When a query or dataset operation needs S3 access, the backend calls
`setup_s3_httpfs(conn, cfg)`, which executes:

```sql
-- 1. Install and load the httpfs extension (idempotent):
INSTALL httpfs;
LOAD httpfs;

-- 2. Register a named S3 secret (idempotent, replaces any previous secret):
CREATE OR REPLACE SECRET nubi_s3 (
    TYPE S3,
    KEY_ID     '<resolved key id>',
    SECRET     '<resolved secret>',
    REGION     '<S3_REGION>',
    URL_STYLE  'path',          -- for MinIO / S3-compatible endpoints
    ENDPOINT   '<host:port>',   -- stripped of scheme (DuckDB expects host:port only)
    USE_SSL    false            -- false when endpoint scheme is http://
);
```

Credentials are resolved in order: connector config keys (`s3_key_id`,
`aws_access_key_id`) → `AWS_ACCESS_KEY_ID` env var → `S3_ACCESS_KEY` env var.
The secret is only registered when at least a key ID is resolvable; anonymous
(public bucket) access falls back to DuckDB's default credential chain.

---

## Local development with MinIO

### Option A — Docker Compose (full stack)

```bash
cp .env.compose .env.compose.local   # already has safe MinIO defaults
make up                               # or: docker compose up -d
```

Services started:

| Service | Port | Purpose |
|---|---|---|
| `minio` | 9000 | S3 API |
| `minio` | 9001 | MinIO web console |
| `minio-init` | *(one-shot)* | Creates the `nubi` bucket on first boot |

The backend service automatically receives `S3_ENDPOINT_URL`,
`S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, `S3_BUCKET`, and
`S3_FORCE_PATH_STYLE` via `docker-compose.yml` environment overrides.

### Option B — Standalone script (no full stack)

```bash
./scripts/minio-dev.sh          # start MinIO + create 'nubi' bucket
./scripts/minio-dev.sh stop     # tear it down
./scripts/minio-dev.sh logs     # tail logs
```

The script prints the exact `export` lines to paste into your shell:

```
export S3_ENDPOINT_URL="http://localhost:9000"
export S3_ACCESS_KEY="minioadmin"
export S3_SECRET_KEY="minioadmin"
export S3_REGION="us-east-1"
export S3_BUCKET="nubi"
export S3_FORCE_PATH_STYLE="true"
```

Then start the backend normally:

```bash
cd backend && uvicorn main:app --reload
```

---

## Path-style vs virtual-hosted-style URLs

MinIO (and most self-hosted S3 clones) require **path-style** URLs:

```
http://localhost:9000/nubi/prefix/file.parquet   # path-style  ✓ MinIO
http://nubi.localhost:9000/prefix/file.parquet   # vhost-style ✗ MinIO
```

AWS S3 defaults to virtual-hosted style; set `S3_FORCE_PATH_STYLE=false`
(and remove `S3_ENDPOINT_URL`) when targeting real AWS.

---

## Typical lakehouse SQL patterns

### Read a Parquet file from the bucket

```sql
SELECT * FROM read_parquet('s3://nubi/datasets/sales/2024/*.parquet')
LIMIT 100;
```

### Write a query result as Parquet

```sql
COPY (
  SELECT region, SUM(revenue) AS total_revenue
  FROM read_parquet('s3://nubi/raw/orders/*.parquet')
  GROUP BY region
) TO 's3://nubi/agg/revenue_by_region.parquet'
(FORMAT 'parquet', CODEC 'zstd');
```

### CSV upload → bucket → registered dataset (pipeline)

1. Frontend uploads CSV to `POST /api/v1/datasets/upload` (multipart `file` +
   `name` form fields).
2. Backend saves the CSV to a temp file, then converts it to Parquet via
   `COPY (SELECT * FROM read_csv_auto(...)) TO '<path>' (FORMAT PARQUET)`.
   The Parquet lands at `<bucket-root>/datasets/<org_id>/<dataset_id>/data.parquet`
   (local) or `s3://nubi/datasets/<org_id>/<dataset_id>/data.parquet` (when
   `NUBI_BUCKET_URI` is set).
3. A datastore row is created with `connector_type='duckdb'` and a `view_sql`
   of `CREATE VIEW dataset AS SELECT * FROM read_parquet('<parquet-path>')`;
   the `parquet_path` config key holds the effective URI (local or `s3://`).
4. The dataset is immediately queryable via the normal query pipeline.

### Using outputs as inputs in dashboards

Because DuckDB can read directly from S3 paths, a dashboard tile can
reference the output of another query/flow:

```sql
-- Dashboard tile: "Revenue this quarter"
SELECT *
FROM read_parquet('s3://nubi/agg/revenue_by_region.parquet')
ORDER BY total_revenue DESC;
```

---

## Production checklist

- [ ] Replace `minioadmin` / `minioadmin` with strong random credentials.
- [ ] Enable TLS on the MinIO endpoint (set `S3_USE_SSL=true` / use `https://`).
- [ ] Set `S3_FORCE_PATH_STYLE=false` if switching to real AWS S3 virtual-hosted style.
- [ ] Store credentials in the Nubi named-secrets store (`NUBI_SECRETS_KEY` must be set).
- [ ] Configure a lifecycle policy on the bucket to expire temporary upload objects.
- [ ] For multi-node MinIO (distributed mode) set `MINIO_VOLUMES` appropriately and add extra drive mappings to the compose service.

---

## MinIO console

When running locally, the MinIO web console is available at
**http://localhost:9001** (user: `minioadmin`, password: `minioadmin`).
Use it to browse buckets, inspect objects, set lifecycle policies, and
monitor throughput.
