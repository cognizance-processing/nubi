# Embedding dashboards

Embedding is Nubi's primary integration surface. Drop `<nubi-dashboard>` into any page in your own application; your auth system decides who sees what. The component fetches results as Arrow IPC, and the Nubi backend enforces row-level security via a short-lived JWT that **your** backend signs — so there is no per-viewer server kernel and no Nubi seat to provision for each end user.

![Your backend signs a short-lived JWT; the component fetches Arrow IPC data scoped to each viewer](illustration:EmbedAuth)

---

## How it fits together

1. A viewer loads a page in **your** app that contains `<nubi-dashboard>`.
2. The element calls your `getToken()` function to obtain a fresh embed JWT.
3. Your backend authenticates the viewer (your session or SSO) and signs a short-lived **RS256 or ES256 JWT** with your private key. The token carries the viewer's org, scope, and per-viewer RLS policies.
4. The element posts that token to the Nubi query API. Nubi verifies the signature against the **JWKS** you registered, checks audience, expiry, and origin, then injects the viewer's `policies` as AST-level `WHERE` predicates before the query runs.
5. Results stream back as Arrow IPC and render in the browser.

Because your private key never leaves your infrastructure, Nubi only ever holds the public half. There is no shared secret to leak.

---

## Step 1 — Register your signing key

Nubi needs your **public** key to verify embed tokens. Register it once as a *JWT issuer*.

### In the UI

1. Open **Settings → Security** (`/settings/security`).
2. Under **JWT issuers**, click **Add issuer** and fill in:

| Field | Description |
|-------|-------------|
| **Name** | A human-readable label, e.g. `Production web app`. |
| **Issuer (`iss` claim)** | The exact string your tokens put in `iss`, e.g. `https://app.yourcompany.com`. |
| **JWKS URL** *(recommended)* | `https://app.yourcompany.com/.well-known/jwks.json` — Nubi fetches and caches it and picks up key rotations automatically. |
| **Or paste a static JWKS** | A full `{"keys": [...]}` JSON object if you don't host a JWKS endpoint. |
| **Algorithms** | Defaults to `RS256`. `ES256` and the longer `RS384/RS512`, `ES384/ES512` variants are also supported. |
| **Audience (`aud` claim)** | Expected `aud` value, e.g. `nubi:your-project-id`. |

3. Save. Issuers are org-scoped and take effect immediately — no server restart needed.

You can toggle an issuer **enabled/disabled** at any time. Tokens with a disabled `iss` are rejected immediately.

### Via the management API

```json
POST /api/v1/security/jwt-issuers
Authorization: Bearer <your-access-token>
Content-Type: application/json

{
  "name":       "Production web app",
  "issuer":     "https://app.yourcompany.com",
  "audience":   "nubi:your-project-id",
  "jwks_url":   "https://app.yourcompany.com/.well-known/jwks.json",
  "algorithms": ["RS256"],
  "enabled":    true
}
```

Either `jwks_url` or `static_jwks_json` must be provided. The matching `GET`, `PUT` (partial update), and `DELETE` routes under `/api/v1/security/jwt-issuers/{id}` let you manage individual issuers.

> Embed tokens must use an **asymmetric** algorithm (RS256 or ES256). Nubi rejects `alg: none` and blocks HS256 on the embed path entirely — this prevents algorithm-confusion attacks. Your private key never leaves your infrastructure.

---

## Step 2 — Mint short-lived embed tokens

The component calls a `getToken()` function you expose on `window` before each query. That function should call a small endpoint on **your** backend that authenticates the viewer and returns a freshly signed JWT.

### JWT claims contract

```json
{
  "iss":          "https://app.yourcompany.com",
  "sub":          "viewer-or-session-id",
  "aud":          "nubi:your-project-id",
  "org":          "your-nubi-org-id",
  "project":      "your-project-slug",
  "roles":        ["viewer"],
  "scope":        ["read:dashboard:*"],
  "policies":     { "tenant_id": "acme" },
  "embed_origin": "https://app.yourcompany.com",
  "iat":          1749470000,
  "exp":          1749470900
}
```

