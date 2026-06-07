---
name: Cube
tagline: Headless semantic layer + API for embedded analytics
selfHost: "Yes — Cube Core is open source (MIT license). Self-hosted requires Redis, Cube Store cluster, API instances. Cube Cloud adds managed HA, advanced caching, observability."
pricing: "Free tier (hobbyists). Starter: $40/developer/month. Premium: $80/developer/month + Explorer $40/user + Viewer $20/user. Enterprise: custom. Infra billed separately per-hour on top of seats."
pricingUnverified: false
sourceUrls:
  - https://cube.dev/pricing
  - https://cube.dev/docs/product/administration/pricing
  - https://cube.dev/product/cube-core
  - https://github.com/cube-js/cube
---

## Strength

Gold-standard pre-aggregation engine; headless architecture suits any frontend; strong JWT/RLS security model; open-source core (MIT); embedded analytics chat and dashboards on Premium+.

## Limitation

High modeling tax — must write cube schema (JS/YAML) before any query works. Headless = no built-in viz or authoring. Infra billing on top of seats can surprise. JSON transport only. Cube Cloud hourly billing adds up: Dedicated deployment $0.60–$1.20/hr.

## Notes

Cube is the "auto pre-aggregations" benchmark. Nubi replicates Cube Store's core weapon without requiring a hand-written semantic model. Cube remains the better choice if you already have LookML-style schemas or need headless-only delivery.
