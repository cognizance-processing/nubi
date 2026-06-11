# Multi-Source Ingestion + Bridge v2 — Implementation Design

Status: direction agreed, scope pinned below.
Audience: backend-first; CLI + small UI surface in later phases.
Constraint: additive only. Existing connectors, flows, and bridge tunnel keep
working unchanged; no public route shape changes.

This doc covers ingestion from FTP / SFTP / object storage (incl. zip archives) /
customer APIs, into **any connector target** (managed lakehouse, BigQuery,
Postgres, Snowflake, …), including sources reachable only through a bridge agent
on a customer machine — with a credential model where long-lived secrets never
leave the control plane.

---

## 1. Core decisions (settled)

1. **Everything is a connector.** A connector is the unit of
   "external system + credentials + network path". FTP/SFTP/buckets/APIs are
   connectors so they inherit encrypted secret storage
   (`connector_secrets`, AES-256-GCM), the Connectors UI, org scoping, and
   `network_mode` bridge routing for free.
2. **Capability split, not type split.** Connectors expose either (or both):
   - the existing query interface `execute(plan) → Arrow` (SQL-queryable), or
   - a new **file interface** (`list_files`, `open`, optional `move`/`delete`).
   The registry stays one registry; `capabilities()` grows flags. FTP/SFTP are
   file-only; `duckdb_storage` proves a bucket can be both. Non-queryable
   sources become queryable **by landing in a queryable target**.
3. **Targets are any connector, uniformly.** Ingestion is
   `source connector → staging → target connector`. The lakehouse is just one
   target (object-storage class), not a special case.
4. **Stage-then-promote is the only path to database/warehouse targets in v1.**
   Long-lived connector secrets never leave the control plane; remote agents
   only ever receive ephemeral, task-scoped, write-only grants to a staging
   prefix. Central workers do all loads into Tier-2 targets.
5. **API ingestion is Python code**, not a declarative pagination DSL. The
   existing `python` task kind does the pulling; we add watermark advance, a
   pinned staging/lake writer, and starter templates.
6. **Staging is a dedicated bucket in managed cloud**, a same-bucket `staging/`
   prefix fallback for self-host/dev. Same code path, different posture.

---

## 2. File connectors

New base alongside the query interface (`backend/app/connectors/base.py`):

```python
class FileConnectorMixin:
    def list_files(self, pattern: str, since: datetime | None) -> list[FileStat]: ...
    def open(self, path: str) -> BinaryIO: ...          # streaming read
    def move(self, src: str, dst: str) -> None: ...      # optional, archive-after-ingest
    def delete(self, path: str) -> None: ...             # optional
```

`FileStat = {path, size, mtime, etag?}` — `mtime`/`path` feed watermarks.

Registered types (lazy imports, same pattern as warehouse drivers):

- `sftp` — paramiko. Auth: password or private key (key stored as connector
  secret). Host key pinning field in config (TOFU on first connect, then pin).
- `ftp` — ftplib (+ TLS via `FTP_TLS`; plain FTP allowed but flagged in UI).
- File interface on the existing storage-backed connectors — `duckdb_storage` /
  the `app.storage` abstraction already has S3/GCS/Azure/local clients; expose
  them through the mixin rather than writing new clients.

Connectors with `network_mode=bridge` route file traffic through the existing
WebSocket reverse tunnel (`backend/app/bridges/`) exactly like query traffic —
phase 1 needs no bridge changes for "SFTP inside customer VPC".

`capabilities()` additions: `file_interface: bool`, plus loader flags from §4.

---

## 3. `file_ingest` task kind

Symmetric to `bucket_load` (`backend/app/flows/handlers/bucket.py`); new
handler `backend/app/flows/handlers/file_ingest.py`. Config:

```jsonc
{
  "source": {"connector_id": "…", "path": "outbound/*.csv"},
  "format": "csv | json | ndjson | parquet | zip | auto",
  "inner_format": "csv",                  // when format=zip: format of entries
  "target": {"connector_id": "…", "object": "raw.orders"},  // any connector
  "mode": "append | overwrite | merge",
  "incremental": {"strategy": "mtime | filename | none"},
  "post_action": "none | move:<dir> | delete"
}
```

