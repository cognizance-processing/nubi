# Data-Custody Tier (BYO storage, keys & residency)

The **data-custody tier** is an opt-in capability set for deployers who must own
their storage and keys and pin data to a region (POPIA / GDPR data-residency). It
ships **in the OSS core** — every seam and implementation lives in the main repo,
so a self-hoster runs it on their own infrastructure — but it is **OFF by
default** and packaged as a paid tier. There is no separate `ee/` dependency; the
authoritative gate is a single config flag.

> **Status note (CMEK):** customer-managed encryption keys are supported in
> **`kms` mode** (cloud-KMS bucket key, operator-readable via the grant).
> **`client` mode** (deployer-held key the operator never sees) is implemented at
> the crypto layer but is **not yet wired into the engine read path**, so the
> provider **fails closed** (HTTP 501) when it is requested — Nubi never ships a
> custody guarantee it cannot honour. See [CMEK modes](#cmek-modes).

---

## Capabilities at a glance

| # | Capability | What it gives you | Config |
|---|------------|-------------------|--------|
| 1 | Dedicated bucket provider | A deployer-owned bucket **per managed datastore** (stronger than a shared-bucket prefix) | `NUBI_LAKEHOUSE_PROVIDER=dedicated` |
| 1 | CMEK (`kms`) | Bucket encrypted with a cloud-KMS key you own | `NUBI_CMEK_MODE=kms` + `NUBI_CMEK_KEY_URI` |
| 2 | Versioned write/ingest API | A first-class host-write contract: land Parquet → atomic publish, idempotent full-replace + incremental partition append, schema declare/evolve | (always available when tier on) |
| 4 | Region pinning | Managed-lake storage refuses any bucket whose region ≠ the configured region | `NUBI_LAKE_REGION` |
| 4 | Cache encryption at rest | Query-cache bytes AES-256-GCM encrypted, per-tenant key binding | `NUBI_CACHE_ENCRYPTION_KEY` |
| 5 | Bulk export to your bucket | Export a managed lake's tables to a **bucket you control** (anti-lock-in) | (always available when tier on) |
| 6 | Tenant isolation, metering, BYO connectors | Already in OSS core | — |

All capabilities are independently optional — enable only what you need.

---

## OSS-core vs tier boundary

Everything is in the OSS repo and self-hostable. The "tier" is a packaging gate,
not a code split:

| Concern | Where it lives | Notes |
|---|---|---|
| Provider seam (`ManagedLakehouseProvider`), dispatch | OSS core (`app/lakehouse/managed.py`) | The `prefix` provider is the default; `dedicated` is selected by config |
| `PrefixIsolatedProvider` (BYO **bucket** via `NUBI_BUCKET_URI`) | OSS core | A self-hoster can already point the central bucket at their own GCS/S3 — BYO storage with the flag **off** |
| `DedicatedBucketProvider`, CMEK, region pin | OSS core, **tier-gated** | `app/lakehouse/dedicated.py`, `app/lakehouse/cmek.py` |
| Write/ingest API, bulk export | OSS core, **tier-gated** | `app/routes/ingest.py`, `app/routes/lake_export.py` |
| Cache encryption | OSS core, **tier-gated** | `app/connectors/cache_encryption.py` |
| The gate itself | OSS core | `app/lakehouse/custody.py` — single source of truth |

> **Note on BYO storage without the tier:** because the central bucket is just
> `NUBI_BUCKET_URI`, a self-hosted OSS deployment *already* keeps all managed-lake
> data in the deployer's own bucket. The custody tier adds **dedicated buckets
> per datastore, region enforcement, CMEK, the host-write API, cache encryption,
> and bulk export** on top of that.

---

## Enabling the tier

Set the deployment switch, then enable individual capabilities:

```bash
# Master switch — nothing below takes effect without this.
NUBI_CUSTODY_ENABLED=true

# Storage provider: 'prefix' (default, shared central bucket) or 'dedicated'.
NUBI_LAKEHOUSE_PROVIDER=dedicated

# Region pin — provisioning refuses storage whose region differs.
NUBI_LAKE_REGION=africa-south1

# CMEK — customer-managed encryption key (see CMEK modes below).
NUBI_CMEK_MODE=kms
NUBI_CMEK_KEY_URI=projects/acme/locations/africa-south1/keyRings/nubi/cryptoKeys/lake

# Query-cache encryption at rest (base64-encoded 32 bytes).
NUBI_CACHE_ENCRYPTION_KEY=<base64 32 bytes>
```

When `NUBI_CUSTODY_ENABLED` is false (default), every custody route fails closed
with `403 custody_disabled`, the provider stays `prefix`, region/CMEK/cache
encryption are no-ops, and `lakehouse_provider_kind()` returns `prefix`
regardless of `NUBI_LAKEHOUSE_PROVIDER`.

---

## Storage providers

### `prefix` (default)

Per-datastore key prefix inside one central bucket:
`orgs/<org>/lake/<datastore>/`. The central bucket is `NUBI_BUCKET_URI` — point it
at your own GCS/S3 bucket and managed-lake data is already yours.

### `dedicated`

Each managed datastore gets its **own** deployer-owned bucket
(`nubi-lake-<org…>-<ds…>`, name server-pinned from trusted ids — never user
input). Stronger physical/IAM isolation; the deployer owns the bucket. On
provision the provider:

- creates (or validates) the bucket in `NUBI_LAKE_REGION` — a region mismatch on
  an existing bucket raises `409 region_mismatch`;
- applies the bucket-level CMEK key in `kms` mode;
- records `managed_region` and `cmek_mode` on the datastore row for audit.

If `dedicated` is explicitly requested but the provider cannot be constructed
(missing cloud SDK, auth failure), provisioning **fails closed** rather than
silently downgrading to the shared-bucket `prefix` provider.

### CMEK modes

| Mode | Guarantee | Mechanism | Status |
|---|---|---|---|
| `none` | Bucket-default AES-256 at-rest only | — | Supported |
| `kms` | Customer-managed key; operator **can** read via the KMS grant (residency/control, not zero-knowledge) | Bucket default KMS key (`default_kms_key_name` / SSE-KMS) — transparent to the engine | Supported |
| `client` | Deployer-held key the operator **never** sees | App-layer AES-256-GCM envelope before upload | **Fail-closed (501) at every entry point** — see below |

#### Why `client` mode is fail-closed — and where the guard lives

The query engine (DuckDB) reads Parquet objects **directly from cloud/local storage** with no
app-layer mediation.  If the app were to write AES-256-GCM encrypted blobs and DuckDB then
read them as raw Parquet, the result would be **silent data corruption** — not a decryption
error, just garbage query results, with no indication that anything is wrong.

To prevent this, every entry point that could write or read lake objects is individually
guarded by a single centralized helper (`cmek.assert_cmek_readable(mode)` in
`app/lakehouse/cmek.py`).  The guard raises `ManagedLakehouseError("cmek_client_unsupported",
…, 501)` when `mode == "client"` and is a no-op for `none` and `kms`.

**Guarded entry points** (as of this commit):

| Entry point | File | Guard position |
|---|---|---|
| `DedicatedBucketProvider.provision()` | `app/lakehouse/dedicated.py` | Top of method, before bucket creation |
| `PrefixIsolatedProvider.provision()` | `app/lakehouse/managed.py` | Top of method, before row creation |
| `open_ingest_session()` | `app/routes/ingest.py` | After custody gate, before session open |
| `upload_part()` | `app/routes/ingest.py` | After custody gate, before staging write |
| `commit_session()` | `app/routes/ingest.py` | After custody gate, before lake promote |
| `export_lake()` | `app/routes/lake_export.py` | After custody gate, before DuckDB read |
| `list_lake_tables()` | `app/routes/lake_export.py` | After custody gate, before storage listing |

The guard is intentionally **belt-and-suspenders** at the ingest step: open/upload/commit each
check independently so a CMEK mode change between steps cannot sneak through.

#### Forward path for `client` mode (design decision — tracked, not built)

Three options exist; none is cheap or risk-free.  This is an intentional, documented
limitation — not a gap.

1. **Decrypting proxy** — a thin sidecar in front of the object store that holds the key in
   memory and decrypts on the fly for every DuckDB read.  Adds latency and a new trust
   boundary (the proxy can see plaintext); must handle DuckDB's parallel read threads.

2. **DuckDB extension** — a custom loadable extension that registers a VFS layer
   intercepting `read_parquet()` and decrypting blobs.  High complexity; must track DuckDB
   API changes; testing for silent-corruption edge cases is non-trivial.

3. **Decrypt-before-engine** — the app materialises decrypted Parquet into an ephemeral temp
   area for each query, then DuckDB reads the temp copy.  High I/O cost and a window where
   plaintext exists at rest in the temp area (defeats part of the custody guarantee).

Until one of these paths is designed, reviewed, and tested against silent-corruption scenarios,
`client` mode remains fail-closed at every write/read entry point.  Use `mode=kms` for
customer-managed keys today.

---

## Write / ingest API (v1)

A session-based host-write contract under `/api/v1/lake`. The producer lands
Parquet parts into a staging area, then commits a manifest; the server verifies
every part (size + SHA-256) before an atomic publish.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/lake/{datastore_id}/ingest/sessions` | Open a session — `{mode, partition?, table_name?, schema?, idempotency_key}`. Idempotent on `idempotency_key`. |
| `PUT` | `/lake/{datastore_id}/ingest/sessions/{id}/parts/{relpath}` | Upload one Parquet/Arrow part; returns its `{path, size, sha256}`. |
| `POST` | `/lake/{datastore_id}/ingest/sessions/{id}/commit` | Verify the manifest, then atomically publish. |
| `GET` | `/lake/{datastore_id}/ingest/sessions/{id}` | Session status. |
| `POST` | `/lake/{datastore_id}/ingest/sessions/{id}/abort` | Cleanup staging, mark aborted. |

**Publish modes**

- `full_replace` — promote new parts, then sweep stale objects under the table
  prefix (publish-then-sweep; the `_nubi/` sidecars are never swept). Object
  stores lack true multi-object atomicity — documented.
- `append` — promote parts under a partition sub-prefix without touching existing
  partitions; re-commit of the same session is a no-op (idempotent).

**Schema** is recorded per table (`_nubi/schema.json`). Append rejects column
removal or type narrowing with `409 schema_incompatible`; additive columns are
allowed. `full_replace` validates the declared schema against the actual parquet.

**Safety:** `org_id` is from the verified identity (never the body); the
datastore must be a managed lake in the caller's org (else `404`); part `relpath`
and `partition` are validated and the final key is asserted to remain under the
server-pinned datastore prefix before any write.

> **Ingest session store (migration 0017):** In production, session state is
> persisted in the `ingest_sessions` Postgres table (migration `0017_ingest_sessions.sql`),
> backed by `PgIngestSessionStore`.  The idempotency index
> `UNIQUE (org_id, datastore_id, idempotency_key)` and a CAS `WHERE state = $from`
> guarantee exactly-once opens and serialised commits across concurrent callers.
> In test and local dev (no DB pool), the store falls back to
> `InMemoryIngestSessionStore` automatically — no config change needed.

---

## Bulk export

Export a managed lake to a bucket **you** control — the anti-lock-in guarantee.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/lake/{datastore_id}/tables` | List exportable tables in the lake. |
| `POST` | `/api/v1/lake/{datastore_id}/export` | Export to `dest_uri` (your bucket). Body: `{dest_uri, dest_creds?, table?|sql?, format?}`. |

`format` is `parquet` (default) or `csv`. The source lake is server-pinned; the
destination is deployer-supplied by design. Guards: `table` is validated against
a safe-segment regex **and** must be a discovered table; `sql` must be a single
`SELECT` (comment-stripped, multi-statement rejected, no `COPY`/`ATTACH`/`PRAGMA`/
`INSTALL`); `dest_uri` may not resolve inside the source lake prefix; `dest_creds`
are registered as a scoped secret and never logged.

> Synchronous export suits reasonable sizes. Very large lakes should use a chunked
> / async job path — documented as a follow-up.

---

## Cache encryption at rest

When `NUBI_CACHE_ENCRYPTION_KEY` is set, query-cache values (in-process LRU and
Redis) are AES-256-GCM encrypted transparently — callers (`/query`) are
unchanged. The already org+datastore-scoped cache key is bound into the GCM
**AAD**, so ciphertext can never be replayed across tenants; a decrypt failure is
treated as a cache **miss** (fail-safe). A *misconfigured* key (bad base64 / wrong
length) **fails closed** — the deployment refuses to start the cache as plaintext
when encryption was requested.

---

## Security model

- **Server-pinned paths** — bucket names, lake prefixes, staging/partition keys
  derive only from trusted ids; final keys are asserted to stay under the
  datastore prefix.
- **Org-scoped everything** — datastores, ingest sessions, secrets, cache keys;
  cross-org access is a 404 / miss.
- **Fail-closed gates** — every custody route calls `assert_custody_enabled()`
  first; the tier defaults off; explicit-but-broken `dedicated`/CMEK-`client`/
  cache-key configs raise rather than silently degrade.
- **Secrets** — storage/destination credentials live only in the encrypted
  secret store, never in responses or logs.

---

## Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `NUBI_CUSTODY_ENABLED` | `false` | Master switch for the tier |
| `NUBI_LAKEHOUSE_PROVIDER` | `prefix` | `prefix` \| `dedicated` |
| `NUBI_LAKE_REGION` | `""` | Region pin for storage + cache (e.g. `africa-south1`) |
| `NUBI_CMEK_MODE` | `none` | `none` \| `kms` \| `client` (client → 501) |
| `NUBI_CMEK_KEY_URI` | `""` | Cloud-KMS key id/uri (mode `kms`) |
| `NUBI_CMEK_KEY_MATERIAL` | `""` | base64 32-byte key (mode `client`, not yet active) |
| `NUBI_CACHE_ENCRYPTION_KEY` | `""` | base64 32-byte key; enables cache encryption |
| `NUBI_BUCKET_URI` | — | Central bucket (point at your own bucket for BYO storage) |

See also: [Self-Hosting](self-host.md) · [Open-Core Architecture](open-core.md) ·
[Compliance](compliance.md) · [Ingestion Design](ingestion-design.md).
