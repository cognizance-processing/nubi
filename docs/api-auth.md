# Authentication & access

> Part of the [API reference](/docs/api-reference) — see it for conventions, error codes, and the audit log.

## Authentication

All endpoints require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

Three token kinds are accepted:

| Kind | Prefix / format | Issued by | Use case |
|---|---|---|---|
| **First-party access token** | short-lived JWT (HS256) | `POST /auth/login` or Google OAuth | Interactive users, CLI, MCP server, AI agents |
| **API key** | `nubi_ak_…` (long-lived) | `POST /auth/api-keys` | CLI in CI, automation — works anywhere a Bearer token does |
| **Embed JWT** | RS256/ES256 | Your backend (registered issuer) | Embedded dashboards, per-viewer RLS |

Embed tokens are read-only and can only reference registered queries by
`query_id` — raw SQL is blocked on the embed path.

### `POST /auth/login`

Authenticate with email + password. Uses a constant-time, enumeration-safe
comparison. Also sets an HTTP-only refresh cookie.

**Body:** `{ "email", "password" }`
**Response `200`:** `{ "user": { id, email, name, ... }, "access_token": "<jwt>" }`
**Errors:** `401 invalid_credentials` — unknown user or wrong password (same error for both).

### `POST /auth/register`

Create a new user account. **Response `201`.**

### `POST /auth/refresh`

Exchange the refresh cookie for a fresh `access_token`.

### `POST /auth/logout`

Revoke the refresh session. **Response `204`.**

### `GET /auth/me`

Return the currently authenticated user. **Response `200`:** `{ "user": { ... } }`.

### `GET /auth/config`

Public auth configuration (e.g. whether Google OAuth is enabled). Used by the
login UI.

### Google OAuth

`GET /auth/google/start` redirects to Google; `GET /auth/google/callback`
completes the exchange and issues a first-party token.

### API keys

Long-lived `nubi_ak_…` keys — the right way to authenticate the CLI in CI.

#### `POST /auth/api-keys`

Mint a key scoped to the caller's default org. The raw key is returned **once**
in `key` and is never retrievable again (only a hash + last four are stored).

**Body:** `{ "name": "GitHub Actions" }`
**Response `201`:** `{ "key": "nubi_ak_…", "api_key": { "id", "name", "last_four", "created_at", ... } }`

#### `GET /auth/api-keys`

List the caller's keys. **Response `200`:** `{ "api_keys": [{ id, name, last_four, created_at, last_used_at, revoked_at }] }`. Key material is never returned.

#### `DELETE /auth/api-keys/{key_id}`

Revoke a key. Cross-user/cross-org keys return **404** (never 403). **Response `204`.**

---

## Scope & access grants

### `GET /auth/scope`

Resolve the caller's effective RLS scope from the **verified token** (works for
first-party **and** embed tokens). Hosts call this to discover what a token is
authorised to see, without re-deriving it themselves.

**Auth:** Any valid token.

**Response `200`:**
```json
{
  "org": "<org_id>",
  "scope": ["read:*"],
  "policies": { "region": "Gauteng" },
  "effective_policies": { "region": ["Gauteng", "JHB", "PTA"] },
  "expanded": true
}
```

- `policies` — raw policy claim from the verified token.
- `effective_policies` — hierarchy-expanded **and** merged with any non-expired
  `access_grants` for the caller's subject, normalised to `{dimension: [values]}`.
- `expanded` — `true` when `effective_policies` differs from `policies`.

Policies/org come from the token only (a request body is ignored). Resolution is
org-scoped and **fails closed** — on error it returns the narrower raw policies,
never a widened set.

---

### `GET /access-grants`

List grants for a subject in the caller's org.

**Auth:** Any org member. **Query:** `subject_type` (`user`|`role`|`embed_sub`),
`subject_id`.

**Response `200`:** `{ "grants": [{ id, subject_type, subject_id, dimension, value, expires_at, created_at }] }`

### `POST /access-grants`

Create (or refresh) a grant. **Auth:** owner/admin only.

**Body:** `{ "subject_type", "subject_id", "dimension", "value", "expires_at"? }`
**Response `201`:** `{ "grant": { ... } }`

### `DELETE /access-grants/{id}`

Delete a grant within the caller's org. **Auth:** owner/admin only.
A grant id belonging to another org (or absent) returns **404** (not 403).
**Response:** `204`.

> Grants are org-scoped and merged into `GET /auth/scope`'s `effective_policies`.
> See [governance.md](./governance.md) for the cardinality cap
> (`NUBI_RLS_MAX_POLICY_VALUES`, default 5000) that fails closed on oversized
> policies.

---
