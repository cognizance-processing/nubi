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
    { label: 'Editor seats', desc: 'People who build dashboards, queries, and flows.' },
    { label: 'Embed views', desc: 'Embedded dashboard loads / mo — generous, since marginal cost ≈ $0.' },
    { label: 'Connector throughput', desc: 'GB scanned from your warehouse, after edge cache + pre-aggs.' },
    { label: 'AI calls', desc: 'Text-to-SQL, MCP tools, and agent steps.' },
    { label: 'Server-kernel time', desc: 'On-demand server kernels (native wheels) — scale-to-zero, only when used.' },
  ],
}

// ── Tiers (USD / month, billed annually) ────────────────────────────────────
export const TIERS = [
  {
    id: 'free',
    name: 'Free',
    price: '$0',
    cadence: 'forever',
    tagline: 'A real free tier — unlimited viewers, no gotchas.',
    cta: 'Start free',
    href: '/register',
    highlight: false,
    features: [
      'Unlimited dashboard views',
      'DuckDB-WASM kernel in the browser',
      '2 editor seats',
      '1 connector',
      '10k embed views / mo',
      '500 AI calls / mo',
      'Flows: query · python · noop',
      'Community support',
    ],
  },
  {
    id: 'pro',
    name: 'Pro',
    price: '$49',
    cadence: 'per month',
    tagline: 'For growing teams shipping embedded analytics.',
    cta: 'Start free trial',
    href: '/register',
    highlight: true,
    badge: 'Most popular',
    features: [
      'Everything in Free, plus:',
      '5 editor seats ($12/seat after)',
      'Unlimited connectors',
      'Edge cache + auto pre-aggregation',
      '250k embed views / mo',
      '10k AI calls / mo',
      'All Flow task kinds + scheduling',
      'AI / MCP authoring',
      'Email support',
    ],
  },
  {
    id: 'team',
    name: 'Team',
    price: '$249',
    cadence: 'per month',
    tagline: 'Governance and scale for multi-tenant SaaS.',
    cta: 'Start free trial',
    href: '/register',
    highlight: false,
    features: [
      'Everything in Pro, plus:',
      '15 editor seats ($10/seat after)',
      '2M embed views / mo',
      '100k AI calls / mo',
      'SSO, RBAC & audit logs',
      'Auth-as-code RLS policies',
      'Private VPC connector bridge',
      'Priority support',
    ],
  },
  {
    id: 'scale',
    name: 'Scale',
    price: '$1,000',
    cadence: 'per month',
    tagline: 'High-volume embedding with a named team behind you.',
    cta: 'Talk to us',
    href: '/register',
    highlight: false,
    badge: 'Dedicated support',
    features: [
      'Everything in Team, plus:',
      '50 editor seats',
      '20M embed views / mo',
      'Unlimited AI calls (fair use)',
      'On-demand server kernels',
      'Single-tenant deployment option',
      '99.9% uptime SLA',
      'Dedicated support — named contact, shared Slack, 1-business-hour response',
    ],
  },
]

export const ENTERPRISE_NOTE =
  'Need more than Scale — on-prem, custom SLAs, BAA/compliance, volume embed pricing? Enterprise is custom-quoted.'

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
function nubiAnnual(viewers, editors) {
  // Viewers are always free. Tier is chosen by editor count + scale.
  if (editors <= 2 && viewers <= 1000) return 0 // Free
  let base, included, overage
  if (editors <= 5) { base = 49; included = 5; overage = 12 }
  else if (editors <= 15) { base = 249; included = 15; overage = 10 }
  else { base = 1000; included = 50; overage = 12 } // Scale
  const monthly = base + Math.max(0, editors - included) * overage
  return monthly * 12
}

export const CALC_OPTIONS = [
  {
    name: 'Nubi', isNubi: true, note: 'Viewers free · flat plan',
    annual: (v, e) => nubiAnnual(v, e),
  },
  {
    name: 'Power BI', note: 'Pro seats, capped at F64 capacity',
    annual: (v) => Math.min(v * 14 * 12, 8400 * 12),
  },
  {
    name: 'Tableau', note: 'Viewer seats @ $15/mo',
    annual: (v) => v * 15 * 12,
  },
  {
    name: 'Metabase', note: 'Pro $575/mo + $12/embedded user',
    annual: (v) => 575 * 12 + v * 12 * 12,
  },
  {
    name: 'Preset', note: 'Embedded viewer licenses ($500 / 50)',
    annual: (v) => Math.ceil(Math.max(v, 1) / 50) * 500 * 12,
  },
  {
    name: 'Looker', note: 'Platform + ~$400/viewer/yr', estimate: true,
    annual: (v) => 60000 + v * 400,
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
    a: 'Yes. Free includes unlimited dashboard views and the in-browser kernel forever. You upgrade when you need more editors, connectors, embed volume, or governance — not to unlock basic usage.',
  },
  {
    q: 'Can I self-host?',
    a: 'Yes — the open-core is self-hostable. Managed cloud, SSO/RBAC/audit, and dedicated support are paid tiers.',
  },
  {
    q: 'What does “dedicated support” on Scale include?',
    a: 'A named contact, a shared Slack channel, a 1-business-hour first-response target, and a 99.9% uptime SLA. Onboarding and architecture review are included.',
  },
]
