/**
 * pricing.js — marketing pricing model + grounded competitor comparison data.
 *
 * This is PRESENTATION data for the public /pricing page only. Billing
 * ENFORCEMENT (metering, Paystack checkout, tier gating) lives in the EE tree
 * (src/ee/billing/* + backend) per the open-core split and is intentionally
 * NOT imported here.
 *
 * Competitor figures are sourced from public pricing pages (mid-2026); Looker
 * and Sigma are quote-only and reconstructed from reseller/analyst data — these
 * are marked `estimate: true`. See sourceUrl on each entry.
 */

// ── What we bill for (and what we never bill for) ───────────────────────────
export const BILLING_MODEL = {
  // The moat: browser compute ⇒ viewing a dashboard costs us ~$0, so we never
  // meter it. We charge for value created, not for every view.
  neverBilled: [
    'Dashboard views — compute runs in your users’ browsers',
    'Per-viewer “seats” for people who only look at dashboards',
    'Warehouse compute for cached / pre-aggregated reads',
  ],
  metered: [
    { label: 'Storage (GB)', desc: 'Object storage consumed by your org — maps directly to our S3/R2 bill.' },
    { label: 'Compute units', desc: 'Flow runs + query compute on our nodes — maps to container CPU time.' },
    { label: 'Embedded sessions', desc: 'Embedded dashboard loads / mo — egress + per-request compute cost.' },
    { label: 'AI calls', desc: 'Text-to-SQL, MCP tools, and agent steps — maps to Anthropic API tokens.' },
    { label: 'Agent / kernel runs', desc: 'On-demand server kernels (native wheels) — scale-to-zero, only when used.' },
  ],
}

// ── Overage rates (the "buy more when you need it" usage-wallet model) ───────
// Prices are anchored in USD (ZAR is just the currency we bill in). Each tier
// includes a monthly quota; usage beyond it draws from your prepaid wallet
// first, then lands on your monthly invoice. No per-viewer / per-seat overage.
export const OVERAGE_RATES = [
  { label: 'Storage', rate: '$0.10', unit: '/ GB / mo', desc: 'Beyond your plan’s included storage.' },
  { label: 'Compute', rate: '$6', unit: '/ 1,000 CU', desc: 'Flow + query compute past your monthly units.' },
  { label: 'AI calls', rate: '$0.30', unit: '/ call', desc: 'Text-to-SQL, MCP tools, and agent steps.' },
  { label: 'Embedded sessions', rate: '$3', unit: '/ 10,000', desc: 'Embedded dashboard loads past your quota.' },
  { label: 'Agent / kernel runs', rate: '$0.12', unit: '/ run', desc: 'On-demand server kernels (Team & Pro+).' },
]

export const OVERAGE_NOTE =
  'Need more of one thing — say more AI tokens — without jumping a whole tier? ' +
  'Top up your usage wallet and pay only for what you use, metered to the same ' +
  'rate at every paid tier. Overdraw and it’s simply added to your next invoice. ' +
  'Prices are in USD; we bill in ZAR at the daily rate.'