| Claim | Required | Purpose |
|-------|----------|---------|
| `iss` | Yes | Must match a registered issuer exactly. |
| `sub` | Yes | The viewer or session identifier. |
| `aud` | Yes | Must match the issuer's configured audience. |
| `exp` | Yes | Expiry. **Keep it short — 15 minutes or less.** Missing `exp` is rejected. |
| `org` | Yes for embed | Used directly as the org for data and RLS scoping. |
| `scope` | Yes | Must grant a read scope — `read:*`, `read:query`, or `read:dashboard:*`. |
| `policies` | For RLS | Per-viewer row-level-security predicates (see Step 3). |
| `embed_origin` | Recommended | Pins the token to one browser origin (see Step 4). |
| `roles`, `project` | Optional | Carried through to the verified identity. |

> Embed tokens are bearer credentials. Keep lifetimes at 15 minutes or less and always set `embed_origin`. The reference `getToken` helper refreshes ~60 seconds before expiry so viewers never see a stale-token error mid-session.

### Minting on your backend

**Node.js / JavaScript**

```js
import jwt from 'jsonwebtoken'
import fs from 'node:fs'

const PRIVATE_KEY = fs.readFileSync('./keys/embed.pem')  // RS256 private key

export function mintEmbedToken({ userId, tenantId, org }) {
  const now = Math.floor(Date.now() / 1000)
  return jwt.sign(
    {
      iss:          'https://app.yourcompany.com',
      sub:          userId,
      aud:          'nubi:your-project-id',
      org,
      roles:        ['viewer'],
      scope:        ['read:dashboard:*'],
      policies:     { tenant_id: tenantId },
      embed_origin: 'https://app.yourcompany.com',
      iat:          now,
      exp:          now + 900,  // 15 minutes
    },
    PRIVATE_KEY,
    { algorithm: 'RS256' }
  )
}
```

**Python**

```python
import time
from pathlib import Path
import jwt  # PyJWT

PRIVATE_KEY = Path("keys/embed.pem").read_text()

def mint_embed_token(user_id: str, tenant_id: str, org: str) -> str:
    now = int(time.time())
    payload = {
        "iss":          "https://app.yourcompany.com",
        "sub":          user_id,
        "aud":          "nubi:your-project-id",
        "org":          org,
        "roles":        ["viewer"],
        "scope":        ["read:dashboard:*"],
        "policies":     {"tenant_id": tenant_id},
        "embed_origin": "https://app.yourcompany.com",
        "iat":          now,
        "exp":          now + 900,  # 15 minutes
    }
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")
```

Your mint endpoint should authenticate the incoming request with your own session or OAuth system, build claims, sign, and return `{ "token": "<jwt>" }`.

### Wiring the token function on your page

The bundled reference helper caches the token in memory and refreshes it proactively:

```js
import { createGetToken } from './getToken.reference.js'

window.getEmbedToken = createGetToken({
  mintUrl:      '/api/embed-token',           // your backend signs the JWT
  fetchOptions: { credentials: 'include' },   // send your own session cookie
})
```

`createGetToken` handles:
- In-memory caching (never written to `localStorage` or `sessionStorage`).
- Deduplication of concurrent calls during a mint.
- Proactive refresh ~60 seconds before `exp`.

The helper accepts `{ token }`, `{ access_token }`, or a plain JWT string in the mint response.

### Local development shortcut

For local demos where you don't want to stand up signing infrastructure, the backend can mint a first-party token for you:

```json
POST /api/v1/embed/embed-token
Content-Type: application/json

{ "org": "demo-org", "policies": { "tenant_id": "acme" }, "scope": ["read:*"] }
```

Returns `{ "token": "<jwt>", "expires_in": <seconds> }`.

> This endpoint is **disabled by default** and only activates when `EMBED_DEV_TOKEN_ENABLED=true` is set in the backend environment. It mints an HS256 token using the backend's own secret — not your asymmetric key — and must **never** be enabled in production. Real production embeds always use your RS256/ES256 key registered via the issuer UI.

---

## Step 3 — Row-level security via `policies`

Every viewer's token carries a `policies` object. Nubi treats it as the authoritative data boundary for that viewer:

```json
"policies": { "tenant_id": "acme", "region": "EMEA" }
```

