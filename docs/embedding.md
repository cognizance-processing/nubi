# Embedding

Nubi's core surface is embedding: mount a live, cross-filtering dashboard inside any host SaaS page at near-zero marginal cost per view. The `<nubi-dashboard>` custom element fetches Arrow IPC data, enforces RLS from the host's JWT, and renders in the user's browser — no per-session server kernel required.

---

## Quickstart

```html
<!-- 1. Load the widget bundle -->
<script src="https://cdn.example.com/dist-embed/nubi-dashboard.js"></script>

<!-- 2. Mount the component -->
<nubi-dashboard
  query="SELECT region, SUM(revenue) AS total FROM sales GROUP BY 1"
  get-token="getEmbedToken"
  backend="https://api.example.com"
  style="display:block; height:300px;">
</nubi-dashboard>
```

---

## Integration Steps

### 1. Register Your Issuer

Add your app's JWKS endpoint to `app/auth/issuers.py` on the Nubi backend:

```python
{
  "iss":             "https://your-app.example.com",
  "jwks_uri":        "https://your-app.example.com/.well-known/jwks.json",
  "aud":             "nubi:your-project-id",
  "allowed_origins": ["https://your-app.example.com"],
}
```

### 2. Mint Short-Lived JWTs

From your backend, sign JWTs with RS256 or ES256 (max lifetime 15 minutes):

```js
async function getToken() {
  const { token } = await fetch('/your-api/nubi-token').then(r => r.json())
  return token  // signed JWT from your backend
}
window.getEmbedToken = getToken
```

**Required JWT claims:**

```json
{
  "iss":          "https://your-app.example.com",
  "sub":          "user-or-service-id",
  "aud":          "nubi:your-project-id",
  "org":          "your-org-slug",
  "project":      "your-project-slug",
  "roles":        ["viewer"],
  "scope":        ["read:*"],
  "policies":     { "tenant_id": "acme" },
  "embed_origin": "https://your-host-page.example.com",
  "exp":          1234567890,
  "iat":          1234566990
}
```

The `policies` object becomes server-side RLS predicates — injected by the planner as AST-level `WHERE` clauses, never string-concatenated.

### 3. Mount the Component

The element handles JWKS verification, RLS enforcement, Arrow IPC fetch, and rendering automatically.

---

## Per-Viewer RLS

Each viewer's JWT carries their own `policies` object. The Nubi backend:

1. Verifies the JWT signature against the JWKS endpoint registered for the token's `iss`.
2. Extracts `policies` from the verified token (ignoring any `policies` in the request body).
3. Injects them as AST-level predicates before the query reaches the warehouse.

Two viewers with different `tenant_id` policies get different Arrow results and different cache keys — their data is fully isolated. The content-addressed cache key includes `policies`, so different RLS contexts never share a cache slot.

---

## Token-Locked Params

Embed tokens can lock query parameter values so they cannot be overridden by URL params or filter widgets. This is the mechanism for enforcing per-viewer data boundaries beyond row-level security.

The `/d/:id` dashboard view reads locked params from a verified embed JWT and passes them to `SpecRenderer` as immutable initial variable values that filter widgets cannot change.

---

## Embed Token Restrictions

Embed tokens (`kind='embed'`) are subject to stricter constraints than first-party tokens:

| Restriction | Detail |
|-------------|--------|
| **No arbitrary SQL** | Must reference a server-registered query via `query_id`. Any `sql` field is silently ignored. |
| **No compute routes** | Embed tokens lack `exec:kernel` scope; cannot call `/api/v1/compute/run`. |
| **No AI routes** | Embed tokens cannot call AI generation endpoints. |
| **Origin pinning** | `embed_origin` claim must match the `Origin` request header. |
| **Scope required** | Token must carry at least `read:*` or `read:dashboard:*`. |

---

## `<nubi-dashboard>` Reference

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | SQL string or registered query id |
| `token` | One of | Static JWT string |
| `get-token` | One of | Name of a `window` function returning `Promise<string>` |
| `backend` | No | API base URL (default: `http://localhost:8000`) |
| `theme` | No | Reserved for future preset names; theming via CSS custom properties |

### Events

| Event | `detail` | Fired when |
|-------|----------|------------|
| `nubi:ready` | `{ rowCount: number }` | After successful render (real or sample) |
| `nubi:error` | `{ message: string }` | On any non-recoverable error |
| `nubi:query-run` | `{ rowCount, cacheStatus, elapsedMs, sample }` | After each query attempt |

### CSS Custom Properties

```css
nubi-dashboard {
  --nubi-bg:     #ffffff;
  --nubi-fg:     #1a1a2e;
  --nubi-accent: #f0f4ff;
  --nubi-border: #dde1ea;
}
```

### Sample Fallback

On any failure (network, auth, parse error) the element renders a built-in five-row sample table and shows an orange "preview (sample data)" banner. The `nubi:error` event is still fired so the host can log or surface the underlying error.

---

## Token Resolution

The `get-token` attribute value is the **name** of a function on `window`:

```js
window.getEmbedToken = async function() {
  const { token } = await fetch('/api/embed-token').then(r => r.json())
  return token
}
```

The SDK's `createGetToken` helper handles in-memory caching and proactive refresh (~60 seconds before expiry):

```js
import { createGetToken } from './embed/getToken.reference.js'

window.getEmbedToken = createGetToken({
  mintUrl:      '/api/embed-token',
  fetchOptions: { credentials: 'include' },
})
```

---

## Embed Config Endpoint

For dashboard-id-based embedding, the backend serves a read-only descriptor:

```
GET /api/v1/embed/config/{dashboard_id}
Authorization: Bearer <embed-jwt>
```

Response:

```json
{
  "dashboard_id": "board-uuid",
  "title":        "Revenue Overview",
  "spec":         { "version": 1, "title": "...", "widgets": [...] },
  "html":         "<div class='nubi-dashboard'>...</div>",
  "widgets":      [...],
  "theme":        {}
}
```

Org resolution: embed tokens carry an `org` claim used directly as `org_id`. First-party tokens resolve org via the `org_members` table.

---

## JavaScript SDK — `embed.mount()`

```js
import { createNubiClient } from '@nubi/sdk'

const client = createNubiClient({
  baseUrl:  'https://api.example.com',
  getToken: async () => myGetJwt(),
})

const { unmount } = client.embed.mount(
  document.getElementById('dashboard-root'),
  { query: 'revenue_by_month' }
)

// Tear down later:
unmount()
```

Prerequisite: load the `nubi-dashboard` bundle on the host page so the custom element is registered.

---

## Widget Kit for Finer-Grained Layouts

For layouts composed of individual widgets rather than a full dashboard, load the widget bundle:

```html
<script src="https://cdn.example.com/dist-embed/nubi-widgets.js"></script>
```

See the [Dashboards](/docs/dashboards) page for full widget attribute reference.

---

## Security Model Summary

| Control | Mechanism |
|---------|-----------|
| Origin pinning | `embed_origin` JWT claim must match `Origin` header |
| Scope enforcement | Token must carry `read:*` or `read:dashboard:*` |
| SQL allowlist | Embed tokens must reference registered queries; raw SQL is blocked |
| RLS | `policies` from the verified token injected as AST predicates |
| Compute isolation | Embed tokens lack `exec:kernel` scope; cannot call `/api/v1/compute/run` |
| Token reuse prevention | Embed tokens are short-lived (≤ 15 min); `embed_origin` ties them to one host |
| JWKS verification | Signatures verified against the issuer's JWKS endpoint; no shared secret needed |
