"""Competitor pricing data for Nubi's pricing calculator.

Contains both BI/embedded analytics competitors and orchestration tool
competitors, structured for consumption by the public /pricing endpoint
and the landing-page pricing calculator.

Two competitor categories
--------------------------
bi:
    Embedded BI / analytics platforms: Cube, Metabase, Hex, Lightdash,
    Holistics, Luzmo, Embeddable.

orchestration:
    Data-pipeline / workflow orchestration tools: Prefect, Apache Airflow
    (self-hosted), Astronomer (managed Airflow), Dagster, Temporal, AWS
    MWAA, Google Cloud Composer, Mage.ai, Kestra, Windmill.

Data freshness
--------------
All prices were verified against official pricing pages in June 2026.
Update the ``_DATA_AS_OF`` constant and individual records when repricing.

Usage
-----
>>> from app.ee.billing.competitors import bi_competitors, orchestration_competitors
>>> bi = bi_competitors()
>>> orch = orchestration_competitors()
"""

from __future__ import annotations

from typing import Any

# Month/year string displayed in the /pricing response and UI.
_DATA_AS_OF = "June 2026"


# ---------------------------------------------------------------------------
# BI / Embedded analytics competitors
# ---------------------------------------------------------------------------

_BI_COMPETITORS: list[dict[str, Any]] = [
    {
        "tool": "Cube",
        "tagline": "Semantic layer & embedded analytics API",
        "model": "flat-subscription + compute-credits",
        "unit": "compute-credit",
        "pricing": {
            "cloud_starter": "$299/mo (1 compute-credit, 3 seats)",
            "cloud_professional": "$599/mo (2 credits, 10 seats)",
            "enterprise": "custom",
            "self_host": "free (OSS Core)",
        },
        "free_tier": True,
        "free_tier_detail": "OSS self-host free (Core); no permanent free managed tier",
        "per_seat": True,
        "per_seat_detail": "Seat-gated plans — 3 / 10 / custom seats per tier",
        "notable_limits": [
            "Per-seat pricing locks teams out of higher plans",
            "Compute credits metered separately on top of base fee",
            "No native flows/orchestration",
        ],
        "nubi_advantage": (
            "Nubi includes semantic querying, embedded analytics, and flows "
            "in a single subscription with no per-seat or per-credit metering."
        ),
    },
    {
        "tool": "Metabase",
        "tagline": "Open-source BI with embedded analytics",
        "model": "per-user-seat (cloud) or self-hosted",
        "unit": "user-seat",
        "pricing": {
            "oss_self_host": "free",
            "starter_cloud": "$500/mo (5 users incl., $10/extra user/mo)",
            "pro_cloud": "$500/mo (10 users, $20/extra/mo) + $500 embed add-on",
            "enterprise": "custom (on-prem)",
        },
        "free_tier": True,
        "free_tier_detail": "OSS self-host free (unlimited users); cloud: paid only",
        "per_seat": True,
        "per_seat_detail": "$10–20/user/mo overage on cloud plans",
        "notable_limits": [
            "Per-seat overage fees scale steeply with team growth",
            "Embedded analytics is a paid add-on ($500/mo on Pro)",
            "No native orchestration / flows",
        ],
        "nubi_advantage": (
            "Nubi provides unlimited seats at every tier — no per-user overage. "
            "Embedded analytics is bundled, not a separate add-on."
        ),
    },
    {
        "tool": "Hex",
        "tagline": "Collaborative data workspace & notebooks",
        "model": "per-seat subscription",
        "unit": "workspace-seat",
        "pricing": {
            "free": "$0 (1 user, 3 projects)",
            "starter": "$24/user/mo (billed annually)",
            "team": "$50/user/mo",
            "enterprise": "custom",
        },
        "free_tier": True,
        "free_tier_detail": "Free: 1 user, 3 projects, limited compute",
        "per_seat": True,
        "per_seat_detail": "$24–50/user/mo; compute units metered separately",
        "notable_limits": [
            "Pure per-seat model — cost scales directly with headcount",
            "Compute units capped; overages charged separately",
            "Notebook-centric; not an embedded analytics SDK",
        ],
        "nubi_advantage": (
            "Nubi is unlimited-seat at every tier — a 50-person team costs the "
            "same as a 5-person team. No per-seat penalty for growth."
        ),
    },
    {
        "tool": "Lightdash",
        "tagline": "Open-source BI on top of dbt",
        "model": "per-developer-seat (cloud) or self-hosted",
        "unit": "developer-seat",
        "pricing": {
            "oss_self_host": "free",
            "cloud_starter": "$50/developer/mo (unlimited viewers)",
            "cloud_pro": "$150/developer/mo",
            "enterprise": "custom",
        },
        "free_tier": True,
        "free_tier_detail": "OSS self-host free; cloud: developer seats only, viewers free",
        "per_seat": True,
        "per_seat_detail": "$50–150/developer/mo; viewer seats are free",
        "notable_limits": [
            "Per-developer-seat pricing; large dev teams pay proportionally",
            "Tightly coupled to dbt — not a standalone connector framework",
            "No flows / orchestration",
        ],
        "nubi_advantage": (
            "Nubi is not dbt-dependent and provides its own connector ecosystem. "
            "No per-developer seat fee."
        ),
    },
    {
        "tool": "Holistics",
        "tagline": "Self-service BI with code-based modeling",
        "model": "per-seat subscription",
        "unit": "user-seat",
        "pricing": {
            "free": "$0 (3 users, limited queries)",
            "team": "$50/user/mo",
            "enterprise": "custom",
        },
        "free_tier": True,
        "free_tier_detail": "Free: 3 users, 1 data source, 30-day data freshness",
        "per_seat": True,
        "per_seat_detail": "$50/user/mo on Team plan",
        "notable_limits": [
            "Per-seat model — 20 users = $1,000/mo on Team",
            "No embedded analytics SDK",
            "No native flows",
        ],
        "nubi_advantage": (
            "Nubi's embedded SDK + unlimited seats at $149/mo (Pro) undercuts "
            "$50/user/mo the moment a team exceeds 3 users."
        ),
    },
    {
        "tool": "Luzmo",
        "tagline": "Embedded analytics SDK for SaaS products",
        "model": "flat-subscription + embedded-session metering",
        "unit": "embedded-session",
        "pricing": {
            "starter": "$149/mo (5K sessions/mo, 2 workspaces)",
            "business": "$449/mo (20K sessions/mo, unlimited workspaces)",
            "enterprise": "custom",
        },
        "free_tier": False,
        "free_tier_detail": "14-day free trial only; no permanent free tier",
        "per_seat": False,
        "per_seat_detail": "Session-metered, not per-seat",
        "notable_limits": [
            "Session-based metering — high-traffic products pay proportionally",
            "No free tier; minimum $149/mo to start",
            "No flows / orchestration",
        ],
        "nubi_advantage": (
            "Nubi's session metering starts at a lower price point and includes "
            "query engine + flows — Luzmo is embedded-only."
        ),
    },
    {
        "tool": "Embeddable",
        "tagline": "Low-code embedded analytics components",
        "model": "flat-subscription",
        "unit": "plan tier",
        "pricing": {
            "starter": "$300/mo",
            "growth": "$600/mo",
            "enterprise": "custom",
        },
        "free_tier": False,
        "free_tier_detail": "No free tier; demo available on request",
        "per_seat": False,
        "per_seat_detail": "Flat subscription, not per-seat",
        "notable_limits": [
            "No free tier; $300/mo minimum",
            "React/Vue component library only — no full BI platform",
            "No flows, no query engine",
        ],
        "nubi_advantage": (
            "Nubi provides a full platform (query engine, BI, flows, embedded SDK) "
            "at the same price point as Embeddable's component library alone."
        ),
    },
]