// ── Tiers (USD / month) ──────────────────────────────────────────────────────
// Source of truth: backend/app/ee/billing/tiers.py
// 5 tiers: free / starter / team / pro / enterprise
// ALL tiers — unlimited seats and viewers (no per-seat pricing at any tier).
// Metered: storage · compute units · embedded sessions · AI calls · agent runs.
export const TIERS = [
  {
    id: 'free',
    name: 'Free',
    price: '$0',
    cadence: 'forever',
    tagline: 'A real free tier — unlimited editors and viewers, no gotchas.',
    cta: 'Start free',
    href: '/register',
    highlight: false,
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      'DuckDB-WASM kernel in the browser',
      '1 GB storage · 500 compute units / mo',
      '3 connectors',
      '5 dashboards · 2 scheduled flows',
      '10k query row cap per execution',
      'Nubi branding on embeds',
      'Community support',
    ],
  },
  {
    id: 'starter',
    name: 'Starter',
    price: '$9',
    cadence: 'per month',
    tagline: 'For individuals, side-projects, and early-stage startups.',
    cta: 'Start free trial',
    href: '/register',
    highlight: false,
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '5 GB storage · 2,000 compute units / mo',
      '1,000 embedded sessions / mo',
      '5 connectors · 10 dashboards · 3 flows',
      '5 AI calls / mo',
      'Basic row-level security',
      'Google OAuth SSO',
      '7-day audit log',
    ],
  },
  {
    id: 'team',
    name: 'Team',
    price: '$49',
    cadence: 'per month',
    tagline: 'For small teams that outgrew Starter — without the Pro leap.',
    cta: 'Start free trial',
    href: '/register',
    highlight: false,
    features: [
      'Everything in Starter, plus:',
      'Unlimited editors & viewers — no per-seat charge',
      '15 GB storage · 6,000 compute units / mo',
      '5,000 embedded sessions / mo',
      '10 agent runs · 15 AI calls / mo',
      '15 connectors · 30 dashboards · 8 flows',
      'Full RLS with JWT claims · Google SSO',
      'Remove Nubi branding · 30-day audit log',
    ],
  },
  {
    id: 'pro',
    name: 'Pro',
    price: '$149',
    cadence: 'per month',
    tagline: 'For growing teams shipping embedded analytics.',
    cta: 'Start free trial',
    href: '/register',
    highlight: true,
    badge: 'Most popular',
    features: [
      'Everything in Team, plus:',
      'Unlimited editors & viewers — no per-seat charge',
      '50 GB storage · 15,000 compute units / mo',
      '25,000 embedded sessions / mo',
      '50 agent runs · 50 AI calls / mo',
      'All connectors · 100 dashboards · 20 flows',
      'Full RLS with JWT claims · Google + SAML SSO (1 IdP)',
      'Full white-label · custom domain · 90-day audit log',
      '99.5% uptime SLA',
    ],
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    price: '$1,000',
    cadence: 'per month',
    tagline: 'Unlimited scale, BYOC, and white-glove support.',
    cta: 'Talk to us',
    href: '/register',
    highlight: false,
    badge: 'Dedicated support',
    sla: {
      uptime: '99.95%',
      p1_response_minutes: 30,
      p2_response_hours: 2,
      support: 'Dedicated CSM · 24/7 P1 on-call · private Slack channel',
    },
    features: [
      'Everything in Pro, plus:',
      'Unlimited editors & viewers — no per-seat charge',
      '500 GB+ storage · 200,000 compute units / mo',
      'Unlimited embedded sessions',
      '1,000 agent runs · 500 AI calls / mo',
      'Full RLS + HIPAA-ready · custom JS SDK',
      'SAML (unlimited IdPs) + SCIM · multi-tenant workspaces',
      'Dedicated CSM · 99.95% uptime SLA · P1 < 30 min',
      'BYOC / air-gap / on-prem · BAA on request',
    ],
  },
]

export const ENTERPRISE_NOTE =
  'Enterprise includes a contractual 99.95% SLA, dedicated CSM, and 24/7 P1 on-call. Need BYOC, on-prem, or custom pricing? Enterprise is custom-quoted.'