When a query runs, Nubi:

1. Verifies the JWT signature against your registered JWKS.
2. Reads `policies` **from the verified token only** — any `policies` sent in the request body is silently ignored.
3. Injects them as AST-level `WHERE` predicates before the query reaches your warehouse. They are never string-concatenated into SQL.

Two viewers with `tenant_id: "acme"` and `tenant_id: "globex"` receive different row sets and never share a cache slot — the content-addressed cache key includes the RLS claims.

> Per-viewer `policies` (row-level security) is available on **Team and above**. On the Starter plan, design each registered query to be scoped to a single tenant, or upgrade for policy-based isolation.

---

## Step 4 — Embed token restrictions

Embed tokens are deliberately more constrained than first-party tokens.

| Restriction | What it means |
|-------------|---------------|
| **Registered queries only** | An embed token **must** reference a server-registered query by `query_id`. Any `sql` field in the request body is ignored; a request without a `query_id` is rejected with `query_not_registered` (403). Register the queries you want to expose first (see [Queries & Parameters](/docs/queries-and-params)). |
| **Read scope required** | The `scope` claim must grant a read scope: `read:*`, `read:query`, or `read:dashboard:*`. Requests without it return `insufficient_scope` (403). |
| **Origin pinning** | If the token carries `embed_origin`, the request's `Origin` header must match it exactly. A missing `Origin` (e.g. a server-side or scripted call) also fails — the token is bound to one specific browser origin. Mismatch returns `origin_mismatch` (403). |
| **No compute or AI** | Embed tokens are read-only. They cannot invoke server kernels or AI generation endpoints. |

Because raw SQL is blocked on the embed path, the safe exposure surface is your registered query library. Combine a registered query with token-supplied `policies` and you get a fixed, auditable query whose rows are scoped per viewer.

---

## Step 5 — Mount the component

### Drop-in HTML

```html
<!-- 1. Load the bundle (UMD — registers <nubi-dashboard> automatically) -->
<script src="https://cdn.nubi.dev/embed/nubi-dashboard.js"></script>

<!-- Or use the ES module build -->
<script type="module" src="https://cdn.nubi.dev/embed/nubi-dashboard.es.js"></script>

<!-- 2. Set up the getToken helper before the element renders -->
<script type="module">
  import { createGetToken } from '/js/getToken.reference.js'
  window.getEmbedToken = createGetToken({
    mintUrl:      '/api/embed-token',
    fetchOptions: { credentials: 'include' },
  })
</script>

<!-- 3. Mount the element -->
<nubi-dashboard
  query="revenue_by_region"
  get-token="getEmbedToken"
  backend="https://api.yourcompany.com"
  style="display:block; height:360px;">
</nubi-dashboard>
```

The element resolves a token, calls the query API, parses the Arrow IPC response, and renders a table. It de-bounces re-renders and aborts in-flight fetches when attributes change.

### Observed attributes

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | A registered `query_id` (required for embed tokens) or, for first-party tokens, a SQL string. |
| `token` | One of | A static JWT string. |
| `get-token` | One of | The **name** of a `window` function returning `Promise<string>` (or a string). Called before each query. |
| `backend` | No | Nubi API base URL. Defaults to `http://localhost:8000`. |
| `theme` | No | Reserved preset name; theming is done via CSS custom properties. |

`token` and `get-token` are mutually exclusive. For any production embed, use `get-token` so tokens are refreshed automatically.

### Events

All events bubble and are `composed: true` (crossing Shadow DOM boundaries).

| Event | `detail` | Fired when |
|-------|----------|------------|
| `nubi:ready` | `{ rowCount }` | After a successful render (real data or sample fallback). |
| `nubi:query-run` | `{ rowCount, cacheStatus, elapsedMs, sample }` | After each query attempt. |
| `nubi:error` | `{ message }` | On any non-recoverable error (before falling back to sample). |

```js
document.querySelector('nubi-dashboard')
  .addEventListener('nubi:error', e => console.warn('Embed error:', e.detail.message))
```

### Theming

The component renders inside a Shadow DOM with a dark default palette. Override via CSS custom properties on any ancestor or `:root`:

```css
nubi-dashboard {
  --nubi-bg:     #0f1117;   /* table background */
  --nubi-fg:     #e2e8f0;   /* text colour      */
  --nubi-accent: #1e2433;   /* header row       */
  --nubi-border: #2d3748;   /* cell borders     */
}
```

### Sample fallback

If anything fails — network down, auth rejected, parse error — the element still renders a small built-in sample table with a "preview (sample data)" banner. The `nubi:error` event is still fired so your app can log the real cause. This means demo pages always render something visible.

### Programmatic mount via the SDK

```js
import { createNubiClient } from '@nubi/sdk'

const client = createNubiClient({
  baseUrl:  'https://api.yourcompany.com',
  getToken: async () => fetch('/api/embed-token').then(r => r.json()).then(d => d.token),
})

const { unmount } = client.embed.mount(
  document.getElementById('dashboard-root'),
  { query: 'revenue_by_region' }
)

// Tear down when navigating away:
unmount()
```

`embed.mount` still requires the `nubi-dashboard` bundle to be loaded on the page so the custom element is defined.

### Loading a saved dashboard by ID

To embed a dashboard you built in the Nubi editor, fetch its descriptor with an embed token:

```
GET /api/v1/embed/config/{dashboard_id}
Authorization: Bearer <embed-jwt>
```

The response carries `dashboard_id`, `title`, `widgets`, and optionally `spec`, `html`, and `theme`. Hand these to your renderer, or use `client.embed.mount` which calls this endpoint automatically when you pass a `dashboardId`.

---

## White-label options by plan

| Plan | What you get |
|------|--------------|
| **Starter** | Embedding enabled; embeds carry a small Nubi attribution badge. |
| **Team** | Remove the badge; unlock per-viewer RLS via `policies`. |
| **Pro** | Full white-labelling including custom domain for embed requests. |
| **Enterprise** | Fully customisable SDK build and unlimited embedded sessions. |

Embedded "sessions" are dashboard loads per month, each plan includes a monthly allowance, and overages are billed at the plan rate. See [Billing & usage](/docs/billing-and-usage) for per-plan session counts and overage rates.

---

## Security checklist

Use this before shipping an embed to production.

| Check | Why it matters |
|-------|---------------|
| **Sign only on your backend** | Your RS256/ES256 private key must never reach the browser. Browser-side signing lets any viewer forge arbitrary RLS policies. |
| **Token lifetime ≤ 15 minutes** | Embed tokens are bearer credentials. A short `exp` limits the blast radius if a token leaks. The `createGetToken` helper handles transparent refresh. |
| **Set `embed_origin`** | Pins the token to one browser origin. A mismatch — including a missing `Origin` header from a non-browser client — returns 403. This prevents token reuse from unexpected origins. |
| **Register only queries you intend to expose** | The embed path rejects raw SQL; only registered `query_id` values are callable. Keep your query registry audited. |
| **Use JWKS URL, not a static key** | A JWKS URL allows zero-downtime key rotation. With a static key, rotation requires a UI update and a cache flush. |
| **Disable `EMBED_DEV_TOKEN_ENABLED` in production** | The dev mint endpoint skips asymmetric signing and must not be reachable from public traffic. |
| **`alg: none` and HS256 are already blocked** | The Nubi backend rejects algorithm confusion by design — but verify you are not serving HS256-signed tokens from your own mint endpoint to the embed path. |
| **`policies` cannot be overridden by the request body** | Nubi reads RLS claims from the verified token only. But you should still verify on your side that your mint endpoint builds `policies` from authoritative server-side state, not from viewer-supplied input. |
| **Rotate your signing key periodically** | Use a JWKS URL with a `kid` in each key. Nubi's JWKS cache picks up the new key within its TTL; you can force an immediate flush by bumping the issuer in Settings. |

---

## Related

- [Queries & Parameters](/docs/queries-and-params) — register the queries you expose to embeds.
- [Dashboards](/docs/dashboards) — build the boards you embed by ID.
- [Connector Security](/docs/connector-security) — how warehouse credentials are protected.
- [Organization Settings](/docs/organization-settings) — manage JWT issuers and org-level security.