- **Zip is a format, not a source type.** `format=zip` expands entries
  (optionally filtered), applies `inner_format`. Works identically for
  zip-in-bucket and zip-over-SFTP.
- **Watermarks**: reuse `flow_watermarks` (migration `0004_flows.sql`).
  `mtime` strategy ingests files newer than the stored mark; `filename` uses
  lexicographic ordering. Mark advances only on task success.
- The handler talks **only** to the file-connector interface and the loader
  layer (§4), so FTP/SFTP/bucket are one code path.

---

## 4. Loader layer — any connector as target

New module `backend/app/flows/loaders.py`. Strategy chosen per target
connector capabilities:

| Target class | Strategy | Mechanism |
|---|---|---|
| Object storage (incl. managed lakehouse) | `promote` | server-side copy from staging to final path |
| Warehouse with bulk-load compatible with the staging store | `bulk_load` | BigQuery load job (GCS), Snowflake `COPY INTO` (S3/GCS/Azure), Redshift `COPY` (S3), ClickHouse `s3()` … |
| Everything else (Postgres, MySQL, …) | `stream` | worker reads staged Parquet, streams batches (`COPY FROM STDIN`, batched INSERT) |

`capabilities()` additions: `bulk_load_from: ["s3"|"gcs"|"az"]`, `stream_load: bool`.

- `stream` is the universal fallback; `bulk_load` is a per-connector
  optimization (phase 4). Cross-cloud mismatches (e.g. staging on S3, target
  BigQuery which only loads from GCS) fall back to `stream` rather than
  multi-cloud staging in v1.
- All loads run on **central workers** with centrally-resolved secrets,
  regardless of where the bytes were sourced.
- Same loader layer later backs a `connector_write` task kind (flow results →
  connector), the write-side sibling of `bucket_load`.

---

## 5. Staging

Layout: `<staging-store>/orgs/<org_id>/staging/<run_id>/…` — per-run prefix.

- **Managed cloud**: dedicated bucket, separate from the lakehouse bucket.
  - Bucket policy: `PutObject` only for grant principals — no list/read/delete.
  - Lifecycle expiry 24–48 h on the whole bucket (failed-run cleanup is free).
  - Optionally its own KMS key.
  - Rationale: the prefix-pinned grant is the primary control; the dedicated
    bucket bounds the blast radius of any grant-scoping bug to transient,
    not-yet-trusted data. Audit line is clean: every write came from an
    untrusted source.
- **Self-host/dev**: config may point staging at a `staging/` prefix of the
  single existing bucket. Identical code path, weaker posture, documented.
- Provisioned by the managed-lakehouse provisioner
  (`backend/app/lakehouse/managed.py`) — one staging bucket per deployment,
  orgs prefix-isolated within it (consistent with `PrefixIsolatedProvider`).

Promotion/load happens only after **manifest verification**: the producer
(worker or agent) reports `{files: [{path, size, sha256}], row_counts}`; the
server verifies before promote/`COPY`. A malicious producer can write garbage
into its own staging prefix but cannot silently poison a target.

---

## 6. Python API ingestion

The `python` task kind already injects `secrets`, `params`, `inputs`,
and runs in a subprocess (`backend/app/flows/executor.py`). Additions:

1. **Watermark advance**: task may return
   `{"rows": …, "watermark": "<iso>"}`; runtime persists the mark to
   `flow_watermarks` only on success. `ctx.watermark` already injected.
2. **Staging/lake writer**: `ctx.staging.write(df_or_batches, "orders/",
   format="parquet") → manifest`. Server-pinned to
   `orgs/<org>/staging/<run_id>/` — user code cannot escape the prefix. Large
   pulls land as Parquet manifests, not rows serialized through task results;
   a downstream `file_ingest`-style load step (or the same task's declared
   `target`) promotes/loads.
3. **Credentials live in connectors/secrets**, referenced from code
   (`ctx.secrets[...]`, `{{ secrets.NAME }}`) — never inline. Customer APIs get
   registered as `http_json` connectors or named secrets so rotation/revoke
   and the "systems we touch" inventory stay in one place.
4. **Templates, not framework**: 3–4 starter templates (offset-paginated REST,
   cursor-paginated, OAuth token refresh, since-timestamp incremental).
   No declarative `http_pull` DSL.
