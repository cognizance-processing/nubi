---
name: Embeddable
tagline: Embed-first analytics SDK — session-based pricing
selfHost: "No — cloud SaaS only."
pricing: "Free: 200 sessions/month, 3 dashboards, Embeddable branding. Lite: $499/month for 1,000 sessions; $200 per additional 500 sessions overage. Premium: custom pricing, full white-label, dev/staging environments, result caching. Enterprise: custom, compliance, dedicated infra."
pricingUnverified: false
sourceUrls:
  - https://embeddable.com/pricing
  - https://embeddable.com/blog/embedded-analytics-pricing-and-benefit-comparison
---

## Strength

Purpose-built and developer-first for embedded analytics; strong React/Vue component SDK; session-based pricing is predictable for stable-traffic SaaS products; no per-user viewer fees. Premium tier includes result caching and dev/staging environments for proper CI/CD workflow. Good documentation and onboarding experience.

## Limitation

$499/month Lite tier with only 1,000 sessions/month is expensive for the volume — overage at $200/500 sessions compounds quickly at scale. No open-source core; fully proprietary. Cloud-only. No in-browser compute (queries go to customer's warehouse). The free tier is very limited (200 sessions, 3 dashboards, branded). No self-host.

## Notes

Embeddable is the closest structural competitor in the "embedded analytics SDK" category. Nubi Team at $49/month includes 5,000 sessions — more than 10× cheaper than Embeddable Lite at $499/month for the same 1,000-session tier, and at parity volume (5,000 sessions) Embeddable would cost $499 + $1,600 in overages ($2,099/month) versus Nubi Team at $49/month. Even Nubi Starter at $9/month matches Embeddable's 1,000-session Lite tier at a fraction of the cost. The key difference is Nubi's in-browser compute: for repeat views of the same dashboard, compute runs in the viewer's tab, meaning sessions don't require round-trips to Nubi servers, reducing Nubi's COGS and allowing more generous session allowances.
