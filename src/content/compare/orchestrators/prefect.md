---
name: Prefect
tagline: Python-native workflow orchestration — decorators, not YAML
selfHost: "Yes — Prefect Server is fully open source (Apache 2.0). Self-hosted requires a Postgres metadata DB; workers run anywhere (Docker, K8s, cloud VMs). Prefect Cloud adds managed API, multi-tenant workspaces, SSO, and SCIM."
pricing: "Hobby: free (2 users, 1 workspace, 500 serverless mins/month). Paid tiers from ~$75–$100/month for small teams; Enterprise custom. Pricing is seat/workspace-based, not usage-based. Compute/infra is separate (customer-owned or Prefect Serverless at $0.01/min)."
pricingUnverified: false
sourceUrls:
  - https://www.prefect.io/pricing
  - https://www.prefect.io/prefect/open-source
  - https://github.com/PrefectHQ/prefect
  - https://automationatlas.io/answers/prefect-pricing-explained-2026/
---

## Strength

Excellent Python-native developer experience — `@flow` and `@task` decorators turn ordinary functions into durable, retriable, observable tasks with no YAML required. Prefect 3 (Prefect Server OSS) runs with just a Postgres database; no Redis or Celery needed for the basic scheduler. Rich observability UI (Prefect Cloud or self-hosted) with task-level state, logs, artifacts, and automations.

## Limitation

Execution infra is out of scope — Prefect orchestrates but you provision compute (Kubernetes, Docker workers, EC2). For embedded/product use-cases, Prefect has no concept of multi-tenant RLS or per-user data isolation: every flow runs as the same service account. No visual DAG builder; flows are code-only. Serverless is Prefect's own infra (not scale-to-zero in the "no cold start" sense); hybrid self-host requires DevOps investment.

## Notes

Prefect 3 is the closest analogue to Nubi Flows: both are Postgres-backed, Python-native, and avoid Redis/Celery by default. Nubi Flows is intentionally narrower — it runs inside Nubi's RLS-aware multi-tenant stack so flows can safely query per-user data via JWT claims, something Prefect does not natively support.
