# Connector Security

![The Nubi connector trust boundary: secrets stay encrypted in the app layer, never in the database or API responses](illustration:TrustBoundary)

Connector credentials — passwords, service-account keys, API tokens — are encrypted at the application layer before they touch the database. This page explains the storage model, the encryption scheme, key rotation, network modes, and the API contracts that enforce these guarantees.

---

## Storage Model

A connector is two things: a `datastores` row that holds non-secret config, and an encrypted blob in a separate `connector_secrets` table. The split is explicit and enforced in code.

| Table | Holds | Protection |
|---|---|---|
| `datastores` | `id`, `org_id`, `name`, `config { connector_type, host, port, database, user, sslmode, network_mode, bridge_id, … }` | Plain JSONB — **no secrets here** |
| `connector_secrets` | `datastore_id` (FK → `datastores.id`), `org_id`, `ciphertext`, `nonce`, `key_version` | AES-256-GCM encrypted blob |

**What goes in `datastores.config`** (plain JSONB, readable by anyone with DB access): `connector_type`, `host`, `port`, `database`, `user`, `sslmode`, `network_mode`, `bridge_id`, and any other structural fields.

**What goes in `connector_secrets`** (ciphertext only): `password`, `service_account_json`, `token`, `api_key`, `access_token`, `aws_secret_access_key`, `private_key`.

The schema for `connector_secrets`:

```sql
connector_secrets (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    datastore_id uuid        NOT NULL,
    org_id       uuid        NOT NULL,
    ciphertext   bytea       NOT NULL,
    nonce        bytea       NOT NULL,
    key_version  int         NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (datastore_id)
)
```

One encrypted blob per connector. The encryption key is never written to or read from the database.

---

## Encryption — AES-256-GCM

Secrets are encrypted in `app.security.crypto` using the Python `cryptography` library's `AESGCM` primitive.

**Per-encryption call:**

1. The current master key (256-bit) is read from the `CONNECTOR_SECRET_KEY` environment variable (base64-encoded, never committed).
2. A **12-byte random nonce** is generated with `os.urandom(12)` (NIST SP 800-38D). A fresh nonce is used for every `put()` call, so two encryptions of the same plaintext produce distinct ciphertexts.
3. `AESGCM.encrypt(nonce, plaintext, associated_data=None)` returns the ciphertext with the **16-byte GCM authentication tag** appended. Any modification to the ciphertext or nonce causes `InvalidTag` on decryption — tampering cannot be silent.
4. The DB stores three fields: `ciphertext` (bytes), `nonce` (bytes), `key_version` (int). The master key is never stored.

The `SecretStore` interface (`app.connectors.secret_store`) wraps this in three async methods:

| Method | What it does |
|--------|--------------|
| `put(datastore_id, org_id, secret)` | JSON-encode the secret dict, encrypt with `encrypt_json()`, upsert into `connector_secrets`. |
| `get(datastore_id, org_id)` | Fetch `ciphertext + nonce + key_version`, decrypt with `decrypt_json()`, return the dict. Returns `None` if not found or org mismatch. |
| `delete(datastore_id, org_id)` | Remove the encrypted blob. Scoped to `org_id` — a wrong-org call is a no-op. |

---

## Environment Setup

Add to your environment (never commit this file):

```bash
# 32-byte random key, base64-encoded
CONNECTOR_SECRET_KEY=<base64-encoded-256-bit-key>

# Optional: version label (defaults to 1)
CONNECTOR_SECRET_KEY_VERSION=1
```

Generate a key:

