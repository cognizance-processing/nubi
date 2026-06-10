# Nubi Embed Demo — Per-Tenant RLS Isolation

End-to-end demo of the Nubi embed story. Two tenants share the same backend
but see only their own rows, isolated server-side by JWT `policies.tenant_id`
claims. Framework-free — plain HTML and vanilla JS.

Token minting is done by the **backend** (`POST /api/v1/embed-token`), not in
the browser. The private signing key never leaves the server. This is the
correct production pattern; the old WebCrypto in-browser keypair approach has
been replaced.

---

## What this demo proves

| Property | How it is demonstrated |
|---|---|
| **Embed JWT minting** | `POST /api/v1/embed-token` on the backend mints an RS256 JWT with `policies.tenant_id = acme-corp` or `globex-inc`. Private key stays server-side. |
| **Per-tenant RLS** | Backend injects `WHERE tenant_id='<value>'` from the token — neither the component nor the browser body can override it. |
| **Origin pinning** | Token's `embed_origin` must match the request `Origin` header. The backend rejects mismatches with `403 origin_mismatch`. |
| **Token lifecycle** | `getToken()` caches the JWT in memory and refreshes ~60 s before expiry. The component calls it before every query. |
| **Zero-framework** | `index.html` is a standalone file with no build step required on the host side. |

---

## Quick start

### 1. Enable the dev token endpoint on the backend

The `POST /api/v1/embed-token` endpoint is gated by an environment variable.
Set it before starting the backend:

```bash
export EMBED_DEV_TOKEN_ENABLED=true
```

> **Important:** This endpoint is for local development and testing ONLY. It
> must **not** be enabled in production. It mints tokens using a dev signing
> key without requiring host-app authentication.

### 2. Start the Nubi backend

```bash
# From repo root
cd /path/to/nubi

# Create .env (copy from .env.example and fill in DATABASE_URL + JWT_SECRET)
cp .env.example .env

# Add the dev token flag to .env (or export it in the shell as above)
echo "EMBED_DEV_TOKEN_ENABLED=true" >> .env

# Start the API server
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Verify the endpoint is live:

```bash
curl -s -X POST http://localhost:8000/api/v1/embed-token \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"acme-corp","org":"demo-org","scope":["read:*"]}' | jq .
# Expected: { "token": "<signed-jwt>" }
```

### 3. Seed demo data with tenant isolation

The demo queries the `demo` connector table. For RLS to produce per-tenant
rows you need data with a `tenant_id` column. The seed script at
`backend/seed_data_duckdb.py` already creates this — or add rows manually:

```sql
-- Acme Corp rows
INSERT INTO demo VALUES ('1', 'acme-corp', 'Widget A', 120.00, true, 'hardware');
INSERT INTO demo VALUES ('2', 'acme-corp', 'Widget B',  80.00, true, 'software');

-- Globex Inc rows
INSERT INTO demo VALUES ('3', 'globex-inc', 'Gizmo X', 450.00, true, 'hardware');
INSERT INTO demo VALUES ('4', 'globex-inc', 'Gizmo Y', 300.00, false, 'services');
```

### 4. Build the embed bundle

```bash
# From repo root
npm install
npm run build:embed
# Output: dist-embed/nubi-dashboard.js
```

### 5. Serve the demo page

The demo page loads the embed bundle from `../../dist-embed/nubi-dashboard.js`
(relative to `examples/embed-demo/`). Serve from any static server:

```bash
# Python (from repo root — serves the whole project)
python -m http.server 8080

# Then open:
open http://localhost:8080/examples/embed-demo/index.html
```

The page defaults to `http://localhost:8000` as the backend URL. You can
override it via the `?backend=` query parameter:

```
http://localhost:8080/examples/embed-demo/index.html?backend=http://localhost:8000
```

### 6. Use the tenant switcher

Click **Acme Corp** or **Globex Inc** to switch tenants. Each switch:
1. Clears the cached token.
2. Calls `POST /api/v1/embed-token` with the new `tenant_id`.
3. Forces the `<nubi-dashboard>` component to re-query.
4. The JWT inspector panel shows the decoded token claims (signature not
   verified in the browser — the backend verifies it on every request).

---

## Backend endpoint contract

```
POST /api/v1/embed-token
Content-Type: application/json

{
  "tenant_id": "acme-corp",        // required — policies.tenant_id value
  "org":       "demo-org",         // required — Nubi org slug
  "scope":     ["read:*"]          // optional — defaults to ["read:*"]
}

→ 200 OK
{
  "token": "<compact-RS256-JWT>"
}

→ 503 Service Unavailable   if EMBED_DEV_TOKEN_ENABLED is not set
→ 422 Unprocessable Entity  if required fields are missing
```

