---
name: Power BI
tagline: "Microsoft's BI platform — deep Office/Azure integration, Copilot AI"
selfHost: "Power BI Report Server: on-premise/self-hosted, included with Power BI Premium or SQL Server EE with SA. Feature lag vs cloud (typically 3–6 months behind)."
pricing: "Free: create/view only, no sharing. Pro: $14/user/month. Premium Per User (PPU): $24/user/month. Fabric F-SKU (capacity, covers embedding): F2 ~$262/month to F128 ~$16,768/month (PAYG). Microsoft 365 E5 includes Power BI Pro."
pricingUnverified: false
sourceUrls:
  - https://www.microsoft.com/en-us/power-platform/products/power-bi/pricing
  - https://azure.microsoft.com/en-us/pricing/details/power-bi-embedded/
  - https://powerbiconsulting.com/blog/power-bi-pricing-licensing-guide-2026
  - https://datatako.com/blog/power-bi-embedded-complete-2026-guide
---

## Strength

Best price-performance for Microsoft shops (included in M365 E5); Copilot AI at no extra charge on F2+ capacity (GPT-4/Azure OpenAI); capacity-based embedding removes per-viewer cost; 100+ connector library; Excel familiarity reduces training cost.

## Limitation

Lock-in to Microsoft/Azure ecosystem. Import mode requires scheduled refresh (data staleness). VertiPaq is proprietary — no Arrow IPC. Copilot requires F-SKU capacity (not available on Pro). A-SKU and P-SKU retirement causes migration complexity. Q&A feature retiring Dec 2026.

## Notes

Power BI's Fabric F-SKU model is actually competitive for embedded analytics — no per-viewer license at any F-tier means F4 (~$400/month) covers ~100 concurrent users. Nubi's near-zero marginal cost advantage is most pronounced outside the Microsoft ecosystem; inside it, Power BI + M365 E5 is hard to beat on price.
