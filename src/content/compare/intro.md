---
title: How Nubi Compares
eyebrow: "Competitive overview · 2026"
subtitle: "An honest comparison against Hex, Cube, Metabase, Looker, Sigma, Tableau, Power BI, and Apache Superset."
---

## The structural edge

**Nubi's core bet:** the analytics kernel runs in the user's browser by default — so the marginal cost of an embedded view is ≈ $0 at high cache-hit rates. Arrow IPC + WebGL handles 1M+ point datasets. No hand-written semantic model required to start.

> **How we're better:** cheaper than Hex (no per-session kernel), cheaper-at-scale than naive caching (auto pre-aggregations, the Cube weapon), lower friction than Cube (no hand-written semantic model to start), and rendering + authoring are included rather than bring-your-own.

## Positioning

| | Hex | Cube | Nubi |
|---|---|---|---|
| Shape | Notebook + apps | Headless semantic layer + API | Batteries-included BI + embed |
| Kernel | Python per session, their cloud (10–30 s cold, $$) | n/a (warehouse + Cube Store) | Pyodide in browser; on-demand server kernel only when needed |
| Result transport | JSON via pandas | JSON / SQL API | Arrow IPC over WebSocket |
| Viz | Plotly/SVG, chokes past ~50 k rows | bring-your-own | WebGL/WebGPU on Arrow buffers, 1M+ points |
| Caching | Per-session, weak cross-user | Pre-aggregations in Cube Store | Content-hashed edge cache + auto pre-aggregations |
| Modeling tax | Medium | High (define cubes first) | Low (point at a warehouse and go) |
| Embedding | Separate product, bolt-on auth | Core strength, headless only | Core surface; editor embeddable, not just output |
| Pricing | Per-seat (kernels cost real money) | Infra/seat | Connector throughput / embed views / AI calls / on-demand kernel time; real free tier |
