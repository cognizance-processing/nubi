---
name: Apache Airflow
tagline: Battle-tested DAG orchestration — the industry default for data engineering
selfHost: "Yes — Apache Airflow is fully open source (Apache 2.0). Self-host requires Postgres metadata DB, Redis (Celery executor) or Kubernetes (K8s executor), and a webserver + scheduler process. Managed options: AWS MWAA (~$350–$1,400/month), Google Cloud Composer (~$300/month), Astronomer Astro ($100–$5,000+/month)."
pricing: "Apache Airflow OSS: free to self-host. Managed services billed by environment size/capacity: AWS MWAA from ~$350/month (mw1.small); Cloud Composer from ~$300/month; Astronomer Astro from ~$100/month. No per-user license fee — cost is infrastructure."
pricingUnverified: false
sourceUrls:
  - https://airflow.apache.org/
  - https://aws.amazon.com/managed-workflows-for-apache-airflow/
  - https://automationatlas.io/answers/apache-airflow-pricing-explained-2026/
  - https://tasrieit.com/blog/managed-airflow-services-compared-2026
---

## Strength

The de facto industry standard — 80+ provider packages, the broadest ecosystem of operators, and the largest community. Every major cloud, data warehouse, and data tool has an Airflow operator. Proven at planet-scale: thousands of production DAGs, daily at companies like Airbnb, Lyft, and most large data orgs. Python DAG definitions are version-controlled and code-reviewed like any other software.

## Limitation

Heavy infrastructure footprint: requires Postgres, Redis (Celery) or Kubernetes (K8s executor), a scheduler process, and a webserver — significant DevOps overhead. Python DAG definition style (global-scope task instantiation) is error-prone and has a steep learning curve. No built-in asset-centric data lineage. No LLM/agent task kind natively. Designed for data engineering batch pipelines, not multi-tenant per-user workflow execution.

## Notes

Airflow is a serious distributed pipeline engine — it is emphatically not a lightweight alternative. Nubi Flows is appropriate for teams that want workflow logic embedded in their analytics product (with RLS, per-user params, and agent steps) without standing up a scheduler cluster. For large-scale ETL or data eng pipelines, Airflow or a managed equivalent remains the right tool.
