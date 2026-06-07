---
name: Dagster
tagline: Asset-centric orchestration — software-defined assets, lineage, and data quality
selfHost: "Yes — Dagster OSS is fully open source (Apache 2.0). Self-host requires Postgres (metadata/event log), a dagster-daemon process, and a webserver (Dagit). No Redis required. Dagster+ (cloud) adds managed hosting, branching deployments, and CI/CD."
pricing: "OSS: free to self-host. Dagster+ Solo: $10/month + $0.040/credit. Starter: $100/month + $0.035/credit. Pro: custom. Serverless compute: $0.01/compute-minute (no charge for hybrid/BYOC). Credits = asset materializations + ops executed."
pricingUnverified: false
sourceUrls:
  - https://dagster.io/pricing
  - https://dagster.io/vs/dagster-vs-airflow
  - https://docs.dagster.io/deployment
  - https://support.dagster.io/articles/3171123463-dagster-solo-and-starter-pricing-updates-may-2026
---

## Strength

Asset-centric model (Software-Defined Assets) is genuinely superior for dbt-heavy and data quality workflows — every pipeline step declares what data asset it produces, giving you automatic lineage, freshness tracking, and partition-aware re-materialization. Modern developer experience: type-checked resources, configurable jobs, built-in data quality checks, and first-class dbt integration. Cleaner than Airflow for teams building a modern lakehouse stack.

## Limitation

Steeper conceptual learning curve than Prefect — the asset/job/resource/sensor abstraction hierarchy takes time to internalise. Credit-based pricing (per asset materialization) can be surprising for high-frequency or fan-out pipelines. No native agent/LLM task kind; no multi-tenant per-user RLS context. Overkill for simple workflows embedded inside a product. Self-hosting requires running dagster-daemon + Dagit + Postgres.

## Notes

Dagster's Software-Defined Assets are a structural improvement over Airflow's task-centric model for data lineage use cases. Nubi Flows sits at a different altitude: a lightweight DAG engine embedded in the Nubi product stack for query/python/agent tasks with JWT-scoped RLS, not a standalone data engineering platform. For teams already building a Dagster-based data platform, Nubi Flows can complement (not replace) it.