// ── BI competitor comparison: the "viewer tax" ──────────────────────────────
// cost500 = illustrative annual cost to serve ~500 dashboard viewers, BEFORE
// warehouse compute, derived from each vendor's public model.
export const BI_COMPARISON = [
  {
    name: 'Nubi',
    isNubi: true,
    model: 'Flat plan — viewers are free (browser compute)',
    cost500: '$0 / viewer',
    computeExtra: 'None — runs client-side',
    estimate: false,
  },
  {
    name: 'Tableau',
    model: 'Per-viewer seat ($15–$35/viewer/mo)',
    cost500: '≈ $90k–$210k / yr',
    computeExtra: 'Bundled (extracts) ',
    sourceUrl: 'https://www.tableau.com/pricing',
    estimate: false,
  },
  {
    name: 'Looker',
    model: 'Per-viewer seat (~$400/viewer/yr) + platform',
    cost500: '≈ $200k / yr + ~$60k platform',
    computeExtra: 'Your warehouse, per query',
    sourceUrl: 'https://cloud.google.com/looker/pricing',
    estimate: true,
  },
  {
    name: 'Power BI',
    model: 'Pro $14/viewer/mo — or F64 capacity (~$8.4k/mo) to free viewers',
    cost500: '≈ $84k / yr (Pro seats)',
    computeExtra: 'Capacity throttles under load',
    sourceUrl: 'https://www.microsoft.com/en-us/power-platform/products/power-bi/pricing',
    estimate: false,
  },
  {
    name: 'Metabase',
    model: 'Pro $575/mo + $12 / embedded user / mo',
    cost500: '≈ $79k / yr',
    computeExtra: 'Your database',
    sourceUrl: 'https://www.metabase.com/pricing/',
    estimate: false,
  },
  {
    name: 'Hex',
    model: '$36–$75 / editor + per-minute kernel compute; embed = Enterprise',
    cost500: 'Custom (Enterprise)',
    computeExtra: 'Per-minute server kernels',
    sourceUrl: 'https://hex.tech/pricing/',
    estimate: false,
  },
  {
    name: 'Cube',
    model: 'Consumption — $0.15–$0.30 / CCU (no per-viewer)',
    cost500: 'Scales with query load',
    computeExtra: 'Your warehouse + CCU burn',
    sourceUrl: 'https://cube.dev/pricing',
    estimate: false,
  },
  {
    name: 'Sigma',
    model: 'Per-seat + usage credits; embed adds 2–3×',
    cost500: '≈ $60k+ / yr (quote)',
    computeExtra: 'Live warehouse, per query',
    sourceUrl: 'https://www.sigmacomputing.com/pricing',
    estimate: true,
  },
  {
    name: 'Preset',
    model: 'Embedded viewers licensed — from $500/mo per 50',
    cost500: '≈ $60k / yr (list)',
    computeExtra: 'Your database',
    sourceUrl: 'https://preset.io/pricing/',
    estimate: false,
  },
]

// ── Orchestration comparison: Flows vs standalone orchestrators ─────────────
export const ORCH_COMPARISON = [
  {
    name: 'Nubi Flows',
    isNubi: true,
    model: 'Included in your plan',
    floor: '$0 extra',
    infra: 'Runs on the Postgres you already have',
    meter: 'No per-task / per-credit metering',
    estimate: false,
  },
  {
    name: 'Astronomer (Astro)',
    model: 'Usage — deployment + worker hours',
    floor: '≈ $255/mo per env (before workers)',
    infra: 'Managed Airflow control plane',
    meter: 'Per compute-hour',
    sourceUrl: 'https://www.astronomer.io/pricing/',
    estimate: false,
  },
  {
    name: 'AWS MWAA',
    model: 'Per environment-hour + workers',
    floor: '≈ $365/mo min (small env, 24/7)',
    infra: 'Airflow + Celery (managed) in your VPC',
    meter: 'Per env-hour + CloudWatch',
    sourceUrl: 'https://aws.amazon.com/managed-workflows-for-apache-airflow/pricing/',
    estimate: false,
  },
  {
    name: 'GCP Composer',
    model: 'Env fee + vCPU/GB + GKE/Cloud SQL',
    floor: '≈ $250–$610/mo env fee',
    infra: 'Airflow on always-on GKE',
    meter: 'Per vCPU-hr + storage',
    sourceUrl: 'https://cloud.google.com/composer/pricing',
    estimate: false,
  },
  {
    name: 'Prefect Cloud',
    model: 'Per seat',
    floor: '$100/mo Starter → $400/mo Team',
    infra: 'Postgres-backed (self-host) or managed',
    meter: 'Serverless-minute allowance',
    sourceUrl: 'https://www.prefect.io/pricing',
    estimate: false,
  },
  {
    name: 'Microsoft Fabric',
    model: 'Capacity SKU (F2+) billed per CU-hour',
    floor: '≈ $263/mo (F2, pay-as-you-go, 24/7)',
    infra: 'Always-on capacity; pipelines share the SKU',
    meter: 'Per capacity-unit-hour (throttles at cap)',
    sourceUrl: 'https://azure.microsoft.com/en-us/pricing/details/microsoft-fabric/',
    estimate: false,
  },
  {
    name: 'Apache Airflow (self-host)',
    model: 'Free OSS — you run + operate it',
    floor: '$0 license, but ~$300–$800/mo infra + ops',
    infra: 'Scheduler + workers + Redis/Celery + metadata DB',
    meter: 'None — you pay for the boxes + on-call',
    sourceUrl: 'https://airflow.apache.org/',
    estimate: true,
  },
  {
    name: 'Dagster+',
    model: 'Base + per-credit',
    floor: '$100/mo + credits (no bundle since May 2026)',
    infra: 'Postgres + agents / serverless',
    meter: 'Per materialization + op',
    sourceUrl: 'https://dagster.io/pricing',
    estimate: false,
  },
  {
    name: 'Temporal Cloud',
    model: 'Base + per-Action',
    floor: '$100/mo Essentials',
    infra: 'Cassandra/Postgres + Elasticsearch (self-host)',
    meter: 'Per Action ($50/M)',
    sourceUrl: 'https://temporal.io/pricing',
    estimate: false,
  },
]

