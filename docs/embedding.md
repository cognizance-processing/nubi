# Embedding dashboards

Embedding is Nubi's primary integration surface. Drop a `<nubi-dashboard>` tag
(or an iframe) into any page of your own application and it renders a live,
interactive Nubi board — while **your** auth system decides who sees what. The
component fetches results as Arrow IPC, and the Nubi backend enforces row-level
security using a short-lived JWT that **your** backend signs. There is no
per-viewer server kernel and no Nubi seat to provision for each of your end users.

![Your backend signs a short-lived JWT; the component fetches Arrow IPC data scoped to each viewer](illustration:EmbedAuth)

This guide has four parts:

1. [Embed a dashboard in 5 minutes](#embed-a-dashboard-in-5-minutes) — the happy path.
2. [Auth & row-level security](#auth--row-level-security) — the important part: signed embed tokens and per-viewer RLS.
3. [Embedding modes](#embedding-modes) — live, frozen snapshot, or public export.
4. [Customization](#customization) — theming, parameters, sizing, events.

For the precise, versioned field/endpoint/error reference, see the
[Embed API reference](/docs/embed-api).

---

## Embed a dashboard in 5 minutes

The fastest path from a saved board to a working embed. You will publish a board,
copy an embed snippet, drop it into a host page, and see it render.

### 1. Build (or open) the board you want to embed

Create a dashboard in the editor and open its viewer at `/d/{board_id}`. Any
saved board can be embedded by its id.

![A saved Nubi dashboard in the viewer — this is what you will embed](/docs/screenshots/dashboard-view.webp)

### 2. Get the embed snippet

Ask Nubi for the embed descriptor for that board. This does **not** return a
token — Nubi never signs embed tokens (see [Auth & RLS](#auth--row-level-security)) —
it returns the ready-to-paste snippet plus the exact claim shape your backend
must sign.

```http
POST /api/v1/boards/{board_id}/share
Authorization: Bearer <your-first-party-access-token>
```

The response:

```jsonc
{
  "board_id": "brd_123",
  "title": "Revenue by region",
  "embed_url": "https://your-host-page.example.com/d/brd_123",
  "config_endpoint": "/api/v1/embed/config/brd_123",
  "snippet": "<script src=\"https://cdn.example.com/dist-embed/nubi-dashboard.js\"></script>\n<nubi-dashboard\n  dashboard-id=\"brd_123\"\n  get-token=\"getEmbedToken\"\n  backend=\"https://api.example.com\"\n  style=\"display:block; height:600px;\">\n</nubi-dashboard>",
  "mint": {
    "token": null,                       // host-minted only — Nubi never signs embed JWTs
    "algorithm": "RS256 | ES256",
    "max_ttl_minutes": 15,
    "required_claims": {
      "iss": "https://your-app.example.com",
      "sub": "user-or-service-id",
      "aud": "nubi:your-project-id",
      "org": "your-org-slug",
      "scope": ["read:dashboard:*"],
      "policies": { "tenant_id": "<viewer-tenant>" },
      "embed_origin": "https://your-host-page.example.com",
      "exp": "<= iat + 15m"
    }
  },
  "rls": { "...": "the row-level-security / trust-boundary summary" }
}
```

`mint.token` is always `null` — that is by design. Only your backend can produce
a signed token, because only your backend holds the private key that authors the
per-viewer `policies` claim.

![The Share dialog surfaces the snippet and the exact claims your backend must sign](/docs/screenshots/NEW-share-dialog.webp)

### 3. Drop the tag into your host page

Paste the snippet from `snippet` into your page. The bundle registers the
`<nubi-dashboard>` custom element automatically:

```html
<!-- 1. Load the bundle (UMD — registers <nubi-dashboard> automatically) -->
<script src="https://cdn.example.com/dist-embed/nubi-dashboard.js"></script>

<!-- 2. Mount the element -->
<nubi-dashboard
  dashboard-id="brd_123"
  get-token="getEmbedToken"
  backend="https://api.example.com"
  style="display:block; height:600px;">
</nubi-dashboard>
```

The element fetches the read-only descriptor from
`GET /api/v1/embed/config/{dashboard_id}`, runs each widget's registered query,
parses the Arrow IPC response, and renders. It de-bounces re-renders and aborts
in-flight fetches when attributes change.

> Prefer an iframe? `POST /boards/{id}/share` also returns `embed_url`
> (`/d/{board_id}`). You can point an `<iframe>` at it instead of using the web
> component — see [Sizing & responsive](#sizing--responsive).

### 4. It renders

![A Nubi dashboard rendered inside a host application page](/docs/screenshots/NEW-embedded-dashboard.webp)

If anything fails — the backend is unreachable, no token is wired up, auth is
rejected — the element still renders a small built-in **sample table** with a
"preview (sample data)" badge, and fires a `nubi:error` event so your app can log
the real cause. This means a demo page always shows *something*.

To render **real, per-viewer data** you need one more thing: a signed embed
token. That is the next section — and it is the part that matters.

---

## Auth & row-level security

This is the heart of a production embed. Get it right and every viewer sees
exactly their rows; get it wrong and you leak data across tenants.

### Why Nubi never mints your tokens

Your embed tokens carry the `policies` claim — the row-level-security boundary
for a viewer. If Nubi minted tokens, Nubi would be authoring your access rules,
and the browser (which is untrusted) would be in the loop. Instead:

- **Your backend signs** each token with **your private key** (RS256 or ES256).
- **Nubi holds only your public key** (via a JWKS URL you register). There is no
  shared secret to leak.
- **RLS is enforced server-side** in the connector: predicates from the verified
  token's `policies` are injected into the SQL AST before the query reaches your
  warehouse — never string-concatenated, never trusted from the request body,
  never enforced in the browser.

![The browser is untrusted; RLS predicates are injected server-side from the verified token](illustration:TrustBoundary)

### How it fits together

1. A viewer loads a page in **your** app containing `<nubi-dashboard>`.
2. The element calls your `getToken()` function to obtain a fresh embed JWT.
3. Your backend authenticates the viewer (your session or SSO) and signs a
   short-lived **RS256/ES256** JWT carrying the viewer's `org`, `scope`, and
   per-viewer `policies`.
4. The element sends that token to the Nubi query API. Nubi verifies the
   signature against your registered **JWKS**, checks `aud`, `iss`, `exp`, and
   `embed_origin`, then injects `policies` as `WHERE` predicates before the query
   runs.
5. Results stream back as Arrow IPC and render in the browser.

### Register your signing key (JWT issuer)

Nubi needs your **public** key to verify embed tokens. Register it once as a
*JWT issuer*.

**In the UI:** open **Settings → Security** (`/settings/security`), then under
**JWT issuers** click **Add issuer**:

| Field | Description |
|-------|-------------|
| **Name** | A human-readable label, e.g. `Production web app`. |
| **Issuer (`iss`)** | The exact string your tokens put in `iss`, e.g. `https://app.yourcompany.com`. |
| **Audience (`aud`)** | Expected `aud` value, e.g. `nubi:your-project-id`. |
| **JWKS URL** *(recommended)* | `https://app.yourcompany.com/.well-known/jwks.json` — Nubi fetches, caches, and picks up key rotations automatically. |
| **Or paste a static JWKS** | A full `{"keys": [...]}` object if you don't host a JWKS endpoint. |
| **Algorithms** | Defaults to `["RS256"]`; `RS384`, `RS512`, `ES256`, `ES384`, `ES512` are also accepted. |

Issuers are org-scoped and take effect immediately — no restart. You can toggle
an issuer **enabled/disabled** at any time; tokens from a disabled `iss` are
rejected.

**Via the management API:**

```http
POST /api/v1/security/jwt-issuers
Authorization: Bearer <your-first-party-access-token>
Content-Type: application/json
```
```jsonc
{
  "name":       "Production web app",
  "issuer":     "https://app.yourcompany.com",  // exact iss claim
  "audience":   "nubi:your-project-id",         // exact aud claim
  "jwks_url":   "https://app.yourcompany.com/.well-known/jwks.json",
  // OR, instead of jwks_url:
  // "static_jwks_json": { "keys": [ { "kty": "RSA", "kid": "...", "n": "...", "e": "AQAB" } ] },
  "algorithms": ["RS256"],   // optional; defaults to ["RS256"]
  "enabled":    true,        // false = reject all tokens from this issuer
  "host_mode":  false,       // set true for claim-native multi-tenant issuers
  "org_claim":  null         // JWT claim carrying the org id when host_mode is true
}
```

Exactly one of `jwks_url` or `static_jwks_json` is required. The matching `GET`,
`PUT` (partial update), and `DELETE` routes under
`/api/v1/security/jwt-issuers/{issuer_id}` manage individual issuers. After any
mutation the in-process issuer registry is synced immediately.

**Key rotation:** with a `jwks_url` (recommended), rotating your key pair only
requires publishing the new `kid` in your JWKS endpoint — no API call to Nubi.

> Embed tokens **must** use an asymmetric algorithm (RS256/ES256). Nubi rejects
> `alg: none` and blocks HS256 on the embed path entirely — this prevents
> algorithm-confusion attacks. Your private key never leaves your infrastructure.

### The embed JWT claim contract

Your `getToken()` function returns a token your backend signed with these claims.
This is the exact shape Nubi verifies (see `backend/app/auth/verify.py`):

```json
{
  "iss":          "https://app.yourcompany.com",
  "sub":          "viewer-or-session-id",
  "aud":          "nubi:your-project-id",
  "org":          "your-nubi-org-id",
  "scope":        ["read:dashboard:*"],
  "policies":     { "tenant_id": "acme", "region": "EMEA" },
  "embed_origin": "https://app.yourcompany.com",
  "roles":        ["viewer"],
  "project":      "your-project-slug",
  "iat":          1749470000,
  "exp":          1749470900
}
```

| Claim | Required | Purpose |
|-------|----------|---------|
| `iss` | Yes | Must match a registered issuer exactly. Unknown `iss` → `invalid_token` (401). |
| `sub` | Yes | The viewer or session identifier. |
| `aud` | Yes | Must match the issuer's configured audience. |
| `exp` | Yes | Expiry. **Keep it short — 15 minutes or less.** Missing `exp` is rejected. |
| `org` | Yes for embed | Used directly as the org for data and RLS scoping. A non-UUID value is resolved against the org `external_key`. |
| `scope` | Yes | Must grant a read scope — `read:*`, `read:query`, or `read:dashboard:*`. |
| `policies` | For RLS | Per-viewer row-level-security predicates (see below). |
| `embed_origin` | Recommended | Pins the token to one browser origin (see [restrictions](#embed-token-restrictions)). |
| `roles`, `project`, `datastore` | Optional | Carried through to the verified identity. |

> `exp`, `aud`, `iss`, and `sub` are hard-required by the verifier — a token
> missing any of them is rejected with `invalid_token` (401).

### Mint tokens on your backend

The component calls a `getToken()` function you expose on `window`. That function
should hit a small endpoint on **your** backend that authenticates the viewer and
returns a freshly signed JWT.

**Node.js**

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
      scope:        ['read:dashboard:*'],
      policies:     { tenant_id: tenantId },   // authoritative server-side value
      embed_origin: 'https://app.yourcompany.com',
      iat:          now,
      exp:          now + 900,                 // 15 minutes
    },
    PRIVATE_KEY,
    { algorithm: 'RS256' }
  )
}
```

**Python (PyJWT)**

```python
import time
from pathlib import Path
import jwt

PRIVATE_KEY = Path("keys/embed.pem").read_text()

def mint_embed_token(user_id: str, tenant_id: str, org: str) -> str:
    now = int(time.time())
    payload = {
        "iss":          "https://app.yourcompany.com",
        "sub":          user_id,
        "aud":          "nubi:your-project-id",
        "org":          org,
        "scope":        ["read:dashboard:*"],
        "policies":     {"tenant_id": tenant_id},
        "embed_origin": "https://app.yourcompany.com",
        "iat":          now,
        "exp":          now + 900,  # 15 minutes
    }
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")
```

Build `policies` from **authoritative server-side state** (the viewer's session),
never from anything the browser supplied. Your endpoint should authenticate the
request with your own session/OAuth, sign, and return `{ "token": "<jwt>" }`.

### Wire the token function on your page

Nubi ships a reference `getToken` helper (`embed/getToken.reference.js`) that
caches the token in memory and refreshes it proactively:

```js
import { createGetToken } from '/js/getToken.reference.js'

window.getEmbedToken = createGetToken({
  mintUrl:      '/api/embed-token',           // your backend signs the JWT
  fetchOptions: { credentials: 'include' },   // send your own session cookie
})
```

It handles in-memory caching (never `localStorage`/`sessionStorage`),
deduplication of concurrent mints, and proactive refresh ~60 seconds before
`exp`. The helper accepts `{ token }`, `{ access_token }`, or a plain JWT string
in the mint response.

### Row-level security via `policies`

Every viewer's token carries a `policies` object, which Nubi treats as the
authoritative data boundary for that viewer:

```json
"policies": { "tenant_id": "acme", "region": "EMEA" }
```

At query time Nubi:

1. Verifies the JWT signature against your registered JWKS.
2. Reads `policies` **from the verified token only** — any `policies` in the
   request body is ignored.
3. Injects them as AST-level `WHERE` predicates before the query reaches your
   warehouse. They are never string-concatenated into SQL.

Two viewers with `tenant_id: "acme"` and `tenant_id: "globex"` receive different
row sets and never share a cache slot — the content-addressed cache key includes
the RLS claims.

> Per-viewer `policies` is available on **Team and above**. On the Starter plan,
> scope each registered query to a single tenant, or upgrade for policy-based
> isolation.

### Embed token restrictions

Embed tokens are deliberately more constrained than first-party tokens
(enforced in `backend/app/routes/query.py` and `backend/app/auth/verify.py`):

| Restriction | What it means |
|-------------|---------------|
| **Registered queries only** | An embed token **must** reference a server-registered query by `query_id`. Any `sql` in the request body is ignored; a request without a `query_id` is rejected with `query_not_registered` (403). Register the queries you expose first — see [Queries & Parameters](/docs/queries-and-params). |
| **Read scope required** | `scope` must grant `read:*`, `read:query`, or `read:dashboard:*`. Otherwise → `insufficient_scope` (403). |
| **Origin pinning** | If the token carries `embed_origin`, the request `Origin` header must match it exactly. A **missing** `Origin` (server-side or scripted call) also fails — the token is bound to one browser origin. Mismatch → `origin_mismatch` (403). |
| **No compute or AI** | Embed tokens are read-only; they cannot invoke server kernels or AI generation. |

Because raw SQL is blocked on the embed path, your safe exposure surface is your
registered query library. Combine a registered query with token-supplied
`policies` and you get a fixed, auditable query whose rows are scoped per viewer.

### Local development shortcut

For local demos where you don't want to stand up signing infrastructure, the
backend can mint a first-party token for you:

```http
POST /api/v1/embed/embed-token
Content-Type: application/json
```
```json
{ "org": "demo-org", "policies": { "tenant_id": "acme" }, "scope": ["read:*"] }
```

Returns `{ "token": "<jwt>", "expires_in": <seconds> }`.

> This endpoint is **disabled by default** and only activates when
> `EMBED_DEV_TOKEN_ENABLED=true` is set in the backend environment (it also
> hard-refuses when `ENV=production`). It mints an **HS256** token using the
> backend's own secret — not your asymmetric key — and must **never** be enabled
> in production. Real production embeds always use your RS256/ES256 key registered
> via the issuer UI.

---

## Embedding modes

Nubi supports three ways to serve an embedded board, trading freshness and
per-viewer security against cost and reach.

| Mode | Data | Per-viewer RLS | Auth | Turn on |
|------|------|----------------|------|---------|
| **Live embed** | Fresh, queried per view | Yes — injected per token | Signed embed token | Default |
| **Frozen / CDN snapshot** | Point-in-time, no live DB | No — one captured policy view | Signed embed token | `POST /boards/{id}/snapshot` |
| **Public (unsafe) export** | Point-in-time, no live DB | None — fully public | **None** | Off by default; two interlocks |

### Mode 1 — Live embed

The default. The `<nubi-dashboard>` element fetches the descriptor
(`GET /api/v1/embed/config/{id}`) and runs each widget's registered query live,
with the viewer's `policies` applied as RLS predicates. Every load reflects
current warehouse data, isolated per viewer.

Use it when data must be fresh and each viewer sees only their own rows — the
normal multi-tenant SaaS case.

### Mode 2 — Frozen / CDN snapshot

Freeze a board's data into a single read-only DuckDB sidecar artifact, then serve
that instead of querying live:

```http
POST /api/v1/boards/{board_id}/snapshot                    # create a snapshot
POST /api/v1/boards/{board_id}/snapshot?snapshot_id=<id>   # refresh in place
GET  /api/v1/embed/frozen/{dashboard_id}                   # frozen viewer (embed token)
```

The frozen viewer **still requires a verified token** (first-party or embed) and
is metered as an embedded session — it is not public. But the data is frozen
through **one** policy view captured at snapshot time (from the verified token's
`policies`, never a request body); there is **no per-viewer predicate injection**
at render time, because there is no live query.

Use it for expensive dashboards you don't want to recompute on every view,
point-in-time reporting, or resilience when the warehouse is offline. For
per-tenant isolation, capture a separate snapshot per tenant — drive the refresh
through a `snapshot_refresh` flow task with the desired `policies` (a scheduled
tick has no user JWT).

### Mode 3 — Public (unsafe) export

Produce a self-contained static HTML file that loads the frozen snapshot sidecar
from a public URL and queries it client-side with DuckDB-WASM — **no backend, no
auth, no expiry**:

```http
POST /api/v1/boards/{board_id}/export/public
```

> **This artifact is fully public.** Authentication is required to *create* the
> export, but the resulting HTML and the snapshot it links are exposed to anyone
> with the URL — no auth, no expiry, no per-viewer security. The data is frozen
> with the **exporter's** RLS view and shown uniformly to every visitor. Every
> generated page carries a loud red UNSAFE banner and an audit entry.

It is **off by default** and refused with `public_exports_disabled` (403) unless
**both** interlocks are on: the deployment switch `ALLOW_UNSAFE_PUBLIC_EXPORTS`,
**and** the org's `public_exports_enabled` setting. Only export boards whose data
is safe to make completely public — marketing metrics, status pages, and the
like.

---

## Customization

### Mounting the component

```html
<nubi-dashboard
  dashboard-id="brd_123"
  get-token="getEmbedToken"
  backend="https://api.example.com"
  theme="dark"
  style="display:block; height:600px;">
</nubi-dashboard>
```

Observed attributes (`embed/nubi-dashboard.js`):

| Attribute | Required | Description |
|-----------|----------|-------------|
| `dashboard-id` | One of | A saved board id. The element fetches its descriptor from `GET /embed/config/{id}` and renders each widget. |
| `query` | One of | A registered `query_id` (embed tokens) or, for first-party tokens, a SQL string. **Takes precedence over `dashboard-id`** when both are set. |
| `get-token` | One of | The **name** of a `window` function returning `Promise<string>`. Called before each query so short-lived tokens refresh automatically. |
| `token` | One of | A static JWT string. Mutually exclusive with `get-token`. |
| `backend` | No | Nubi API base URL. Defaults to `http://localhost:8000`. |
| `theme` | No | `"dark"` (default) or `"light"`. Fine-grained control is via CSS custom properties. |

For any production embed, use `get-token` (not `token`) so tokens refresh before
they expire.

### Events

All events bubble and are `composed: true` (they cross Shadow DOM boundaries).

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

`<nubi-dashboard>` renders inside a Shadow DOM with a dark default palette.
Override via CSS custom properties on any ancestor or `:root`:

```css
nubi-dashboard {
  --nubi-bg:     #0f1117;   /* table background */
  --nubi-fg:     #e2e8f0;   /* text colour      */
  --nubi-accent: #1e2433;   /* header row       */
  --nubi-border: #2d3748;   /* cell borders     */
}
```

The widget-kit elements (`<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, …) expose
a fuller **25-token** theme contract plus a `theme="light|dark"` attribute — see
the [Theme contract](/docs/embed-api#theme-contract-25-tokens) in the Embed API
reference.

### Variables & parameters

When you embed the saved-board viewer via its URL (`embed_url` = `/d/{board_id}`,
e.g. in an iframe), the board's declared variables can be seeded and locked:

- **URL params** — `?region=EMEA` seeds any declared variable that opts into URL
  binding (`url_bind: true`), and filter changes are written back to the URL
  (shallow replace) so the state is shareable and survives a refresh.
- **Token-locked params** — pass a token on the URL as `?_token=<jwt>` (or
  `?_embed=<jwt>`) whose payload carries `locked_params`. Locked values
  **override** URL params and cannot be changed by filter widgets or URL edits.
  The client decode is trust-on-read only — the server independently verifies the
  signature and re-enforces the lock.

Precedence, highest to lowest: **token-locked params → URL params → spec
defaults**. Use locked params to pin a per-viewer data boundary (e.g. a tenant
filter) the viewer cannot widen.

### Sizing & responsive

The element is a block box — size it with normal CSS:

```html
<nubi-dashboard dashboard-id="brd_123" get-token="getEmbedToken"
  style="display:block; width:100%; height:600px;"></nubi-dashboard>
```

For the iframe route, use a responsive wrapper:

```html
<iframe src="https://your-host.example.com/d/brd_123?region=EMEA"
        style="width:100%; height:640px; border:0;"
        title="Revenue by region"></iframe>
```

### Programmatic mount via the SDK

```js
import { createNubiClient } from '@nubi/sdk'

const client = createNubiClient({
  baseUrl:  'https://api.example.com',
  getToken: async () => fetch('/api/embed-token').then(r => r.json()).then(d => d.token),
})

const { unmount } = client.embed.mount(
  document.getElementById('dashboard-root'),
  { dashboardId: 'brd_123' }
)

// Tear down when navigating away:
unmount()
```

`embed.mount` still requires the `nubi-dashboard` bundle to be loaded on the page
so the custom element is defined. When you pass a `dashboardId` it calls
`GET /api/v1/embed/config/{id}` for you.

### Loading a saved dashboard by ID (manually)

To drive your own renderer, fetch the descriptor directly with an embed token:

```http
GET /api/v1/embed/config/{dashboard_id}
Authorization: Bearer <embed-jwt>
```

The response carries `dashboard_id`, `title`, `widgets`, and optionally `spec`,
`html`, and `theme`.

---

## White-label options by plan

| Plan | What you get |
|------|--------------|
| **Starter** | Embedding enabled; embeds carry a small Nubi attribution badge. |
| **Team** | Remove the badge; unlock per-viewer RLS via `policies`. |
| **Pro** | Full white-labelling including a custom domain for embed requests. |
| **Enterprise** | Fully customisable SDK build and unlimited embedded sessions. |

Embedded "sessions" are metered per **embed config / frozen-view fetch** (each
`GET /embed/config` or `GET /embed/frozen` from an embed token starts one).
First-party dashboard views are never metered. Each plan includes a monthly
allowance; overages bill at the plan rate — see
[Billing & usage](/docs/billing-and-usage).

---

## Security checklist

Use this before shipping an embed to production.

| Check | Why it matters |
|-------|---------------|
| **Sign only on your backend** | Your RS256/ES256 private key must never reach the browser. Browser-side signing lets any viewer forge arbitrary RLS policies. |
| **Token lifetime ≤ 15 minutes** | Embed tokens are bearer credentials. A short `exp` limits the blast radius if one leaks; the `createGetToken` helper refreshes transparently. |
| **Set `embed_origin`** | Pins the token to one browser origin. A mismatch — including a missing `Origin` header from a non-browser client — returns 403. |
| **Register only queries you intend to expose** | The embed path rejects raw SQL; only registered `query_id` values are callable. Keep the registry audited. |
| **Build `policies` server-side** | Nubi reads RLS from the verified token only, but your mint endpoint must build `policies` from authoritative server state, not viewer-supplied input. |
| **Use a JWKS URL, not a static key** | A JWKS URL allows zero-downtime key rotation via a new `kid`. |
| **Disable `EMBED_DEV_TOKEN_ENABLED` in production** | The dev mint endpoint skips asymmetric signing and must not be reachable from public traffic. |
| **Keep public exports off unless truly public** | `ALLOW_UNSAFE_PUBLIC_EXPORTS` + per-org `public_exports_enabled` must both be on; only export data safe for anyone. |
| **`alg: none` and HS256 are already blocked** | Nubi rejects algorithm confusion by design — but verify you are not serving HS256 tokens to the embed path from your own mint endpoint. |

---

## Rate limiting and embed exemption

Nubi enforces per-org rate limits on query-class routes, keyed by the
cryptographically **verified** org (a forged `org` claim falls back to the client
IP key, never a fresh bucket).

| Route class | Default cap | Env var |
|-------------|-------------|---------|
| `query` | 120 req/min | `NUBI_RATELIMIT_QUERY_RPM` |
| `auth` | configurable | `NUBI_RATELIMIT_AUTH_RPM` |
| `chat` | 20 req/min | `NUBI_RATELIMIT_CHAT_RPM` |
| Burst ceiling | 1.5× cap | `NUBI_RATELIMIT_BURST_FACTOR` |

Redis-backed when `REDIS_URL` is set (enforced across all workers); otherwise an
in-process approximation. Requests over the cap receive `HTTP 429` with a
`Retry-After` header.

**Embed exemption.** A cockpit dashboard fires many tile queries at once.
Throttling those would degrade the board for everyone, so **verified embed
tokens** (`kind: "embed"`, signature checked against your JWKS) are exempt from
the per-org query bucket on the read paths below:

| Exempt path | Description |
|-------------|-------------|
| `POST /api/v1/metrics/{id}/query` | Metric tile queries |
| `POST /api/v1/metrics/{id}/sql` | Metric SQL export |
| `POST /api/v1/query` and `/api/v1/query/*` | General registered-query path |

The exemption applies **only** to verified embed tokens — first-party tokens on
these paths remain subject to the bucket, and an invalid/forged embed token falls
back to IP-keyed limiting. Per-query resource cost is still bounded by the query
planner, the DuckDB memory ceiling, and the registered-query allowlist.

Set `NUBI_RATELIMIT_ENABLED=false` to disable all rate limiting (local dev and
tests only — never in production).

---

## Related

- [Embed API reference](/docs/embed-api) — versioned component/claim/endpoint/error contract.
- [Queries & Parameters](/docs/queries-and-params) — register the queries you expose to embeds.
- [Dashboards](/docs/dashboards) — build the boards you embed by id.
- [Connector Security](/docs/connector-security) — how warehouse credentials are protected.
- [Organization Settings](/docs/organization-settings) — manage JWT issuers and org-level security.