5. Dependencies: pinned curated set for now (httpx, pandas, pyarrow already
   present). Per-org extra requirements is a known future ask — out of scope.

VPC-only customer APIs: central execution can't reach them; this is the
phase-3/5 case (bridge-side execution), not solved by proxy hacks in v1.

---

## 7. Bridge v2 — install, auth, revoke

### Install
Agent ships inside the existing pip CLI (`cli/nubi_cli/`):

```
pip install nubi
nubi bridge start --token nubi_br_…   # or token in agent config file
```

Container image / `curl | sh` wrapper later. Agent connects out (WebSocket
reverse tunnel — existing `bridges/agent.py` / `broker.py`), so no inbound
firewall holes on the customer side.

### Bridge tokens
Same proven pattern as API keys (`0010_api_keys.sql`,
`backend/app/auth/api_keys.py`) but **scoped to the bridge identity, not a
user**:

- Format `nubi_br_<43-char-base64url>` (256-bit), SHA-256 hash stored, raw
  value shown once at mint. Bound to (org, bridge_id).
- Presented on every tunnel handshake/heartbeat. Authenticates the **control
  channel only** — a bridge token by itself can read no secrets and no storage.
- **Rotate**: mint new, grace window where both validate, then revoke old.
- **Revoke**: `revoked_at`; broker rejects next handshake/heartbeat and drops
  the live tunnel. Bridge → `offline`; connectors pinned to it fail fast with
  "bridge revoked", not a hang.

### Ephemeral grants (agent-side ingestion)
1. Agent claims an ingest task over the authenticated channel (the existing
   claim/lease model in `runtime.py` extends to remote claimants).
2. Control plane validates bridge ↔ org ↔ task binding, then mints the grant:
   presigned multipart URLs or STS/SAS/downscoped token, **write-only**, pinned
   to `orgs/<org>/staging/<run_id>/`, TTL 15–60 min. Delivered over the tunnel,
   held in agent memory only, never on disk.
3. Agent streams from the local source to staging, reports the manifest.
4. Central worker verifies and promotes/loads (§4–5).

**Blast radius of a fully compromised customer machine**: write-only access to
one run's staging prefix + ability to claim that one bridge's tasks until the
token is revoked. No read of org data, no connector creds, no cross-org or
cross-run reach. (S3 presigned URLs aren't revocable mid-TTL — keep TTLs
short; Azure SAS-via-stored-access-policy is revocable if we want it.)

### Reserved: agent-resident credentials (not built now)
For "target is the customer's own warehouse in the same VPC as the bridge":
secret configured agent-side via CLI; control plane stores only a reference.
**Reserve the secret-reference format now** so a connector secret can be either
inline ciphertext or `resident:<bridge_id>:<name>`. Build later (EE candidate)
when a customer asks; it inverts the trust boundary cleanly (their creds never
enter our store).

---

## 8. Phases

1. **Central ingestion** — file-connector interface; `sftp`/`ftp` connectors +
   file interface on storage connectors; `file_ingest` task kind (formats incl.
   zip, watermarks, post-actions); loader layer with `promote` + `stream`
   fallback; staging-location config (prefix mode for self-host). Bridge-routed
   sources work via the existing tunnel, executed centrally.
2. **Python ergonomics** — `ctx.staging` writer, watermark advance from Python
   returns, ingest templates in the builder.
3. **Bridge v2** — bridge tokens (mint/rotate/revoke), agent in the pip CLI,
   dedicated staging bucket + grant minting in managed cloud, agent-side
   execution of `file_ingest` claims.
4. **Bulk loads** — per-warehouse `bulk_load` (BigQuery load jobs, Snowflake
   `COPY INTO`, Redshift `COPY`, ClickHouse `s3()`); `connector_write` task kind.
5. **Later / on demand** — agent-resident credentials (EE candidate),
   multi-cloud staging, per-org Python dependencies, customer-push ingest
   endpoint.

---

## 9. Out of scope (keep forward-compatible, do not build)

- Declarative HTTP pagination DSL (`http_pull` task kind) — Python + templates
  instead.
- Shipping any stored connector secret to an agent, ever.
- Streaming/CDC ingestion — `streaming_cdc` capability flag exists; untouched.
- Customer-push ingest API (external systems POST to us) — separate design.