# ---------------------------------------------------------------------------
# Orchestration / workflow competitors
# ---------------------------------------------------------------------------

_ORCHESTRATION_COMPETITORS: list[dict[str, Any]] = [
    {
        "tool": "Prefect Cloud",
        "tagline": "Modern data pipeline orchestration",
        "model": "flat-subscription + serverless-compute-overage",
        "unit": "serverless-minutes (seat-gated)",
        "pricing": {
            "free_hobby": "$0 (1 user, 5 deployments, 500 min/mo)",
            "starter": "$100/mo (3 users, 4,500 min/mo)",
            "team": "$400/mo (8 users, 13,500 min/mo)",
            "enterprise": "custom",
            "overage": "$0.005/min beyond quota (~$0.30/hr)",
            "self_host": "free (Prefect Server OSS)",
        },
        "free_tier": True,
        "free_tier_detail": "Hobby: 1 user, 5 deployments, 500 serverless min/mo",
        "per_execution": False,
        "per_seat": True,
        "per_seat_detail": "Seat-gated plans: 1 / 3 / 8 seats per tier",
        "metered_charges": ["serverless-minutes beyond quota"],
        "nubi_advantage": (
            "Nubi flows have no serverless-minute meter and no per-seat gate. "
            "A Prefect Team plan at $400/mo compares to Nubi Pro at $149/mo — "
            "but Nubi Pro includes BI, query engine, and connectors as well."
        ),
    },
    {
        "tool": "Apache Airflow (self-hosted)",
        "tagline": "Open-source workflow orchestration",
        "model": "infrastructure-cost (OSS software)",
        "unit": "cloud-VM + database + DevOps time",
        "pricing": {
            "license": "free (Apache 2.0)",
            "minimal_infra_monthly": "~$50–110/mo (single VM)",
            "production_k8s_monthly": "$200–2,000/mo",
            "effective_with_devops": "$500–3,000/mo (0.25–0.5 FTE overhead)",
        },
        "free_tier": True,
        "free_tier_detail": "OSS software is free; infrastructure and DevOps add ~$500–3,000/mo at real scale",
        "per_execution": False,
        "per_seat": False,
        "per_seat_detail": "No seat pricing; infra cost only",
        "metered_charges": [],
        "nubi_advantage": (
            "Zero infrastructure management, zero DevOps overhead. Nubi flows run on "
            "managed infrastructure — no Kubernetes clusters, no metadata DB, no "
            "Celery worker management."
        ),
    },
    {
        "tool": "Astronomer (Astro)",
        "tagline": "Managed Apache Airflow cloud",
        "model": "usage-based compute",
        "unit": "deployment-hour + worker-hour",
        "pricing": {
            "developer": "$0.35/hr/deployment + workers from $0.13/hr",
            "team": "$0.42/hr/deployment + dedicated clusters from $2.40/hr",
            "business": "custom",
            "enterprise": "custom",
            "typical_min_monthly": "~$300–600/mo for small production deployment",
        },
        "free_tier": False,
        "free_tier_detail": "No free managed tier; OSS Airflow is free (self-hosted)",
        "per_execution": False,
        "per_seat": False,
        "per_seat_detail": "Deployment-hour and worker-hour metering, not per-seat",
        "metered_charges": ["deployment-hours", "worker-hours", "dedicated-cluster-hours"],
        "nubi_advantage": (
            "No always-on deployment-hour fee. Astronomer charges $0.35/hr/deployment "
            "regardless of whether any flows are running — Nubi has no idle cost."
        ),
    },
    {
        "tool": "Dagster Cloud",
        "tagline": "Asset-oriented data orchestration",
        "model": "base-subscription + per-credit metering",
        "unit": "credit (1 credit = 1 asset materialization or 1 op execution)",
        "pricing": {
            "solo": "$10/mo + $0.040/credit",
            "starter": "$100/mo + $0.035/credit",
            "serverless_compute": "$0.010/min extra",
            "pro": "custom",
        },
        "free_tier": False,
        "free_tier_detail": "30-day free trial only; no permanent free plan",
        "per_execution": True,
        "per_seat": False,
        "per_seat_detail": "Credit-metered, not per-seat",
        "metered_charges": [
            "credits ($0.035–0.040 each; 1 credit = 1 asset materialization or 1 op)",
            "serverless-compute-minutes ($0.010/min)",
        ],
        "nubi_advantage": (
            "Nubi flows have no per-run credit meter. A pipeline that runs 10,000 "
            "ops/day on Dagster Starter racks up $350/mo in credit charges alone — "
            "on top of the $100/mo base fee. Nubi: zero per-run charge."
        ),
    },
    {
        "tool": "Temporal Cloud",
        "tagline": "Durable workflow execution engine",
        "model": "plan-minimum + per-Action metered",
        "unit": "Action (workflow event: start, heartbeat, signal, activity complete)",
        "pricing": {
            "essentials": "$100/mo (1M Actions included)",
            "business": "$500/mo (2.5M Actions included)",
            "enterprise": "custom (10M+ Actions)",
            "overage_per_million": "$50 (0–5M), $45 (5–10M), $40 (10–20M), $25 (200M+)",
            "active_storage": "$0.042/GBh",
            "retained_storage": "$0.00105/GBh",
            "free_credits": "$1,000 on signup; $6,000 for qualifying startups",
        },
        "free_tier": True,
        "free_tier_detail": "$1,000 in credits on signup; $6,000 for qualifying startups (<$30M funding)",
        "per_execution": True,
        "per_seat": False,
        "per_seat_detail": "Action-metered, not per-seat",
        "metered_charges": [
            "Actions ($50/M, scaling to $25/M at 200M+)",
            "active-workflow-state storage ($0.042/GBh)",
            "retained-history storage ($0.00105/GBh)",
        ],
        "nubi_advantage": (
            "Temporal is a general-purpose durable-workflow engine — not a BI platform. "
            "Nubi flows are included in the platform subscription with no per-Action metering. "
            "At 5M actions/mo, Temporal Essentials costs $300/mo in overage alone."
        ),
    },
    {
        "tool": "AWS MWAA",
        "tagline": "Managed Workflows for Apache Airflow (AWS)",
        "model": "per-environment-hour (standard) or per-task-hour (serverless)",
        "unit": "environment-hour or task-hour",
        "pricing": {
            "standard_small_env": "$0.49/hr (~$360/mo always-on)",
            "standard_large_env": "$0.99/hr (~$723/mo always-on)",
            "worker_small": "$0.055/hr",
            "worker_large": "$0.22/hr",
            "serverless": "$0.080/hr per task (min 1 min)",
            "example_large_10workers": "~$1,047/mo",
        },
        "free_tier": False,
        "free_tier_detail": "No free tier",
        "per_execution": False,
        "per_seat": False,
        "per_seat_detail": "Environment-hour metering, not per-seat",
        "metered_charges": [
            "environment-hours ($0.49–0.99/hr regardless of utilisation)",
            "worker-hours ($0.055–0.22/hr per additional worker)",
        ],
        "nubi_advantage": (
            "AWS MWAA charges ~$360/mo just to keep a Small environment alive — "
            "before a single task runs. Nubi has no always-on environment fee."
        ),
    },
    {
        "tool": "Google Cloud Composer",
        "tagline": "Managed Apache Airflow on GCP",
        "model": "per-DCU-hour",
        "unit": "DCU-hour (1 DCU = 1 vCPU-hr or 1 GB RAM-hr, whichever is higher)",
        "pricing": {
            "rate_us_central1": "$0.06/DCU-hr",
            "small_env_dcu": "~12 DCU/hr",
            "small_env_monthly": "~$518/mo",
            "billing_granularity": "10-minute intervals",
        },
        "free_tier": False,
        "free_tier_detail": "No free tier; GCP free tier does not cover Composer",
        "per_execution": False,
        "per_seat": False,
        "per_seat_detail": "DCU-hour metering, not per-seat",
        "metered_charges": [
            "DCU-hours (~12 DCU/hr for small env = ~$518/mo)",
            "GKE node pool costs",
            "persistent disk + Cloud SQL",
        ],
        "nubi_advantage": (
            "Google Cloud Composer starts at ~$518/mo for the smallest environment — "
            "and that's before GKE nodes and storage. Nubi Pro is $149/mo all-in."
        ),
    },
    {
        "tool": "Mage.ai",
        "tagline": "Modern data pipeline orchestration",
        "model": "base-subscription + compute-hours (Kubernetes executor only)",
        "unit": "block-runs/mo + compute-hour",
        "pricing": {
            "oss_self_host": "free",
            "starter": "$100/mo + $0.29/compute-hr (15K block runs/mo)",
            "team": "$500/mo (15K block runs/mo, 2 workspaces)",
            "plus": "$2,000/mo (50K block runs/mo, 6 workspaces)",
            "enterprise": "custom",
        },
        "free_tier": True,
        "free_tier_detail": "OSS self-host free (Docker/pip/conda); no free managed cloud tier",
        "per_execution": True,
        "per_seat": False,
        "per_seat_detail": "Block-run capped plans, not per-seat",
        "metered_charges": [
            "compute-hours ($0.29/hr when using Kubernetes executor)",
            "block-run quotas (hard cap; upgrade required when exceeded)",
        ],
        "nubi_advantage": (
            "Nubi flows are not block-run capped. Mage.ai's 15K block-run cap on "
            "Starter means pipelines with frequent micro-tasks will exceed quota quickly."
        ),
    },
    {
        "tool": "Kestra",
        "tagline": "Open-source orchestration with 1,400+ plugins",
        "model": "OSS-free / Cloud-PAYG / Enterprise-annual",
        "unit": "executions + resources (cloud); per-instance unlimited (enterprise)",
        "pricing": {
            "oss": "free (unlimited flows + executions, self-hosted)",
            "cloud": "PAYG — no published $/execution (early-adopter access, mid-2026)",
            "enterprise": "custom annual license (per-instance, unlimited executions)",
        },
        "free_tier": True,
        "free_tier_detail": "Full OSS tier: unlimited flows, unlimited executions, 1,400+ plugins, self-hosted",
        "per_execution": False,
        "per_seat": False,
        "per_seat_detail": "Not per-seat; infra/license cost only",
        "metered_charges": [],
        "nubi_advantage": (
            "Kestra is orchestration-only. Nubi adds embedded BI, a query engine, "
            "connectors, and dashboards alongside flows — all in one subscription."
        ),
    },
    {
        "tool": "Windmill",
        "tagline": "Developer platform for scripts, flows, and apps",
        "model": "per-seat + per-compute-unit (workers)",
        "unit": "developer-seat + CU (1 CU = worker with 2 GB RAM = 2 worker-GB-month)",
        "pricing": {
            "cloud_free": "$0 (unlimited executions, up to 10 SSO users, self-host)",
            "cloud_team": "$10/dev/mo + $10/operator/mo + $100/CU/mo (workers)",
            "cloud_enterprise": "from ~$120/mo (custom seats + CUs)",
            "self_host_oss": "free (unlimited executions)",
        },
        "free_tier": True,
        "free_tier_detail": "Self-hosted OSS: free, unlimited executions. Cloud: free with usage limits.",
        "per_execution": False,
        "per_seat": True,
        "per_seat_detail": "$10/dev/mo + $10/operator/mo on Cloud Team",
        "metered_charges": [
            "developer-seats ($10/mo each)",
            "operator-seats ($10/mo each)",
            "compute-units ($100/CU/mo; 1 CU = 2 worker-GB-month)",
        ],
        "nubi_advantage": (
            "Nubi is unlimited-seat at every tier. Windmill Cloud Team charges per "
            "developer AND per operator seat, plus compute units — the meter runs on "
            "three axes simultaneously."
        ),
    },
]


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def bi_competitors() -> list[dict[str, Any]]:
    """Return the BI / embedded analytics competitor list.

    Returns a list of competitor records suitable for JSON serialisation.
    Each record includes: ``tool``, ``tagline``, ``model``, ``unit``,
    ``pricing``, ``free_tier``, ``free_tier_detail``, ``per_seat``,
    ``per_seat_detail``, ``notable_limits``, ``nubi_advantage``.

    Returns
    -------
    list[dict]
        Competitor records for the BI/embedded category.
    """
    return list(_BI_COMPETITORS)


def orchestration_competitors() -> list[dict[str, Any]]:
    """Return the orchestration / workflow competitor list.

    Returns a list of competitor records suitable for JSON serialisation.
    Each record includes: ``tool``, ``tagline``, ``model``, ``unit``,
    ``pricing``, ``free_tier``, ``free_tier_detail``, ``per_execution``,
    ``per_seat``, ``metered_charges``, ``nubi_advantage``.

    Returns
    -------
    list[dict]
        Competitor records for the orchestration category.
    """
    return list(_ORCHESTRATION_COMPETITORS)


def all_competitors() -> dict[str, list[dict[str, Any]]]:
    """Return both competitor categories keyed by ``"bi"`` and ``"orchestration"``.

    Returns
    -------
    dict
        ``{"bi": [...], "orchestration": [...], "as_of": "<month year>"}``.
    """
    return {
        "bi": bi_competitors(),
        "orchestration": orchestration_competitors(),
        "as_of": _DATA_AS_OF,
    }
