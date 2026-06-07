# Connector Security

This document explains how Nubi stores and protects connector credentials — connection details, passwords, service-account keys, and API tokens — so that secrets are never readable from the database directly, even by someone with full access to the Nubi Postgres schema.

---

## The Model

A connector is a `datastores` row coupled with an encrypted secret blob in a separate `connector_secrets` table. The two are related by the datastore `id`. This split is intentional:

- **`datastores.config`** holds *non-secret* connection parameters only: `host`, `port`, `database`, `user`, `sslmode`, `network_mode`, `bridge_id`, `connector_type`, and similar structural fields. These are stored as plain JSONB and are readable by anyone who can query the table.
- **`connector_secrets`** holds the encrypted blob for sensitive fields: `password`, `service_account_json`, `token`, `api_key`. The ciphertext is stored; the plaintext is never written to any database table.

```
┌─────────────────────────────────────────┐
│  datastores (plain JSONB)               │
│  id, org_id, name, config {             │
│    connector_type, host, port,          │
│    database, user, sslmode, …           │  ← NO secrets here
│  }                                      │
└──────────────────────┬──────────────────┘
                       │ id FK
┌──────────────────────▼──────────────────┐
│  connector_secrets                      │
│  datastore_id, org_id, key_version,     │
│  nonce, ciphertext                      │  ← AES-256-GCM encrypted
└─────────────────────────────────────────┘
```

---

## Encryption — AES-256-GCM

Secrets are encrypted at the application layer using **AES-256-GCM** (`app.security.crypto`) before they reach the database. The scheme:

1. The application reads a 256-bit master key from the `CONNECTOR_SECRET_KEY` environment variable (stored in `.env.main`, never committed, never in the DB).
2. Each secret blob is encrypted with a **random 96-bit (12-byte) nonce** (NIST SP 800-38D). The nonce is stored alongside the ciphertext.
3. The **16-byte GCM authentication tag** is appended to the ciphertext by the `cryptography` library — any tampering is detected on decryption.
4. A `key_version` integer is stored so that key rotation can be applied incrementally without downtime.

The DB stores three fields per secret row: `ciphertext` (bytes), `nonce` (bytes), `key_version` (int). The encryption key is never stored in the database.

### `connector_secrets` Schema

```sql
connector_secrets(
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

---

## SecretStore Interface

The `SecretStore` interface (`app.connectors.secret_store`) provides three async methods:

| Method | Description |
|--------|-------------|
| `put(datastore_id, org_id, secret: dict)` | Serialize, encrypt with `encrypt_json()`, and upsert into `connector_secrets`. |
| `get(datastore_id, org_id) → dict` | Fetch ciphertext + nonce, decrypt with `decrypt_json()`, deserialize, and return the secret dict. |
| `delete(datastore_id, org_id)` | Remove the encrypted blob (called on connector delete). |

The master key is the **only** sensitive value that must be protected outside the database. There is no GCP Secret Manager or external KMS dependency; the security boundary is the key itself.

---

## Environment Setup

Add to `.env.main` (never commit this file):

```bash
# 32-byte random key, base64-encoded
CONNECTOR_SECRET_KEY=<base64-encoded-256-bit-key>

# Optional: explicit version label (defaults to 1)
CONNECTOR_SECRET_KEY_VERSION=1
```

Generate a key:

```bash
python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

---

## Key Rotation

To rotate the master key without downtime:

1. Generate a new 32-byte key.
2. Set `CONNECTOR_SECRET_KEYS` to a JSON map of all versions (old + new):

```bash
CONNECTOR_SECRET_KEYS='{"1":"<old-b64-key>","2":"<new-b64-key>"}'
```

The highest numeric version is treated as current — new secrets are encrypted with key 2. Old blobs (still encrypted with key 1) are decrypted transparently using the matching key from the registry.

3. Run the re-encryption migration to re-encrypt all key-1 blobs with key 2, then remove key 1 from the map.

The `key_version` column in `connector_secrets` tracks which key encrypted each blob, enabling partial migrations.

---

## API Contract — Secret Handling

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

The server rejects any request that places a known secret key (`password`, `service_account_json`, `token`, `api_key`) inside `config`. This is enforced by a Pydantic `model_validator` that returns `422 Unprocessable Entity`.

The response contains the `datastores` row with `config` only — **the `secret` field is never echoed back**. A `_sanitise()` function strips all known secret keys from the response before returning. An internal `_assert_no_secret_leakage()` guard raises `AssertionError` in tests if any secret key appears in the serialised response.

### Updating and Rotating Secrets

```json
PUT /api/v1/connectors/{id}
{
  "config": { "host": "new-host.example.com" },
  "secret": { "password": "new-password" }
}
```

When `secret` is supplied, `SecretStore.put()` overwrites the existing encrypted blob. Config fields not mentioned are preserved.

### Retrieving Connectors

`GET /api/v1/connectors` and `GET /api/v1/connectors/{id}` return the `datastores` row. **Secrets are never returned** by any read endpoint.

### Deleting a Connector

`DELETE /api/v1/connectors/{id}` deletes the `datastores` row and calls `SecretStore.delete()` to remove the encrypted blob. The `connector_secrets` table also has a `REFERENCES datastores(id) ON DELETE CASCADE` constraint so a direct database-level delete removes the secret row automatically.

### Test Probe

`POST /api/v1/connectors/{id}/test` is a **structural check** — no network socket is opened. It verifies:

1. The `datastores` row exists and has a `connector_type` (config layer).
2. The encrypted secret can be retrieved and decrypted without error (secret layer).

Response:
```json
{ "ok": true, "layers": { "config": true, "secret": true } }
```

---

## Network Mode and Bridge Security

Connectors that live inside a private network expose two optional config fields:

| Field | Type | Meaning |
|-------|------|---------|
| `network_mode` | `string` | `"direct"` (default) or `"bridge"` |
| `bridge_id` | `string (uuid)` | Reference to the Nubi bridge agent that proxies traffic to the private host |

When `network_mode="bridge"`, the query executor routes the connection through the bridge agent WebSocket tunnel identified by `bridge_id`. The bridge ID is non-sensitive and is stored in `datastores.config` (not in secrets). The bridge agent itself authenticates using a `token` stored in the bridge's config (not in `connector_secrets`). See [Bridges](/docs/bridges) for full details.

---

## Security Properties

| Property | How it is enforced |
|----------|--------------------|
| Secrets never in `datastores.config` | Pydantic validator (create/update) + `_assert_no_secret_leakage()` assertion |
| Secrets never in API responses | `_sanitise()` strips all secret keys before returning; internal assertion double-checks |
| Secrets encrypted at rest | AES-256-GCM in the application layer; ciphertext in `connector_secrets` |
| Nonce uniqueness | 12-byte random nonce per encryption call (NIST SP 800-38D) |
| Tampering detection | GCM authenticated tag — any mutation of the ciphertext causes decryption to fail |
| Master key not in DB | Key lives in env only (`CONNECTOR_SECRET_KEY` in `.env.main`) |
| Cross-org isolation | All endpoints filter by `org_id`; wrong-org access returns 404 |
| Secret removal on delete | `SecretStore.delete()` called; DB cascade removes `connector_secrets` row |
| Key rotation | `key_version` column + `CONNECTOR_SECRET_KEYS` multi-key registry supports zero-downtime rotation |
