---
name: Preset
tagline: Managed Apache Superset — cloud-hosted open-source BI
selfHost: "Yes — Preset Certified Superset (Enterprise) supports managed self-host; core Apache Superset is free open-source. Cloud SaaS is primary offering."
pricing: "Starter: free (up to 5 users). Professional: $20/user/month (annual) / $25/month (monthly). Embedded viewer add-on: $500/month for 50 viewers. Enterprise: custom pricing with managed private cloud option."
pricingUnverified: false
sourceUrls:
  - https://preset.io/pricing/
---

## Strength

Fully managed Apache Superset removes all the operational burden of self-hosting; strong SQL Lab for power users; large open-source Superset ecosystem for community charts and connectors; no-seat-penalty for small teams on the free tier.

## Limitation

Embedding is an add-on rather than a core product surface: $500/month for 50 embedded viewers is competitive only at very small scale — 500 viewers would cost $5,000/month. Every embedded viewer is a paid viewer seat. Professional tier is per-seat, so costs scale with team size. The underlying Superset architecture has no in-browser compute, no Arrow IPC, and no WebGL path — large datasets degrade at the same ceiling as self-hosted Superset.

## Notes

Preset is the clearest managed-Superset option. For teams already invested in Apache Superset, Preset eliminates DevOps overhead. The $20/user/month Professional price is competitive against Metabase Pro for internal BI. However, for embedded SaaS products the per-viewer-add-on model penalises growth identically to Metabase.