```bash
python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

The app raises `RuntimeError` at startup if `CONNECTOR_SECRET_KEY` is not set, with a message that includes the generation command.

---

## Key Rotation

The `key_version` column in `connector_secrets` allows incremental, zero-downtime rotation.

**Simple form** — one key in the environment:

```bash
CONNECTOR_SECRET_KEY=<base64-key>
CONNECTOR_SECRET_KEY_VERSION=1
```

**Extended form** — JSON map of all versions. When `CONNECTOR_SECRET_KEYS` is set it takes precedence over the two vars above:

```bash
CONNECTOR_SECRET_KEYS='{"1":"<old-b64-key>","2":"<new-b64-key>"}'
```

The **highest numeric version** in the map is treated as current. New secrets are encrypted with the current key. Old blobs (any version still in the map) are decrypted transparently using their recorded `key_version`.

**Rotation procedure:**

1. Generate a new 32-byte key.
2. Set `CONNECTOR_SECRET_KEYS` with both the old and new keys (`"1"` → old, `"2"` → new). Deploy. New secrets encrypt with key 2; existing blobs decrypt with key 1.
3. Run the re-encryption migration — it re-encrypts all key-1 blobs with key 2.
4. Remove key 1 from `CONNECTOR_SECRET_KEYS`. Old-version decrypts are no longer needed.

Requesting decryption with a version not present in the registry raises `KeyError` with a message listing the registered versions.

---

## API Contract

### Creating a Connector

```json
POST /api/v1/connectors
{
  "name": "prod-postgres",
  "type": "postgres",
  "config": {
    "host": "db.example.com",
    "port": 5432,
    "database": "analytics",
    "user": "readonly",
    "sslmode": "require"
  },
  "secret": {
    "password": "hunter2"
  }
}
```

A Pydantic `model_validator` rejects any request that places a known secret key (`password`, `service_account_json`, `token`, `api_key`, `access_token`, `aws_secret_access_key`, `private_key`) inside `config` — the server returns `422 Unprocessable Entity`.

The response returns the `datastores` row. **The `secret` field is never echoed back.** A `_sanitise()` function strips all known secret keys before every response, and an internal `_assert_no_secret_leakage()` assertion raises immediately in tests if any leak is detected.

### Updating Config or Rotating a Secret

```json
PUT /api/v1/connectors/{id}
{
  "config": { "host": "new-host.example.com" },
  "secret": { "password": "new-password" }
}
```

When `secret` is supplied, `SecretStore.put()` overwrites the existing blob (upsert semantics). Fields not mentioned in `config` are preserved. The same validator rejects secret keys in the `config` field. Response: updated row, no secrets.

### Reading Connectors

`GET /api/v1/connectors` and `GET /api/v1/connectors/{id}` return only the `datastores` row. Secrets are never returned by any read endpoint.

### Deleting a Connector

`DELETE /api/v1/connectors/{id}` deletes the datastore row and calls `SecretStore.delete()` to remove the encrypted blob. A `REFERENCES datastores(id) ON DELETE CASCADE` constraint on `connector_secrets` also removes the blob if the row is deleted at the DB level directly.

### Structural Test Endpoint

`POST /api/v1/connectors/{id}/test` is a **structural check** — no network socket is opened. It verifies that the config row exists and the encrypted secret can be retrieved and decrypted:

```json
{
  "ok": true,
  "checked": "config+secret resolved",
  "connector_id": "<uuid>",
  "type": "postgres",
  "layers": { "config": true, "secret": true }
}
```

`ok: false` with `layers.secret: false` means the encrypted blob is missing or undecryptable — the master key may have been rotated without re-encrypting old blobs, or the blob was never written.

---

## Network Modes

Two fields in `datastores.config` control how the query executor reaches the database:

| Field | Type | Description |
|-------|------|-------------|
| `network_mode` | string | `"direct"` (default) or `"bridge"` |
| `bridge_id` | uuid | References the bridge agent that proxies to the private host |

**`direct`** — the Nubi backend connects to `host:port` in the datastore config directly. No extra infrastructure required.

**`bridge`** — the query executor routes the connection through the bridge agent WebSocket tunnel identified by `bridge_id`. The bridge establishes an agent-per-VPC reverse tunnel; the query goes through a local TCP proxy allocated by the broker. If the bridge agent is not connected, the server returns `503 bridge_not_connected`. Bridge details (the connection registry, tunnel lifecycle) are in [Bridges](/docs/bridges).

`bridge_id` is non-sensitive and is stored in `datastores.config` (not in secrets). The bridge agent itself authenticates using a token in its own config, not in `connector_secrets`.

Modes `ssh_tunnel`, `psc`, and `cloudsql_proxy` are recognised by the schema but return `501 network_mode_unavailable` — they require provisioned infrastructure not yet enabled.

---

## Secrets in Flows

Org-scoped secrets (distinct from connector secrets) can be injected into flow cells using `{{ secrets.NAME }}` in SQL cells or as a `secrets` dict in Python cells. These are managed separately via `nubi secrets set` / `nubi secrets list` and stored encrypted in the `secrets` table. See [Secrets](/docs/secrets) for details.

Connector secrets are never exposed to flow cell code — cells receive a resolved database connection, not the credentials used to open it.

---

## Security Properties Summary

| Property | Enforcement |
|----------|-------------|
| Secrets never in `datastores.config` | Pydantic `model_validator` on create and update; `assert` guard before every DB write |
| Secrets never in API responses | `_sanitise()` strips all secret keys; `_assert_no_secret_leakage()` double-checks |
| Secrets encrypted at rest | AES-256-GCM in the application layer; only ciphertext reaches the DB |
| Nonce uniqueness | 12-byte `os.urandom()` nonce per call — identical plaintexts produce distinct ciphertexts |
| Tampering detection | GCM authentication tag — any mutation of ciphertext or nonce raises `InvalidTag` |
| Master key not in DB | Key lives in env only (`CONNECTOR_SECRET_KEY` or `CONNECTOR_SECRET_KEYS`) |
| Cross-org isolation | All endpoints filter by `org_id`; wrong-org access returns `404` (no information leak) |
| Secret removal on delete | `SecretStore.delete()` called explicitly; DB cascade removes blob if row is hard-deleted |
| Key rotation | `key_version` column + `CONNECTOR_SECRET_KEYS` multi-key registry enables zero-downtime rotation |
| DDL/DML blocked at query layer | Planner rejects non-`SELECT` statements (DROP, DELETE, UPDATE, INSERT, CREATE, TRUNCATE) with `400` |
| Named params never string-concatenated | Values go into positional `$N` bindings; they never appear in the rewritten SQL |
| Pre-run estimates respect caller scope | The optional `Connector.estimate(plan)` hook estimates the RLS-rewritten `plan.sql` (never raw SQL), is advisory-only (swallows engine errors → `None`, never blocks a run), and has no user-facing route — see [Connectors](/docs/connectors#optional-pre-run-estimate) |
