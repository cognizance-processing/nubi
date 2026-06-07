---
name: "Preset / Apache Superset"
tagline: Open-source BI (Apache Superset) with a managed cloud option (Preset)
selfHost: "Yes — Apache Superset is free to self-host (Apache 2.0). Docker Compose and Kubernetes Helm charts available. Preset offers managed self-hosted 'Certified Superset' on Enterprise."
pricing: "Apache Superset: 100% free and open source (Apache 2.0 license). Preset Cloud: Starter free forever (up to 5 users); Professional $20/user/month (billed annually); Enterprise: custom. Embedded viewer licenses: from $500/month for 50 viewers (Preset Professional+)."
pricingUnverified: false
sourceUrls:
  - https://preset.io/pricing/
  - https://superset.apache.org/
  - https://www.metabase.com/blog/vs-superset
  - https://embeddable.com/blog/metabase-pricing
---

## Strength

Fully open source (Apache 2.0 — no license cost, no AGPL compliance burden); large community; low barrier to start; ECharts viz library is capable; Redis-based result caching configurable out of the box.

## Limitation

No Arrow IPC; no WebGL GPU rendering; no automatic pre-aggregation; no formal semantic layer (requires external dbt/Cube for pre-agg). Embedded viewer pricing on Preset adds up quickly at scale. AI features limited and largely unverified. Self-hosting requires significant DevOps investment.

## Notes

Apache Superset is the best entry point if you need open-source BI with zero license cost and Apache 2.0 freedom. Nubi's advantage emerges at scale — embedded analytics cost and rendering performance — not at zero-to-one exploration.
