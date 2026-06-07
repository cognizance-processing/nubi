---
title: The Structural Difference
tagline: Three architectural bets that change what's possible — and what it costs.
---

## Near-zero marginal cost

Compute runs in the viewer's browser (Pyodide + DuckDB-WASM). 500 concurrent embedded viewers sharing the same dashboard collapse to 1 warehouse hit — the advantage is real at high cache-hit rates and extends to diverse workloads via automatic pre-aggregations.

## Arrow IPC end-to-end

Results move as columnar Arrow buffers over WebSocket. The viz layer reads them directly — no JSON serialisation round-trip. WebGL/WebGPU renders 1M+ points at 60 fps via regl; `<nubi-chart>` auto-upgrades to WebGL above a configurable row threshold.

## Auth as code

Publish your JWKS, implement `getToken()`, mount `<nubi-dashboard>`. JWT claims drive row-level security — enforced server-side in the connector before any buffer reaches the browser. No separate embed SDK. Policies are TypeScript/SQL in your repo, diffable in PRs.

---

## Honest limitations

The cost advantage is real **only at high cache-hit / pre-aggregation rates** — 500 analysts each slicing differently reverts to warehouse scans. Browser memory cap (~4 GB) requires aggressive pushdown. Pyodide native-wheel gaps mean on-demand kernel is a launch requirement, not optional. NoSQL is deliberately out of scope. The M10 self-host stack is not yet shipped.
