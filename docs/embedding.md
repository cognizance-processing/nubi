# Embedding dashboards

Embedding is Nubi's core surface. You mount a live, cross-filtering dashboard inside any page of your own application, and your app's own login decides who sees what. The `<nubi-dashboard>` web component runs in the viewer's browser, fetches results as Arrow IPC, and the Nubi backend enforces row-level security from a short-lived JWT that **your** backend signs — so there is no per-viewer server kernel and no Nubi seat to buy for each end user.

This page walks through the full flow: registering your signing key, minting embed tokens, enforcing per-viewer row-level security, mounting the component or SDK, and the white-label options available at each tier.

---

## How embedding works

1. A viewer loads a page in **your** app that contains a `<nubi-dashboard>` element.
2. The element asks your page for a token (via a `get-token` function you provide).
3. Your backend authenticates the viewer (your session/SSO) and signs a short-lived **embed JWT** with your private key. The token carries the viewer's org, scope, and row-level-security policies.
4. The element calls the Nubi query API with that token. Nubi verifies the signature against the **JWKS** (public key) you registered, checks the audience, expiry, and origin, then runs the query with the viewer's RLS predicates injected.
5. Results stream back as Arrow IPC and render in the browser.

Because the token is signed by your key and verified against your public JWKS, **Nubi never holds a shared secret for your app** and you never have to round-trip viewer credentials through Nubi.

---

## Step 1 — Register your signing key (JWT issuer)

Nubi needs the **public** half of the key you sign embed tokens with. You register it once as a *JWT issuer*.

### In the UI

1. Open **Settings → Security** (`/settings/security`).
2. Under **JWT issuers**, add an issuer and fill in:
   - **Name** — a human-readable label (e.g. "Production web app").
   - **Issuer (`iss` claim)** — the exact string your tokens will put in their `iss` claim, e.g. `https://app.yourcompany.com`.
   - **JWKS URL** *(recommended)* — the HTTPS URL of your JSON Web Key Set, e.g. `https://app.yourcompany.com/.well-known/jwks.json`. Nubi fetches and caches it, and picks up your key rotations automatically.
   - **Or paste a static JWKS** — if you don't host a JWKS endpoint, paste a full JWKS JSON object (e.g. `{"keys": [...]}`) instead.
   - **Algorithms** — the signing algorithms you allow. Defaults to `RS256`. `ES256` and the longer `RS384/RS512`, `ES384/ES512` variants are also supported.
   - **Audience (`aud` claim)** — the expected `aud` value, e.g. `nubi:your-project-id`.
3. Save. Issuers are scoped to your org, and changes take effect immediately (no restart).

Each issuer can be toggled **enabled**/disabled. While disabled, tokens carrying that `iss` are rejected.

### Via the API

The same configuration is available through the management API. You need a first-party access token with writer permission.

```
POST /api/v1/security/jwt-issuers
Authorization: Bearer <your-access-token>
Content-Type: application/json

{
  "name":      "Production web app",
  "issuer":    "https://app.yourcompany.com",
  "audience":  "nubi:your-project-id",
  "jwks_url":  "https://app.yourcompany.com/.well-known/jwks.json",
  "algorithms": ["RS256"],
  "enabled":   true
}
```

Either `jwks_url` or `static_jwks_json` must be provided. The matching `GET`, `PUT` (partial update), and `DELETE` routes under `/api/v1/security/jwt-issuers/{id}` let you retrieve, update, and remove an issuer.

> Embed tokens are always **asymmetric** (RS256/ES256). Nubi rejects `alg: none` and never accepts an HS256-signed token on the embed path — this blocks algorithm-confusion attacks. You keep the private key; Nubi only ever sees the public key.

---

## Step 2 — Mint short-lived embed tokens

On every request the component needs a token, it calls a function you expose on `window`. That function should hit a small endpoint on **your** backend that authenticates the viewer and returns a freshly signed JWT.

### Required and recommended claims

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
| `iss` | Yes | Must match a registered issuer. |
| `sub` | Yes | The viewer or session identifier. |
| `aud` | Yes | Must match the issuer's configured audience. |
| `exp` | Yes | Expiry. **Keep it short — 15 minutes or less.** Missing `exp` is rejected. |
| `org` | Yes for embed | Used directly as the org for data and RLS scoping. |
| `scope` | Yes | Must grant a read scope (see Step 4). |
| `policies` | For RLS | Per-viewer row-level-security predicates (see Step 3). |
| `embed_origin` | Recommended | Pins the token to one browser origin (see Step 4). |
| `roles`, `project` | Optional | Carried through to the verified identity. |

