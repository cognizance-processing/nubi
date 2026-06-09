# Secrets

Secrets are org-scoped, named, encrypted credentials — API keys, cloud storage tokens, third-party passwords — that flow tasks reference at execution time without the value ever leaving the server. A secret is written once, encrypted at rest, and resolved server-side into a task's context when a flow runs. The plaintext value is **never** returned by the API after it is set.

Secrets are distinct from connector credentials (see [Connector Security](/docs/connector-security)). Connector secrets live on a datastore row and are consumed by the query planner; these org secrets are general-purpose and consumed by the Flows engine (e.g. `bucket_load` storage credentials).

---

## Model

| Property | Value |
|----------|-------|
| Scope | Per organisation. Callers only see and operate on their own org's secrets. |
| Identity | A `name` (e.g. `S3_CREDS`, `STRIPE_API_KEY`) unique within the org. |
| Encryption at rest | Encrypted before storage; the API responds with metadata only. |
| Read path | Resolved server-side into `TaskContext.secrets` when a flow runs — never sent to the client. |
| Upsert | Setting an existing name overwrites its value (returns `201`). |

The stored shape (internal) is `{id, org_id, name, value_encrypted, created_by, created_at, updated_at}`. The public shape returned by the API drops `value_encrypted` entirely: `{id, org_id, name, created_by, created_at, updated_at}`.

---

## Encryption

Secret values are encrypted at the application layer before they reach the database. The master key is read from the `NUBI_SECRETS_KEY` environment variable — a URL-safe base64-encoded key. Generate one with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `NUBI_SECRETS_KEY` before starting the server. Encryption and decryption read the key lazily, so a misconfiguration surfaces with a clear, actionable error at the time a secret is set or resolved — not silently at import time. Decrypting a value that was encrypted under a different key fails loudly rather than returning garbage.

> The production store is asyncpg-backed (`secrets` table, migration `0015`). Tests inject an in-memory store. Both expose the same interface, so route handlers are unchanged across environments.

---

## Referencing Secrets in Flows

Inside a flow's task `config`, reference a secret by name with the `{{ secrets.NAME }}` template expression:

```json
{
  "key": "export",
  "kind": "bucket_load",
  "needs": ["pull"],
  "config": {
    "uri": "s3://my-bucket/exports/out.parquet",
    "source": "pull",
    "format": "parquet",
    "secret": "S3_CREDS"
  }
}
```

The `bucket_load` task kind takes a `secret` config field naming a secret whose **JSON-decoded** value is used as the storage credentials dict. More generally, any string in a task `config` can interpolate a secret with `{{ secrets.NAME }}`; the executor resolves it from `ctx.secrets` at runtime. See [Referencing data between cells](/docs/flows#referencing-data-between-cells) in the Flows doc.

Before each task runs, the Flows runtime resolves all of the org's secrets into the task context (`resolve_all`), so handlers read them via `ctx.secrets[name]` or via the `{{ secrets.NAME }}` template. Because resolution happens server-side at execution time, the value is never exposed to a dashboard, an embed token, or the client.

---

## REST API

All endpoints require a valid first-party Bearer token. Secrets are org-scoped: cross-org access is not possible.

Base path: `/api/v1/secrets`

### Set (or update) a secret

```
POST /api/v1/secrets
Authorization: Bearer <jwt>
Content-Type: application/json
```

Request:

```json
{ "name": "S3_CREDS", "value": "{\"aws_access_key_id\": \"...\", \"aws_secret_access_key\": \"...\"}" }
```

Response `201` — note there is **no** `value` field:

```json
{
  "id":         "secret-uuid",
  "org_id":     "org-uuid",
  "name":       "S3_CREDS",
  "created_by": "user-uuid",
  "created_at": "2026-01-15T09:00:00Z",
  "updated_at": "2026-01-15T09:00:00Z"
}
```

Setting a name that already exists overwrites the value (upsert) and still returns `201`. An empty `name` or `value` returns `400`.

### List secrets

```
GET /api/v1/secrets
Authorization: Bearer <jwt>
```

Response `200`: an array of the public secret objects (above), sorted by name. Values are never included.

### Delete a secret

```
DELETE /api/v1/secrets/{name}
Authorization: Bearer <jwt>
```

Response `204` on success; `404` if no secret with that name exists for the caller's org.

---

## CLI

The `nubi` CLI manages secrets both locally (for `nubi flows run`, which executes flows on your machine) and via the cloud API.

```bash
# Set a secret locally (~/.nubi/secrets) and, when logged in, via the API.
nubi secrets set S3_CREDS '{"aws_access_key_id": "...", "aws_secret_access_key": "..."}'

# Write only to the local file, skip the API.
nubi secrets set MY_KEY my_value --local-only

# List secret names locally and from the API (values are never shown).
nubi secrets list
```

`nubi secrets set` writes to `~/.nubi/secrets` so a local `nubi flows run` can populate `TaskContext.secrets` and resolve `{{ secrets.NAME }}` templates offline. When a Bearer token is present (after `nubi login`), it also persists the secret via `POST /secrets`. `nubi secrets list` shows, per name, whether it is stored locally and/or in the API — never the value.

See [SDK & CLI](/docs/sdk-and-cli) for the full CLI reference.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NUBI_SECRETS_KEY` | Yes (to use secrets) | URL-safe base64 master key used to encrypt/decrypt secret values. Generate with the command above. If unset, setting or resolving a secret raises a clear error. |
