# Nubi Widgets Demo — the standalone widget kit

A framework-free example of the Nubi **widget kit** — the individual custom
elements you can drop into any page without the full `<nubi-dashboard>`:

- `<nubi-kpi>` — a single metric tile
- `<nubi-table>` — a paginated data table
- `<nubi-chart>` — a chart (line / bar / scatter / area / pie), with the
  WebGL scatter path for large point counts

These are the same components `<nubi-dashboard>` composes internally, exposed
so a host app can place a single widget exactly where it wants. For the full
dashboard (layout + cross-filtering + per-tenant JWT RLS), see
[`../embed-demo/`](../embed-demo/).

---

## Quick start

```bash
# From the repo root — build the widget bundle
npm install
npm run build:widgets        # → dist-embed/nubi-widgets.js (UMD) + .es.js (ESM)

# Serve the repo and open this page
python -m http.server 8080
open http://localhost:8080/examples/widgets-demo/index.html
```

The page loads the UMD bundle from `../../dist-embed/nubi-widgets.js`, which
auto-registers the custom elements (`registerNubiWidgets()`) on load.

---

## Using a widget in your own page

```html
<script src="https://cdn.nubi.dev/embed/nubi-widgets.js"></script>

<nubi-kpi   query-id="revenue_total"  value="revenue" format="currency"></nubi-kpi>
<nubi-chart query-id="revenue_by_month" type="area" x="month" y="revenue"></nubi-chart>
<nubi-table query-id="recent_orders"  page-size="20"></nubi-table>
```

Each widget fetches via the same token flow as `<nubi-dashboard>` — supply a
`get-token` attribute naming a global `getToken()` closure that returns a
short-lived JWT minted by **your backend** (never sign tokens in the browser).
See [`../embed-demo/README.md`](../embed-demo/README.md) for the token-minting
contract and the production wiring patterns.

---

## File layout

```
examples/widgets-demo/
├── index.html   This demo page (KPI + table + chart widgets)
└── README.md    This file
```