// ── Cost calculator ─────────────────────────────────────────────────────────
// Illustrative ANNUAL USD cost as a function of dashboard viewers + editors,
// derived from each vendor's public model (see BI_COMPARISON sources). These
// are estimates for comparison, not quotes.
//
// NOTE: Nubi has NO per-seat pricing at any tier (seats are unlimited).
// Tier selection is driven by embedded-session volume, not editor count.
// Viewers map to embedded sessions (1 viewer ≈ ~10 sessions/mo estimate).
function nubiAnnual(viewers, editors) {
  // Viewers and editors are always free of seat charges.
  // Tier is chosen by estimated embedded-session volume.
  const estimatedSessions = viewers * 10 // rough 10 sessions/viewer/month
  let base
  if (estimatedSessions <= 0 && editors <= 10) {
    base = 0    // Free (no embedded sessions, small team)
  } else if (estimatedSessions <= 1000) {
    base = 9    // Starter — 1,000 sessions/mo
  } else if (estimatedSessions <= 5000) {
    base = 49   // Team — 5,000 sessions/mo
  } else if (estimatedSessions <= 25000) {
    base = 149  // Pro — 25,000 sessions/mo
  } else {
    base = 1000 // Enterprise — unlimited sessions
  }
  return base * 12
}

export const CALC_OPTIONS = [
  {
    name: 'Nubi', isNubi: true, note: 'Unlimited seats — viewers AND editors free; session-metered ($0/$9/$49/$149/$1k)',
    // Editors are free at every tier — the bar never moves with editor count.
    annual: (v, e) => nubiAnnual(v, e),
  },
  {
    name: 'Power BI', note: 'Pro $14/user (viewers + editors), capped at F64 capacity',
    annual: (v, e) => Math.min((v + (e || 0)) * 14 * 12, 8400 * 12),
  },
  {
    name: 'Tableau', note: 'Viewers @ $15/mo + Creator (editor) seats @ $70/mo',
    annual: (v, e) => v * 15 * 12 + (e || 0) * 70 * 12,
  },
  {
    name: 'Metabase', note: 'Pro $575/mo + $12/embedded viewer + $12/editor seat',
    annual: (v, e) => 575 * 12 + v * 12 * 12 + (e || 0) * 12 * 12,
  },
  {
    name: 'Preset', note: 'Embedded viewers ($500 / 50) + Creator seats @ $40/mo',
    annual: (v, e) => Math.ceil(Math.max(v, 1) / 50) * 500 * 12 + (e || 0) * 40 * 12,
  },
  {
    name: 'Looker', note: 'Platform + ~$400/viewer/yr + ~$600/developer seat', estimate: true,
    annual: (v, e) => 60000 + v * 400 + (e || 0) * 600,
  },
]

