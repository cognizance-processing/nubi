# @nubi/sdk

Framework-agnostic JavaScript SDK for the Nubi API.

Wraps authentication, Arrow query execution, REST CRUD for all four domain
resources, and embed mounting — in a single, zero-React, drop-in ESM package.

---

## Installation

```bash
npm install @nubi/sdk
```

apache-arrow is **bundled** inside the SDK so no separate Arrow install is needed.

---

## Quickstart

```js
import { createNubiClient } from '@nubi/sdk'

const client = createNubiClient({
  baseUrl: 'https://api.example.com',     // Nubi backend origin
  getToken: async () => myGetJwt(),       // async fn or static string
})
```

`getToken` is called before every authenticated request. Pass an async function
that silently mints or refreshes your JWT, or a plain string for static tokens.

---

## auth

```js
const { user } = await client.auth.me()
// { id, email, name, avatar_url, email_verified, created_at }
```

---

## query — run SQL or a registered query

```js
// Inline SQL — sends { sql }
const table = await client.query('SELECT * FROM sales WHERE region = $1', {
  params: { '1': 'EMEA' },
})

// Registered query id — sends { query_id }
// Any single-word arg with no SQL keyword is treated as a query id.
const table = await client.query('revenue_by_month')

console.log(table.numRows)                     // Apache Arrow Table
console.log(table.getChild('amount').get(0))   // columnar access
```

Returns an [Apache Arrow `Table`](https://arrow.apache.org/docs/js/).
The backend responds with `Content-Type: application/vnd.apache.arrow.stream`;
the SDK parses it via `tableFromIPC`.

---

## resources — REST CRUD

Four resources are available: **datastores**, **boards**, **widgets**, **queries**.
Each has the same five methods:

```js
// List (org-scoped)
const boards = await client.resources.boards.list()

// Get by id
const board = await client.resources.boards.get('board-uuid')

// Create
const newBoard = await client.resources.boards.create({
  name: 'Q1 Dashboard',
  config: { layout: 'grid', columns: 3 },
})

// Update (partial — omit fields to leave them unchanged)
const updated = await client.resources.boards.update('board-uuid', {
  name: 'Q1 Dashboard v2',
})

// Delete (returns null; backend returns 204)
await client.resources.boards.remove('board-uuid')
```

All resources follow the same shape:
```ts
{
  id: string
  org_id: string
  created_by: string
  name: string
  config: object       // arbitrary JSON
  created_at: string   // ISO 8601
  updated_at: string
}
```

Errors from the backend arrive as `{ error: { code, message } }`.
The SDK throws an `Error` whose `.code` property is the `error.code` string
and whose `.message` is `error.message`.

```js
try {
  await client.resources.boards.get('nonexistent')
} catch (err) {
  console.error(err.code)    // e.g. "not_found"
  console.error(err.message) // e.g. "Board not found"
  console.error(err.status)  // HTTP status code, e.g. 404
}
```

---

## embed.mount — render a \<nubi-dashboard\> widget

The embed helper constructs a `<nubi-dashboard>` custom element, sets its
attributes, and appends it to a container element.

**Prerequisite:** the host page must load the `nubi-dashboard` bundle so the
custom element is registered:

```html
<script src="https://cdn.example.com/dist-embed/nubi-dashboard.js"></script>
```

Then in your JavaScript:

```js
const container = document.getElementById('dashboard-root')

const { unmount } = client.embed.mount(container, {
  query: 'SELECT region, SUM(revenue) AS total FROM sales GROUP BY 1',
  // token: 'explicit-jwt'  — optional; if omitted, client's getToken is used
  // backend: 'https://api.other.com'  — optional override
})

// Later, to tear it down:
unmount()
```

`mount()` returns an `{ unmount() }` handle. Calling `unmount()` removes the
element from the DOM and cleans up any window-level token bridge.

### Token resolution

If you pass `token`, it is set as a static attribute on the element.
If you omit `token`, the SDK registers a short-lived `window.__nubiGetToken_<id>`
bridge function that delegates to your `getToken`, so the web component can
silently refresh tokens as they expire.

### CSS custom properties

Theme the widget via CSS custom properties on any ancestor:

```css
#dashboard-root {
  --nubi-bg:      #ffffff;
  --nubi-fg:      #1a1a2e;
  --nubi-accent:  #f0f4ff;
  --nubi-border:  #dde1ea;
}
```

---

## Full example

```html
<!DOCTYPE html>
<html>
<head>
  <!-- 1. Load the nubi-dashboard custom element bundle -->
  <script src="https://cdn.example.com/dist-embed/nubi-dashboard.js"></script>
</head>
<body>
  <div id="root" style="width:100%;height:400px"></div>

  <script type="module">
    import { createNubiClient } from 'https://cdn.example.com/sdk/dist/nubi-sdk.js'

    const client = createNubiClient({
      baseUrl: 'https://api.example.com',
      getToken: async () => {
        // Your auth system mints a short-lived embed JWT here
        const res = await fetch('/api/embed-token')
        const { token } = await res.json()
        return token
      },
    })

    // Run a query and log the result
    const table = await client.query('revenue_by_month')
    console.log('rows:', table.numRows)

    // Or mount an embed widget
    const { unmount } = client.embed.mount(document.getElementById('root'), {
      query: 'revenue_by_month',
    })
  </script>
</body>
</html>
```

---

## Building from source

```bash
cd sdk
npm install
npm run build    # produces sdk/dist/nubi-sdk.js and nubi-sdk.umd.cjs
npm test         # node --test src/index.test.mjs
```

The build uses Vite in lib mode. apache-arrow is bundled into the output;
no peer dependencies are required.