> **Keep tokens short-lived.** Embed tokens are bearer credentials. A 15-minute lifetime, combined with `embed_origin` pinning, limits the blast radius if a token leaks. The reference `getToken` helper refreshes about 60 seconds before expiry.

### Wiring the token function on your page

The simplest path is the bundled reference helper, which caches the token in memory and refreshes it proactively:

```js
import { createGetToken } from './getToken.reference.js'

window.getEmbedToken = createGetToken({
  mintUrl:      '/api/embed-token',          // your backend, signs the JWT
  fetchOptions: { credentials: 'include' },   // send your own session cookie
})
```

Your `mintUrl` endpoint must:

- Authenticate the calling viewer with **your** auth system.
- Build the claims above (especially `policies` and `embed_origin`).
- Sign with your RS256/ES256 **private** key.
- Return `{ "token": "<jwt>" }` (a bare JWT string or `{ "access_token": "..." }` is also accepted).

The token is held in memory only — never written to `localStorage` or `sessionStorage`.

### Local development shortcut

For local demos where you don't want to stand up signing infrastructure, the backend can mint a first-party token for you:

```
POST /api/v1/embed/embed-token
{ "org": "demo-org", "policies": { "tenant_id": "acme" }, "scope": ["read:*"] }
```

This endpoint is **disabled by default** and only works when `EMBED_DEV_TOKEN_ENABLED=true` is set. It mints an HS256 token using the backend secret and must **never** be enabled in production — real embeds use your asymmetric key.

---

## Step 3 — Row-level security via JWT claims

Every viewer's token carries a `policies` object. Nubi treats it as the source of truth for that viewer's data boundary:

```json
"policies": { "tenant_id": "acme", "region": "EMEA" }
```

When the query runs, Nubi:

1. Verifies the JWT signature against the issuer's JWKS.
2. Reads `policies` **from the verified token only** — any `policies` sent in the request body is ignored.
3. Injects them as AST-level `WHERE` predicates before the query reaches your warehouse. They are never string-concatenated into SQL.

Two viewers with different `tenant_id` policies receive different results and never share data. The content-addressed result cache key includes the policies, so different RLS contexts never collide in the cache.

> Row-level security is available on **Team and above**. On lower tiers, design each registered query so it is scoped to a single tenant, or upgrade for policy-based isolation.

---

## Step 4 — Restrictions on embed tokens

Embed tokens are deliberately more constrained than first-party tokens. Designing around these up front avoids surprises.

| Restriction | What it means |
|-------------|---------------|
| **No raw SQL** | An embed token **must** reference a server-registered query by `query_id`. Any `sql` field in the request is ignored; an embed request without a `query_id` is rejected with `query_not_registered` (403). Register the queries you want to expose first (see [Queries & Parameters](/docs/queries-and-params)). |
| **Read scope required** | The `scope` claim must grant a read scope — `read:*`, `read:query`, or `read:dashboard:*`. Without it the request returns `insufficient_scope` (403). |
| **Origin pinning** | If the token carries `embed_origin`, the request's `Origin` header must match it exactly. A missing `Origin` (e.g. a server-side or scripted call) also fails — the token is bound to one browser origin. Mismatch returns `origin_mismatch` (403). |
| **No compute or AI** | Embed tokens are read-only: they cannot run server kernels or call AI generation endpoints. |

Because raw SQL is blocked, **the safe surface for embeds is your registered query library**. Combine a registered query with token-supplied `policies` and you get a fixed, auditable query whose rows are scoped per viewer.

---

## Step 5 — Mount the dashboard

### Option A — the `<nubi-dashboard>` web component

Drop in the bundle, then add the element. It is framework-agnostic and works in plain HTML, React, Vue, or anywhere.

```html
<!-- 1. Load the component bundle (registers the custom element) -->
<script type="module" src="https://cdn.yourcompany.com/nubi-dashboard.js"></script>

<!-- 2. Mount it -->
<nubi-dashboard
  query="revenue_by_region"
  get-token="getEmbedToken"
  backend="https://api.yourcompany.com"
  style="display:block; height:360px;">
</nubi-dashboard>
```

The element resolves a token, calls the query API, parses the Arrow IPC response, and renders a table. It de-bounces re-renders and aborts in-flight fetches when attributes change.