// ── Orchestration cost calculator (the SECOND calculator) ───────────────────
// FAIR, GROUNDED comparison. ANNUAL USD as a function of (environments, monthly
// flow compute-hours). Each formula maps to the vendor's PUBLISHED model (see
// ORCH_COMPARISON sourceUrl) — figures are directional estimates, not quotes.
//
// The honest distinction this shows:
//  • Standalone orchestrators bill for ALWAYS-ON infra/seats PER ENVIRONMENT,
//    regardless of how little you run.
//  • Nubi Flows has NO per-environment / per-seat floor — it is metered on the
//    compute it actually consumes (compute units, $6 / 1,000 CU; 1 CU ≈ 1
//    compute-minute ⇒ ≈ $0.36 / compute-hour). It is NOT free: light workloads
//    are covered by your plan's included compute quota, heavy ones pay overage.
//
// `annual(envs, hours)` — envs = isolated environments, hours = flow
// compute-hours / month.
const NUBI_COMPUTE_USD_PER_HOUR = 0.36 // $6 / 1,000 CU @ 60 CU per compute-hour

export const ORCH_CALC_OPTIONS = [
  {
    name: 'Nubi Flows', isNubi: true,
    note: 'No per-env bill — metered compute only (~$0.36/compute-hr; plan quota included)',
    // Honest, conservative: bills compute from hour 0 (your included quota
    // makes light use effectively free, so this slightly OVER-states Nubi).
    annual: (envs, hours) => Math.round((hours || 0) * NUBI_COMPUTE_USD_PER_HOUR * 12),
  },
  {
    name: 'Prefect Cloud', note: '$100/mo Starter → $400/mo Team (per-seat)',
    annual: (envs) => (Math.max(1, envs) <= 1 ? 100 : 400) * 12,
  },
  {
    name: 'Microsoft Fabric', note: 'F2 capacity 24/7 (~$263/mo per env); throttles at cap',
    annual: (envs) => 263 * 12 * Math.max(1, envs),
  },
  {
    name: 'AWS MWAA', note: 'Small env ~$365/mo (24/7) per env + worker hours',
    annual: (envs, hours) => Math.round((365 * Math.max(1, envs) + (hours || 0) * 0.05) * 12),
  },
  {
    name: 'GCP Composer', note: 'Env fee + GKE/Cloud SQL (~$400/mo per env)',
    annual: (envs) => 400 * 12 * Math.max(1, envs),
  },
  {
    name: 'Apache Airflow (self-host)', note: 'Infra ~$400/mo per env + on-call ops', estimate: true,
    annual: (envs) => 400 * 12 * Math.max(1, envs) + 6000,
  },
]

export const PRICING_FAQ = [
  {
    q: 'Why don’t you charge per viewer?',
    a: 'Because we don’t pay per viewer. Dashboards compute in the user’s browser (Pyodide + DuckDB-WASM), so an extra viewer costs us essentially nothing — and we pass that on. You’re billed for editors, AI, and warehouse throughput, never for someone looking at a chart.',
  },
  {
    q: 'What counts as an “embed view”?',
    a: 'One load of an embedded dashboard in your app. Re-renders, cross-filters, and interactions within a session are free — and identical queries across viewers collapse to one warehouse hit via the content-hashed edge cache.',
  },
  {
    q: 'Do I need a separate orchestrator like Airflow or Prefect?',
    a: 'No. Flows is built in and runs on the same Postgres as the rest of Nubi — no Redis, no Celery, no separate control plane to pay for or operate. Retries, timeouts, result caching, and RLS-aware execution are included.',
  },
  {
    q: 'Is there a free tier I can actually use in production?',
    a: 'Yes. Free includes unlimited editors, unlimited dashboard views, and the in-browser kernel forever. You upgrade when you need more connectors, embed volume, AI calls, or governance — not to unlock basic usage.',
  },
  {
    q: 'Can I self-host?',
    a: 'Yes — the open-core is self-hostable. Managed cloud, SSO/RBAC/audit, and dedicated support are paid tiers.',
  },
  {
    q: 'What does “dedicated support” on Enterprise include?',
    a: 'A dedicated Customer Success Manager (CSM), a private Slack/Teams channel, 24/7 P1 on-call (< 30 min first response for site-down incidents), P2 < 2 hours, monthly business reviews, and a contractual 99.95% uptime SLA. Onboarding, architecture review, and optional BYOC deployment are included.',
  },
]