The returned JWT contains:
- `iss` — backend-configured issuer
- `sub` — `embed-dev-{tenant_id}`
- `aud` — `"nubi"`
- `org` — org slug from the request body
- `policies` — `{ "tenant_id": "<value>" }`
- `embed_origin` — the request `Origin` header (pinned server-side)
- `scope` — from the request body
- `roles` — `["viewer"]`
- `iat`, `exp` — issued-at and expiry (15 minutes)

---

## Production wiring

In production, replace `POST /api/v1/embed-token` with your own server-side
mint endpoint. The pattern is identical — your server authenticates the
caller, looks up which tenant they are allowed to see, signs a JWT with your
private RSA key, and returns `{ token }`.

### Option A: Python signer (scripts/sign_embed_jwt.py)

```bash
python scripts/sign_embed_jwt.py \
    --tenant acme-corp \
    --org    my-org \
    --iss    https://myapp.example.com \
    --aud    nubi \
    --origin https://myapp.example.com \
    --ttl    900
```

### Option B: Node.js mint endpoint

```js
// pages/api/embed-token.js (Next.js example)
import { SignJWT, importPKCS8 } from 'jose'
import fs from 'fs'

const PRIVATE_KEY_PEM = fs.readFileSync(process.env.EMBED_PRIVATE_KEY_PATH, 'utf8')

export default async function handler(req, res) {
  const session = await getSession(req)
  if (!session) return res.status(401).json({ error: 'unauthenticated' })

  const { tenant_id } = req.body
  if (!isAuthorizedForTenant(session.user, tenant_id)) {
    return res.status(403).json({ error: 'forbidden' })
  }

  const privateKey = await importPKCS8(PRIVATE_KEY_PEM, 'RS256')
  const token = await new SignJWT({
    org:          process.env.NUBI_ORG,
    roles:        ['viewer'],
    scope:        ['read:*'],
    policies:     { tenant_id },
    embed_origin: process.env.EMBED_ORIGIN,
  })
    .setProtectedHeader({ alg: 'RS256', kid: 'embed-key-1' })
    .setIssuer(process.env.EMBED_ISS)
    .setAudience('nubi')
    .setSubject(session.user.id)
    .setIssuedAt()
    .setExpirationTime('15m')
    .sign(privateKey)

  res.json({ token })
}
```

---

## JWT claims reference

The Nubi backend enforces the following claims on every embed token:

| Claim | Required | Description |
|---|---|---|
| `iss` | Yes | Issuer URI — must be registered in the backend issuer registry. |
| `sub` | Yes | End-user or service-account identifier. |
| `aud` | Yes | Audience — must match the registered issuer config's `aud`. |
| `exp` | Yes | Expiry timestamp (Unix seconds). Backend rejects expired tokens. |
| `iat` | Recommended | Issued-at timestamp. |
| `org` | Yes | Nubi org slug — identifies which org's data is queried. |
| `scope` | Yes | Must include `read:*` or `read:query` for query access. |
| `policies` | Recommended | Object of RLS column→value pairs (e.g. `{"tenant_id": "acme"}`). |
| `embed_origin` | Recommended | Exact `Origin` header the embed is served from. Backend enforces this. |
| `roles` | Optional | Role strings (e.g. `["viewer"]`). |

### RLS enforcement

`policies` is the mechanism for per-tenant row-level security. The Nubi planner
injects each key→value pair as a SQL predicate:

```sql
-- policies = {"tenant_id": "acme-corp"}
-- Original:  SELECT * FROM sales
-- Planned:   SELECT * FROM sales WHERE tenant_id = 'acme-corp'
```

The body of the query request CANNOT override `policies`. Even if an attacker
sends `body.claims.policies = { tenant_id: "other-tenant" }`, the backend always
uses the token's `policies`. This is a hard security property — see
`backend/tests/test_embed_rls.py` test `test_body_claims_policies_are_ignored`.

---

## Security checklist

- [ ] `EMBED_DEV_TOKEN_ENABLED` is NOT set in production.
- [ ] Private key is stored only on your server (never in browser, .env repo, etc.).
- [ ] `embed_origin` is set to the exact origin that hosts the embed.
- [ ] Mint endpoint is authenticated — only signed-in users get tokens for their tenant.
- [ ] Token TTL is 15 minutes or less (`exp <= now + 900`).
- [ ] JWKS endpoint is HTTPS with a valid cert (no self-signed in production).
- [ ] Backend CORS is configured to accept requests only from your embed origin.

---

## File layout

```
examples/embed-demo/
├── index.html      Host page with tenant switcher + live JWT inspector (needs the backend)
├── demo-mock.html  Zero-setup variant: mock token, built-in sample data, no backend
└── README.md       This file

scripts/
└── sign_embed_jwt.py   Server-side Python JWT signer + JWKS generator
```
