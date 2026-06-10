---
title: The Structural Difference
tagline: Three architectural bets that change what's possible — and what it costs.
---

## Near-zero marginal cost

Compute runs in the viewer's browser (DuckDB-WASM, SQL). 500 concurrent embedded viewers sharing the same dashboard collapse to 1 warehouse hit — the advantage is real at high cache-hit rates and extends to diverse workloads via automatic pre-aggregations.

## Arrow IPC end-to-end

Results move as columnar Arrow buffers over WebSocket. The viz layer reads them directly — no JSON serialisation round-trip. `<nubi-chart>` renders on canvas via Apache ECharts, so charts stay fast and responsive even on large result sets.

## Auth as code

Publish your JWKS, implement `getToken()`, mount `<nubi-dashboard>`. JWT claims drive row-level security — enforced server-side in the connector before any buffer reaches the browser. No separate embed SDK. Policies are TypeScript/SQL in your repo, diffable in PRs.

---

## Honest limitations

The cost advantage is real **only at high cache-hit / pre-aggregation rates** — 500 analysts each slicing differently reverts to warehouse scans. Browser memory cap (~4 GB) requires aggressive pushdown. The browser only runs SQL (DuckDB-WASM), so Python and native-wheel workloads route to the on-demand server kernel — it's a launch requirement for those, not optional. NoSQL is deliberately out of scope. The M10 self-host stack is not yet shipped.