#### Attributes

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | A registered `query_id` (required for embed tokens) or, for first-party tokens, a SQL string. |
| `token` | One of | A static JWT string. |
| `get-token` | One of | The **name** of a `window` function returning `Promise<string>` (or a string). Called before each query. |
| `backend` | No | Nubi API base URL. Defaults to `http://localhost:8000`. |
| `theme` | No | Reserved preset name; theming is done via CSS custom properties. |

#### Events

The element dispatches bubbling, composed events so your host page can react:

| Event | `detail` | Fired when |
|-------|----------|------------|
| `nubi:ready` | `{ rowCount }` | After a successful render (real data or sample fallback). |
| `nubi:query-run` | `{ rowCount, cacheStatus, elapsedMs, sample }` | After each query attempt. |
| `nubi:error` | `{ message }` | On any non-recoverable error. |

```js
document.querySelector('nubi-dashboard')
  .addEventListener('nubi:error', e => console.warn('Embed error:', e.detail.message))
```

#### Theming

The component renders inside a Shadow DOM with a **dark** default palette. Override the CSS custom properties on a parent or `:root`:

```css
nubi-dashboard {
  --nubi-bg:     #0f1117;  /* table background */
  --nubi-fg:     #e2e8f0;  /* text colour      */
  --nubi-accent: #1e2433;  /* header row       */
  --nubi-border: #2d3748;  /* cell borders     */
}
```

#### Sample fallback

If anything fails — network down, auth rejected, parse error — the element still renders a small built-in sample table with a "preview (sample data)" badge, so a demo page never shows an empty box. The `nubi:error` event is still fired so your app can log the real cause.

### Option B — the JavaScript SDK

If you prefer a programmatic client (and want to call other Nubi APIs from your host app), use `@nubi/sdk`. Its `embed.mount` creates and wires up the same `<nubi-dashboard>` element for you, bridging your `getToken` automatically.

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

// Later, to tear down:
unmount()
```

The SDK also exposes `client.query(idOrSql, { params })` (returns an Arrow `Table`) and CRUD clients for `datastores`, `boards`, `widgets`, and `queries`. Note: `embed.mount` still requires the `nubi-dashboard` bundle to be loaded on the page so the custom element is defined.

### Loading a saved dashboard by id

To embed a dashboard you built in Nubi (rather than a single query), fetch its descriptor with an embed token:

```
GET /api/v1/embed/config/{dashboard_id}
Authorization: Bearer <embed-jwt>
```

The response carries the board's `title`, its `spec` and/or `html`, a `widgets` list, and any `theme`, which your host can hand to the renderer. The same scope, origin, and org checks apply as for the query API.

---

## White-label options by plan

What you can do with embeds — volume and branding — depends on your plan. The branding capability ladders up as you move to higher plans:

- **Starter** — embedding is enabled; embeds carry a small Nubi attribution badge.
- **Team** — remove the Nubi badge, and unlock row-level security (per-viewer `policies`).
- **Pro** — full white-labelling, including serving embeds from your own custom domain.
- **Enterprise** — a fully customisable JS SDK build and unlimited embedded sessions.

Embedded "sessions" are dashboard loads per month, and each plan includes a monthly allowance. For the per-plan session allowances, prices, and overage rates, see the [Pricing page](/pricing) or [Billing & usage](/docs/billing-and-usage).

Row-level security (per-viewer `policies`) is available from **Team** upward.

---

## Security model at a glance

| Control | Mechanism |
|---------|-----------|
| Signature | Verified against your registered JWKS (RS256/ES256). `alg: none` and HS256-on-embed are always rejected. |
| Audience & issuer | `aud` and `iss` must match the registered issuer. |
| Expiry | `exp` is mandatory; keep it ≤ 15 minutes. |
| Origin pinning | `embed_origin` claim must match the request `Origin` header. |
| Scope | Token must carry a read scope (`read:*`, `read:query`, or `read:dashboard:*`). |
| SQL allowlist | Embed tokens can only run registered queries by `query_id`; raw SQL is blocked. |
| Row-level security | `policies` from the verified token are injected as AST predicates; body-supplied policies are ignored. |
| Cache isolation | The result cache key includes `policies`, so tenants never share a cache slot. |
| No shared secret | Nubi only holds your public key; your private signing key never leaves your infrastructure. |

---

## Related

- [Queries & Parameters](/docs/queries-and-params) — register the queries you expose to embeds.
- [Dashboards](/docs/dashboards) — build the boards you embed by id.
- [Connector Security](/docs/connector-security) — how warehouse credentials are protected.
