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
          │  SET s3_*          │
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

The DuckDB connector reads these variables from the process environment.  Set
them in `.env.compose.local`, your shell, or a secrets manager.

| Variable | Required | Default | Description |
|---|---|---|---|
| `S3_ENDPOINT_URL` | yes (non-AWS) | *(none)* | Full URL of the S3-compatible endpoint, e.g. `http://localhost:9000` for local MinIO or `https://s3.amazonaws.com` for AWS. |
| `S3_ACCESS_KEY` | yes | *(none)* | Access key ID (MinIO root user, AWS Access Key ID, etc.). |
| `S3_SECRET_KEY` | yes | *(none)* | Secret access key. |
| `S3_REGION` | no | `us-east-1` | AWS/MinIO region.  For MinIO the value is arbitrary but must be set. |
| `S3_BUCKET` | no | `nubi` | Default bucket used by the seed bundle and CSV-upload pipeline. |
| `S3_FORCE_PATH_STYLE` | no | `true` | Must be `true` for MinIO and most self-hosted S3 clones.  Set to `false` only for real AWS S3 virtual-hosted style. |

### Mapping to DuckDB httpfs `SET` statements

```sql
-- DuckDBConnector sets these before running any user query:
SET s3_endpoint      = '<host:port from S3_ENDPOINT_URL>';
SET s3_access_key_id = '<S3_ACCESS_KEY>';
SET s3_secret_access_key = '<S3_SECRET_KEY>';
SET s3_region        = '<S3_REGION>';
SET s3_url_style     = 'path';   -- when S3_FORCE_PATH_STYLE=true
SET s3_use_ssl       = 'false';  -- when endpoint is http://
```

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

1. Frontend uploads CSV to `POST /data/{datastore_id}/upload` (multipart).
2. Backend writes the file to `s3://nubi/uploads/<uuid>/<filename>.csv`.
3. A dataset row is inserted in the `datastores` table with
   `connector_type='duckdb'` and the `database` field pointing at the
   Parquet copy produced by `COPY ... TO 's3://nubi/datasets/<uuid>/'`.
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
