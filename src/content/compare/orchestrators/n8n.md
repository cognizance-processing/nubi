---
name: n8n
tagline: Visual workflow automation — 400+ integrations, self-hostable, AI-native nodes
selfHost: "Yes — n8n Community Edition is free to self-host (fair-code license: source-available, free for internal use). Docker image available; Postgres or SQLite for state. Business/Enterprise plans add licensed features (SSO, Git version control, environments) even when self-hosted."
pricing: "Cloud: Starter €20/month (2,500 executions), Pro €50/month (10,000 executions), Business €667/month (40,000 executions + self-host), Enterprise custom. Self-hosted Community: free (unlimited executions). Pricing is per workflow execution, not per step."
pricingUnverified: false
sourceUrls:
  - https://n8n.io/pricing/
  - https://automationatlas.io/answers/n8n-pricing-self-hosted-vs-cloud-2026/
  - https://expresstech.io/the-real-cost-of-self-hosting-n8n-in-2026/
  - https://dancumberlandlabs.com/blog/n8n-ai-workflows/
---

## Strength

Best visual DAG builder in its class — drag-and-drop node canvas with 400+ pre-built integration nodes (HTTP, databases, SaaS apps, 12+ LLM providers). AI agent nodes are first-class: connect OpenAI, Anthropic Claude, Gemini, Ollama, and others directly in the canvas with no code. Fair-code license makes the full self-hosted version free for internal use with unlimited workflow runs. Strong community and active node ecosystem.

## Limitation

n8n is an integration/automation tool, not a data pipeline orchestrator — it has no concept of data assets, lineage, Arrow IPC, or warehouse-native query execution. Execution model is workflow-centric (trigger → steps), not a dependency DAG over data artifacts. Cloud pricing is execution-capped (2,500–40,000/month), which can be limiting for high-frequency data workflows. Fair-code license (not Apache/MIT) restricts building competing products on the codebase. No per-user RLS or multi-tenant data isolation.

## Notes

n8n is the closest visual analogue to Nubi Flows' React Flow DAG builder. The key difference is domain: n8n excels at SaaS integration automation; Nubi Flows targets analytics workflows (query → python transform → agent summary) running inside a multi-tenant BI product with JWT-scoped row-level security. They are complementary for different use cases.
