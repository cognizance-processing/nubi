---
name: Holistics
tagline: BI platform with unlimited embedded viewers on flat fee
selfHost: "No — cloud SaaS only. No self-host option."
pricing: "Entry: $960/month ($800/month annual), 10 included seats, unlimited embedded viewers, 100 reports cap. Standard: $1,200/month ($1,000/month annual), 10 included seats, unlimited reports. Security Compliance Suite (SCS): $2,400/month ($2,000/month annual), SAML, SCIM, RLS passthrough, HIPAA-ready. Additional seats: $15–$18/seat/month at each tier."
pricingUnverified: false
sourceUrls:
  - https://www.holistics.io/pricing/
  - https://www.holistics.io/blog/embedded-analytics-pricing/
---

## Strength

Genuinely unlimited embedded viewers at a flat platform fee — the most generous embedded-viewer model among BI platforms in this price range. Strong RLS with passthrough auth on SCS tier. Unlimited reports on Standard+. Solid SQL-first workflow with no-code exploration. Well-suited for SaaS ISVs embedding BI for customers.

## Limitation

Cloud-only; no self-host option. The flat fee starts at $800/month annual — significantly higher than Nubi's entry tiers (Starter $9/month, Team $49/month). No in-browser compute: all queries push to the warehouse. No WebGL or Arrow IPC path. The $800/month entry is the lowest cost viable option for embedded analytics but is still ~16× Nubi Team ($49/month, 5,000 embedded sessions) or ~5× Nubi Pro ($149/month, 25,000 embedded sessions). AI features are limited compared to newer entrants.

## Notes

Holistics is the strongest direct competitor for the "unlimited embedded viewers, flat fee" positioning. Nubi Pro at $149/month offers an equivalent unlimited-viewer model at roughly one-fifth the price of Holistics Entry ($800/month annual). The key Nubi differentiator is the near-zero marginal cost of browser-side compute at high cache-hit rates; Holistics is all warehouse-pushdown and therefore passes warehouse query costs through to customers. Holistics has a more mature ecosystem and a stronger track record with mid-market ISVs.
