# Secrets

![Secrets stay server-side — the trust boundary between browser and kernel](illustration:TrustBoundary)

Secrets are org-scoped, named, encrypted credentials — API keys, storage tokens, third-party passwords — that flow tasks and notebook cells reference at execution time. A secret is written once, encrypted at rest under a master key that never enters the database, and resolved server-side at runtime. The plaintext value is **never** returned by the API after it is set.

Secrets are distinct from connector credentials. Connector credentials live on a datastore row and are consumed by the query planner (encrypted with AES-256-GCM); org secrets are a general-purpose named key-value store consumed by the Flows engine and notebook cells. See [Connector Security](/docs/connector-security) for the connector side.

---

## Data model

| Property | Detail |
|---|---|
| Scope | Per organisation. Callers only see and operate on their own org's secrets. |
| Identity | A `name` (e.g. `S3_CREDS`, `STRIPE_API_KEY`) — unique within the org. |
| Encryption | Fernet (AES-128-CBC + HMAC-SHA256) at the application layer, before the row reaches the database. |
| Read path | Resolved server-side into `TaskContext.secrets` at execution time — never sent to the browser. |
| Upsert | Setting an existing name overwrites the value and returns `201`. |

The internal stored shape is `{id, org_id, name, value_encrypted, created_by, created_at, updated_at}`. Every API response drops `value_encrypted` entirely and returns only `{id, org_id, name, created_by, created_at, updated_at}`.

---

## Encryption

Secret values are encrypted at the application layer (Fernet — AES-128-CBC + HMAC-SHA256) before they reach the database. The master key is read from the `NUBI_SECRETS_KEY` environment variable, a URL-safe base64-encoded key. Generate one with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `NUBI_SECRETS_KEY` before starting the server. The key is read lazily — misconfiguration surfaces at the first set or resolve call with a clear error, not silently at import time. Decrypting a value encrypted under a different key fails loudly rather than returning garbage.

> The production store is asyncpg-backed (`secrets` table, migration `0015`). Tests inject an `InMemorySecretStore`. Both expose the same interface, so route handlers are unchanged across environments.

---

## Referencing secrets in flows

Secrets are managed in the UI at **Flows → Secrets** (`/flows/secrets`) — values are write-only there too:

![The Secrets page under Flows — add encrypted credentials and reference them as {{ secrets.NAME }}](/docs/screenshots/secrets.png)

Reference a secret inside a flow task `config` with the `{{ secrets.NAME }}` template expression:

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

The `bucket_load` task kind takes a `secret` field naming an org secret whose JSON-decoded value is used as the storage credentials dict. More generally, any string in a task `config` can interpolate a secret with `{{ secrets.NAME }}`; the executor resolves it from `ctx.secrets` at runtime.

Before each task runs, the Flows runtime calls `resolve_all` to load all org secrets into the task context. Handlers read them via `ctx.secrets["NAME"]` or the template expression. Resolution happens server-side at execution time — values are never exposed to a dashboard, an embed token, or the browser.

An unknown secret name resolves to an empty string rather than raising an error.

See [Flows](/docs/flows) for the full task authoring reference.

---

## Secrets in notebook cells

Secrets work identically across all execution modes — scheduled flow runs, **Run cell** previews in the notebook editor, and durable single-cell runs all resolve secrets with the same org-scoped helper.

**SQL cells** interpolate secrets with the template syntax:

```sql
SELECT * FROM read_json('https://api.example.com/data?key={{ secrets.MY_API_KEY }}')
```

**Python cells** receive a `secrets` dict alongside `inputs`, `params`, and `dataframes`:

```python
import urllib.request

req = urllib.request.Request(
    "https://api.example.com/data",
    headers={"Authorization": "Bearer " + secrets["MY_API_KEY"]},
)
result = {"value": urllib.request.urlopen(req).status}
```

The dict is resolved server-side and injected into the sandboxed subprocess via its script context — not via environment variables, so kernel env scrubbing is preserved.

Both the SQL and Python cell editors have a **Secrets** dropdown (key icon) in the toolbar that lists org secret names and inserts a reference at the cursor.

### Log masking

After a cell runs, every occurrence of a resolved secret value (4 characters or longer) in **error messages** and **captured logs** (stdout, tracebacks) is replaced with `•••`. So `print(secrets["MY_API_KEY"])` shows `•••` in task logs, and a failing query whose resolved SQL embeds a secret reports a redacted error.

Values shorter than four characters are not masked (too noisy for single-digit tokens).

> Anyone in your org who can edit and run flows can still *use* a secret — including writing a cell that SELECTs the value into its result rows. Masking protects against accidental leakage in logs and errors, not against a deliberate flow author. Assign org membership accordingly.

---

## REST API

All endpoints require a valid first-party Bearer token. Secrets are org-scoped: cross-org access is not possible. Set and delete operations require writer-level access.

Base path: `/api/v1/secrets`

### Set or update a secret

```
POST /api/v1/secrets
Authorization: Bearer <jwt>
Content-Type: application/json
```

Request body:

```json
{ "name": "S3_CREDS", "value": "{\"aws_access_key_id\": \"...\", \"aws_secret_access_key\": \"...\"}" }
```

Response `201` — no `value` field:

```json
{
  "id":         "secret-uuid",
  "org_id":     "org-uuid",
  "name":       "S3_CREDS",
  "created_by": "user-uuid",
  "created_at": "2026-01-15T09:00:00+00:00",
  "updated_at": "2026-01-15T09:00:00+00:00"
}
```

Setting a name that already exists overwrites the value (upsert) and still returns `201`. An empty `name` or `value` returns `400`.

### List secrets

```
GET /api/v1/secrets
Authorization: Bearer <jwt>
```

Response `200` — array of public secret objects sorted by name. Values are never included.

### Delete a secret

```
DELETE /api/v1/secrets/{name}
Authorization: Bearer <jwt>
```

Response `204` on success; `404` if no secret with that name exists in the caller's org.

---

## CLI

The `nubi` CLI manages secrets both locally (for `nubi flows run`, which executes flows on your machine) and via the cloud API.

```bash
# Set a secret locally (~/.nubi/secrets) and, when logged in, via the API.
nubi secrets set S3_CREDS '{"aws_access_key_id": "...", "aws_secret_access_key": "..."}'

# Write only to the local file — skip the API.
nubi secrets set MY_KEY my_value --local-only

# List all secret names locally and from the API (values are never shown).
nubi secrets list
```

`nubi secrets set` writes to `~/.nubi/secrets` so a local `nubi flows run` can populate `TaskContext.secrets` and resolve `{{ secrets.NAME }}` templates offline. When a Bearer token is present (after `nubi login`), it also persists the secret via `POST /api/v1/secrets`. If no token is present, the CLI notes that the value was saved locally only.

`nubi secrets list` prints a table showing, for each name, whether it is stored locally and/or in the API — never the value.

See [SDK & CLI](/docs/sdk-and-cli) for the full CLI reference.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `NUBI_SECRETS_KEY` | Yes (to use secrets) | URL-safe base64 Fernet master key used to encrypt and decrypt secret values at the application layer. Generate with the command above. If unset, any attempt to set or resolve a secret raises a clear `RuntimeError`. |
