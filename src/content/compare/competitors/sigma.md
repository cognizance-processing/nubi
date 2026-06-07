---
name: Sigma Computing
tagline: Spreadsheet-UX live query BI on top of your warehouse
selfHost: "No — Sigma is fully cloud-hosted SaaS. No self-host option."
pricing: "No published pricing — negotiated with sales. Sigma introduced 4-tier license model March 2025: View, Act, Analyze, Build. Median annual contract: $61,158 (range $17.5k–$131k). Essentials from ~$300/month (unlimited users). Creator/Build licenses est. $2,000–$3,500/user/year."
pricingUnverified: true
sourceUrls:
  - https://www.sigmacomputing.com/product/architecture
  - https://qrvey.com/blog/sigma-pricing/
  - https://checkthat.ai/brands/sigma-computing/pricing
  - https://help.sigmacomputing.com/docs/caching-and-data-freshness
---

## Strength

Familiar spreadsheet UX removes BI learning curve; live warehouse queries with no data copies; strong Snowflake/Databricks integration; no per-seat ceiling for viewer counts on some tiers; 6-tier hybrid caching architecture.

## Limitation

All compute costs land on customer's warehouse bill (live query model = warehouse spend driver). Embedding pricing opaque and reportedly can double or triple contract value. No Arrow IPC; browser limited to 10 k rows/page for rendering. No self-host. AI features details unverified.

## Notes

Sigma is Nubi's closest architectural cousin in the "push everything to the warehouse" camp — but Sigma pushes compute *up* (to Snowflake) while Nubi pushes it *down* (to the browser). The result: Sigma's cost scales with warehouse usage; Nubi's marginal cost is near-zero at high cache-hit rates.
