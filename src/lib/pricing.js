/**
 * pricing.js — public pricing data fetcher (src/lib/pricing.js)
 *
 * Fetches tier definitions, FX rate, and competitor data from the public
 * GET /api/v1/pricing endpoint.  This is CORE (not EE) — no auth token is
 * attached, and no EE imports are used.  Safe to import from landing pages,
 * docs, and the OSS distribution.
 *
 * API contract
 * ------------
 * GET /api/v1/pricing
 *   → {
 *       tiers: TierInfo[],            // same shape as billing.js FALLBACK_TIERS
 *       fx: { rate, updated_at, fallback },
 *       competitors_bi: CompetitorEntry[],
 *       competitors_orchestration: CompetitorEntry[],
 *     }
 *
 * Graceful degradation
 * --------------------
 * If the endpoint returns 404 (not yet deployed) or any network error the
 * helpers return static fallback data so the pricing calculator renders.
 *
 * The static fallbacks are identical to the data in src/lib/ee/billing.js
 * and the June 2026 orchestration research artifact — they are duplicated
 * here deliberately so core components have zero dependency on EE modules.
 */

// ---------------------------------------------------------------------------
// FX helpers (duplicated from ee/billing.js so core is EE-free)
// ---------------------------------------------------------------------------

/**
 * ZAR rounding: ceil to nearest R10 (protects margin during ZAR weakness).
 * Matches the backend formula: ceil_to_nearest_10(usd * rate * 1.02)
 *
 * @param {number} usd
 * @param {number} rate  USD→ZAR rate
 * @returns {number}
 */
export function computeZar(usd, rate) {
  if (!usd || !rate) return 0
  const raw = usd * rate * 1.02
  return Math.ceil(raw / 10) * 10
}

/**
 * Format a ZAR integer as a locale string, e.g. 1310 → "R 1,310"
 *
 * @param {number} zar
 * @returns {string}
 */
export function formatZar(zar) {
  if (!zar && zar !== 0) return 'R 0'
  return 'R ' + Math.round(zar).toLocaleString('en-ZA')
}

// ---------------------------------------------------------------------------
// Static fallback tiers — Free / Starter / Team / Pro / Enterprise
// ---------------------------------------------------------------------------

/** @type {import('./ee/billing.js').TierInfo[]} */
export const FALLBACK_TIERS = [
  {
    id: 'free',
    name: 'Free',
    usd_monthly: 0,
    price_zar: 0,
    price_label: 'Free forever',
    annual_usd: null,
    annual_zar_monthly_equiv: null,
    seats: null,
    description: 'For indie devs, OSS evaluators, and small experiments.',
    features: [
      'Unlimited editors & viewers',
      '1 GB storage',
      '500 compute units / month',
      'Up to 5 dashboards',
      '2 scheduled flows',
      '3 built-in connectors (CSV, DuckDB, Postgres)',
      '10,000 row query cap per execution',
      'Nubi branding on all embeds',
      'Community support',
    ],
    cta_label: 'Get started free',
    highlight: false,
    is_enterprise: false,
    has_sla: false,
  },
  {
    id: 'starter',
    name: 'Starter',
    usd_monthly: 9,
    // ceil10($9 × 16.26 × 1.02) = ceil10(R149.35) = R150
    price_zar: 150,
    price_label: 'R 150 / month',
    annual_usd: 90,
    // ceil10($9 × 10/12 × 16.26 × 1.02) = ceil10(R124.46) = R130
    annual_zar_monthly_equiv: 130,
    seats: null,
    description: 'For hobbyists and side-projects that need more headroom.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '5 GB storage',
      '2,000 compute units / month',
      '1,000 embedded sessions / month',
      '5 connectors',
      '10 dashboards · 3 scheduled flows',
      '5 AI calls / month',
      'Basic row-level security',
      'Nubi badge removable',
      'Usage wallet — pay-as-you-go overages',
      'Email support',
    ],
    cta_label: 'Upgrade to Starter',
    highlight: false,
    is_enterprise: false,
    has_sla: false,
  },
  {
    id: 'team',
    name: 'Team',
    usd_monthly: 49,
    // ceil10($49 × 16.26 × 1.02) = ceil10(R812.77) = R820
    price_zar: 820,
    price_label: 'R 820 / month',
    annual_usd: 490,
    // ceil10($49 × 10/12 × 16.26 × 1.02) = ceil10(R677.31) = R680
    annual_zar_monthly_equiv: 680,
    seats: null,
    description: 'For small teams collaborating on production analytics.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '15 GB storage',
      '6,000 compute units / month',
      '5,000 embedded sessions / month',
      '15 connectors (incl. cloud)',
      '30 dashboards · 8 scheduled flows',
      '15 AI calls / month · 10 agent / kernel runs',
      'Basic row-level security',
      'Nubi badge removable',
      'Usage wallet — pay-as-you-go overages',
      'Email support',
    ],
    cta_label: 'Upgrade to Team',
    highlight: false,
    is_enterprise: false,
    has_sla: false,
  },
  {
    id: 'pro',
    name: 'Pro',
    usd_monthly: 149,
    // ceil10($149 × 16.26 × 1.02) = ceil10(R2471.86) = R2480
    price_zar: 2480,
    price_label: 'R 2,480 / month',
    annual_usd: 1490,
    // ceil10($149 × 10/12 × 16.26 × 1.02) = ceil10(R2059.88) = R2060
    annual_zar_monthly_equiv: 2060,
    seats: null,
    description: 'For growing teams shipping production embedded analytics.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '50 GB storage',
      '15,000 compute units / month',
      '25,000 embedded sessions / month',
      '50 AI calls / month · 50 agent / kernel runs',
      'Lakehouse queries — pay per TiB scanned ($5/TiB, first 1 TiB/mo free)',
      'All connectors',
      '100 dashboards · 20 scheduled flows',
      'Full RLS with JWT claims',
      'Google OAuth + SAML (1 IdP)',
      'Full white-label (custom domain)',
      '90-day audit log',
      'Usage wallet — prepaid credits, auto-topup',
      '99.5% uptime SLA',
    ],
    cta_label: 'Upgrade to Pro',
    highlight: true,
    is_enterprise: false,
    has_sla: false,
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    usd_monthly: 1000,
    // ceil10($1000 × 16.26 × 1.02) = ceil10(R16585.20) = R16590
    price_zar: 16590,
    price_label: 'From R 16,590 / month',
    annual_usd: 10000,
    // ceil10($1000 × 10/12 × 16.26 × 1.02) = ceil10(R13821) = R13830
    annual_zar_monthly_equiv: 13830,
    seats: null,
    description: 'For enterprise teams that need SLA guarantees and dedicated support.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '500 GB+ storage (hosted) or unlimited (BYOC)',
      '200,000 compute units / month',
      'Unlimited embedded sessions',
      '500 AI calls / month · 1,000 agent / kernel runs',
      'Lakehouse queries — pay per TiB scanned ($5/TiB, first 1 TiB/mo free)',
      'All connectors + custom connector SDK',
      'Unlimited dashboards & scheduled flows',
      'Full RLS + host-signed JWT pass-through',
      'SAML (unlimited IdPs) + SCIM',
      'Full white-label + custom JS SDK',
      'Unlimited audit log + SIEM export',
      'Usage wallet — prepaid credits, auto-topup, spend cap',
      'BYOC / air-gap / on-prem deployment',
      'BAA / HIPAA on request',
    ],
    cta_label: 'Contact sales',
    highlight: false,
    is_enterprise: true,
    has_sla: true,
    sla: {
      uptime: '99.99%',
      response_time: '4-hour critical / 8-hour standard',
      support: 'Named dedicated support engineer',
    },
  },
]

// ---------------------------------------------------------------------------
// Static fallback: BI / Embedded Analytics competitors
// ---------------------------------------------------------------------------

/**
 * Each competitor model: pricing as a pure function (usage, seats) → USD/month.
 * usage = { storage_gb, compute_units, embedded_sessions, agent_runs, connectors }
 * seats = { editors, viewers }
 *
 * Data sourced from publicly available pricing pages, June 2026.
 *
 * @type {Array<{
 *   id: string,
 *   name: string,
 *   url: string,
 *   note: string,
 *   highlight_seat_penalty: boolean,
 *   model: (usage: object, seats: object) => number,
 * }>}
 */
export const FALLBACK_COMPETITORS_BI = [
  {
    id: 'metabase_pro',
    name: 'Metabase Pro',
    url: 'https://www.metabase.com/pricing',
    note: '$575/mo base + $12/interactive viewer (10 included)',
    highlight_seat_penalty: true,
    model({ embedded_sessions }, { viewers }) {
      const base = 575
      const effectiveViewers = embedded_sessions > 0
        ? Math.max(viewers, Math.ceil(embedded_sessions / 10))
        : viewers
      return base + Math.max(0, effectiveViewers - 10) * 12
    },
  },
  {
    id: 'holistics_standard',
    name: 'Holistics Standard',
    url: 'https://www.holistics.io/pricing',
    note: '$1,000/mo flat (annual) — unlimited viewers',
    highlight_seat_penalty: false,
    model: () => 1000,
  },
  {
    id: 'holistics_scs',
    name: 'Holistics SCS',
    url: 'https://www.holistics.io/pricing',
    note: '$2,000/mo flat (annual) — SAML/SCIM/RBAC',
    highlight_seat_penalty: false,
    model: () => 2000,
  },
  {
    id: 'lightdash_pro',
    name: 'Lightdash Cloud Pro',
    url: 'https://www.lightdash.com/pricing',
    note: '$3,000/mo flat — unlimited seats & viewers',
    highlight_seat_penalty: false,
    model: () => 3000,
  },
  {
    id: 'hex_team',
    name: 'Hex Team',
    url: 'https://hex.tech/pricing',
    note: '$75/editor/mo + compute hours',
    highlight_seat_penalty: true,
    model({ compute_units }, { editors }) {
      return editors * 75 + (compute_units / 1000) * 30
    },
  },
  {
    id: 'count_pro',
    name: 'Count Pro',
    url: 'https://count.co/pricing',
    note: '$49/editor/mo — viewers free',
    highlight_seat_penalty: true,
    model: (_, { editors }) => editors * 49,
  },
  {
    id: 'embeddable_lite',
    name: 'Embeddable Lite',
    url: 'https://embeddable.com/pricing',
    note: '$499/mo for 1,000 sessions; $200 per additional 500',
    highlight_seat_penalty: false,
    model({ embedded_sessions }) {
      const base = 499
      if (embedded_sessions <= 1000) return base
      return base + Math.ceil((embedded_sessions - 1000) / 500) * 200
    },
  },
  {
    id: 'luzmo_starter',
    name: 'Luzmo Starter',
    url: 'https://www.luzmo.com/pricing',
    note: '~$540/mo (EUR-priced, annual) — MAU-based',
    highlight_seat_penalty: false,
    model({ embedded_sessions }) {
      const estimatedMau = embedded_sessions / 4
      return estimatedMau <= 250 ? 540 : 2175
    },
  },
  {
    id: 'preset_professional',
    name: 'Preset Professional',
    url: 'https://preset.io/pricing',
    note: '$20/user/mo + $500/mo embed add-on for 50 viewers',
    highlight_seat_penalty: true,
    model({ embedded_sessions }, { editors, viewers }) {
      const seatCost = (editors + viewers) * 20
      const embedAddon = embedded_sessions > 0 ? 500 : 0
      const viewerOverage = embedded_sessions > 0
        ? Math.max(0, Math.ceil(embedded_sessions / 10) - 50) * 10
        : 0
      return seatCost + embedAddon + viewerOverage
    },
  },
]

// ---------------------------------------------------------------------------
// Static fallback: Data Orchestration competitors (June 2026)
// ---------------------------------------------------------------------------

/**
 * Orchestration competitors.  The usage object here uses different keys:
 * { flow_runs_per_month, workers, seats }
 * to match orchestration pricing units (runs, workers, seats/users).
 *
 * Data sources: research artifact (orchestration-pricing-research).
 *
 * @type {Array<{
 *   id: string,
 *   name: string,
 *   url: string,
 *   note: string,
 *   model_type: 'per-run'|'per-seat'|'flat'|'infra'|'per-action',
 *   model: (orchestration: object) => number,
 * }>}
 */
export const FALLBACK_COMPETITORS_ORCHESTRATION = [
  {
    id: 'prefect_team',
    name: 'Prefect Cloud Team',
    url: 'https://www.prefect.io/pricing',
    note: '$400/mo (8 seats, 13,500 serverless min/mo); overage $0.005/min',
    model_type: 'flat',
    model({ serverless_minutes = 5000, seats = 5 }) {
      // Team plan: $400/mo base (up to 8 seats, 13,500 min)
      // Starter plan: $100/mo (up to 3 seats, 4,500 min)
      const base = seats <= 3 ? 100 : 400
      const included = seats <= 3 ? 4500 : 13500
      const overage = Math.max(0, serverless_minutes - included) * 0.005
      return base + overage
    },
  },
  {
    id: 'astronomer',
    name: 'Astronomer (Astro)',
    url: 'https://www.astronomer.io/pricing/',
    note: '~$0.35/hr/deployment + $0.13/hr/worker; typical small-prod ~$400-600/mo',
    model_type: 'infra',
    model({ deployments = 1, workers = 2, hours_per_month = 730 }) {
      const deploymentCost = deployments * 0.35 * hours_per_month
      const workerCost = workers * 0.13 * hours_per_month
      return deploymentCost + workerCost
    },
  },
  {
    id: 'airflow_self_host',
    name: 'Apache Airflow (self-host)',
    url: 'https://airflow.apache.org',
    note: 'OSS free; infra $50-110/mo minimal, $200-2,000/mo production K8s',
    model_type: 'infra',
    model({ workers = 2 }) {
      // Rough minimal K8s setup
      return workers <= 2 ? 110 : 300 + workers * 50
    },
  },
  {
    id: 'dagster_starter',
    name: 'Dagster Cloud Starter',
    url: 'https://dagster.io/pricing',
    note: '$100/mo + $0.035/credit; 1 credit = 1 asset materialization or op run',
    model_type: 'per-run',
    model({ flow_runs_per_month = 5000, assets_per_run = 2 }) {
      const base = 100
      const credits = flow_runs_per_month * assets_per_run
      return base + credits * 0.035
    },
  },
  {
    id: 'temporal_essentials',
    name: 'Temporal Cloud Essentials',
    url: 'https://temporal.io/pricing',
    note: '$100/mo (1M Actions incl.); overage $50/M actions',
    model_type: 'per-action',
    model({ actions_per_month = 500000 }) {
      const base = 100 // Essentials — includes 1M actions
      const overage = Math.max(0, actions_per_month - 1000000) / 1000000 * 50
      return base + overage
    },
  },
  {
    id: 'aws_mwaa',
    name: 'AWS MWAA (Small)',
    url: 'https://aws.amazon.com/managed-workflows-for-apache-airflow/pricing/',
    note: '$0.49/hr small env (~$360/mo always-on) + $0.055/hr/worker',
    model_type: 'infra',
    model({ workers = 2, hours_per_month = 730 }) {
      const envCost = 0.49 * hours_per_month // small env always-on
      const workerCost = workers * 0.055 * hours_per_month
      return envCost + workerCost
    },
  },
  {
    id: 'gcp_composer',
    name: 'Google Cloud Composer 3',
    url: 'https://cloud.google.com/composer/pricing',
    note: '~$518/mo (small env, us-central1); $0.06/DCU-hr',
    model_type: 'infra',
    model({ dcu_per_hour = 12, hours_per_month = 730 }) {
      // DCU = vCPU-hr or GB RAM-hr; small env ~12 DCU/hr
      return dcu_per_hour * 0.06 * hours_per_month
    },
  },
  {
    id: 'mage_starter',
    name: 'Mage.ai Starter',
    url: 'https://www.mage.ai/pricing',
    note: '$100/mo + $0.29/compute-hr; 15K block runs/mo',
    model_type: 'per-run',
    model({ block_runs = 10000, compute_hours = 10 }) {
      const base = 100
      const overageBlocks = Math.max(0, block_runs - 15000) * 0.01 // rough estimate
      const computeCost = compute_hours * 0.29
      return base + overageBlocks + computeCost
    },
  },
]

// ---------------------------------------------------------------------------
// Lakehouse data pricing — pay-per-scan + storage (BigQuery-comparable model)
// ---------------------------------------------------------------------------

/**
 * Nubi lakehouse: pay per TiB scanned + storage on Cloudflare R2.
 * Dashboard views are FREE — they compute in the user's browser (DuckDB-WASM).
 * The first 1 TiB of scan per month is free (BigQuery parity).
 *
 * Pricing (USD):
 *   Scan:    $5.00 / TiB  (BigQuery charges $6.25/TiB — we are cheaper)
 *   Storage: $0.02 / GB-month  (R2 rate — same as BigQuery's storage tier)
 *   Free:    first 1 TiB scanned / month always free
 */
export const LAKEHOUSE_SCAN_USD_PER_TIB   = 5.00
export const LAKEHOUSE_STORAGE_USD_PER_GB = 0.02
export const LAKEHOUSE_FREE_SCAN_TIB      = 1        // first 1 TiB/mo free

/**
 * Estimate monthly USD cost for a lakehouse workload.
 *
 * cost = max(0, tb_scanned - FREE_SCAN_TIB) * SCAN_RATE
 *       + storage_gb * STORAGE_RATE
 *
 * Dashboard views are free — they run the DuckDB-WASM kernel in the browser
 * and never touch the server. This cost is purely for server-side / heavy
 * queries and data storage.
 *
 * @param {{ queries_per_month: number, avg_gb_scanned: number, storage_gb: number }} params
 * @returns {{ scan_usd: number, storage_usd: number, total_usd: number, tb_scanned: number }}
 */
export function estimateLakehouseCost({ queries_per_month, avg_gb_scanned, storage_gb }) {
  const tb_scanned = (queries_per_month * avg_gb_scanned) / 1024
  const billable_tb = Math.max(0, tb_scanned - LAKEHOUSE_FREE_SCAN_TIB)
  const scan_usd = billable_tb * LAKEHOUSE_SCAN_USD_PER_TIB
  const storage_usd = (storage_gb ?? 0) * LAKEHOUSE_STORAGE_USD_PER_GB
  return {
    scan_usd,
    storage_usd,
    total_usd: scan_usd + storage_usd,
    tb_scanned,
    billable_tb,
  }
}

/**
 * @deprecated Use estimateLakehouseCost() instead.
 * Kept for backward compatibility. The 4× CU multiplier model has been
 * replaced by the pay-per-TiB-scan + storage model.
 */
export const WAREHOUSE_CU_MULTIPLIER = 4

/**
 * @deprecated Use estimateLakehouseCost() instead.
 */
export function estimateWarehouseCu({ queries_per_month, avg_gb_scanned }) {
  const secondsPerQuery = Math.max(avg_gb_scanned / 1.0, 0.1)
  return Math.ceil(queries_per_month * secondsPerQuery * WAREHOUSE_CU_MULTIPLIER)
}

/**
 * Reference data: the primary comparable is BigQuery on-demand.
 * Nubi is ~20% cheaper on scan ($5/TiB vs $6.25/TiB), same storage rate,
 * and dashboard views are free (BigQuery charges for every query including
 * dashboard refreshes). Kept as a reference object; not used for head-to-head
 * price comparison tables.
 */
export const BIGQUERY_REFERENCE = {
  id: 'bigquery_ondemand',
  name: 'Google BigQuery (on-demand)',
  url: 'https://cloud.google.com/bigquery/pricing',
  scan_usd_per_tib: 6.25,
  storage_usd_per_gb: 0.02,
  free_scan_tib: 1,
  free_storage_gb: 10,
  note: '$6.25/TiB scanned (Nubi: $5/TiB) — same pay-per-scan model. First 1 TiB/mo free. Storage $0.02/GB.',
}

/**
 * @deprecated The lakehouse is no longer positioned as a competitor to
 * dedicated warehouses. Use it for BI-scale workloads; connect your own
 * BigQuery/Snowflake/ClickHouse as a Nubi datastore for multi-TB workloads
 * and Nubi pushes queries down to their engine.
 *
 * Kept for backward compatibility with any callers; the PricingCalculator
 * no longer renders a head-to-head competitor bar chart for warehouses.
 */
export const FALLBACK_COMPETITORS_WAREHOUSE = []

// ---------------------------------------------------------------------------
// Nubi tier engine for the calculator — Free / Starter / Team / Pro / Enterprise
// ---------------------------------------------------------------------------

/**
 * Wallet overage rates (ZAR) charged from the usage wallet balance
 * beyond the tier's included quota.
 */
export const WALLET_OVERAGE_RATES = {
  storage_zar_per_gb:       1.50,   // R1.50/GB-month
  compute_zar_per_1000_cu:  100,    // R100/1,000 CUs
  ai_call_zar_per_call:     5,      // R5/AI call (Haiku grounding or Sonnet chat)
  session_zar_per_10k:      50,     // R50/10,000 embedded sessions
  agent_run_zar_per_run:    2,      // R2/agent or kernel run (Pro+ E2B)
}

const NUBI_TIERS_CALC = [
  {
    id: 'free', name: 'Free', usd_monthly: 0,
    quotas: { connectors: 3, storage_gb: 1, compute_units: 500, embedded_sessions: 0, agent_runs: 0, flow_runs_per_month: 60 },
    overages: null,
  },
  {
    id: 'starter', name: 'Starter', usd_monthly: 9,
    quotas: { connectors: 5, storage_gb: 5, compute_units: 2000, embedded_sessions: 1000, agent_runs: 0, flow_runs_per_month: 180 },
    overages: {
      storage_zar_per_gb: WALLET_OVERAGE_RATES.storage_zar_per_gb,
      compute_zar_per_1000_cu: WALLET_OVERAGE_RATES.compute_zar_per_1000_cu,
      ai_call_zar_per_call: WALLET_OVERAGE_RATES.ai_call_zar_per_call,
      session_zar_per_10k: WALLET_OVERAGE_RATES.session_zar_per_10k,
      agent_run_zar_per_run: null,
    },
  },
  {
    id: 'team', name: 'Team', usd_monthly: 49,
    quotas: { connectors: 15, storage_gb: 15, compute_units: 6000, embedded_sessions: 5000, agent_runs: 10, flow_runs_per_month: 480 },
    overages: {
      storage_zar_per_gb: WALLET_OVERAGE_RATES.storage_zar_per_gb,
      compute_zar_per_1000_cu: WALLET_OVERAGE_RATES.compute_zar_per_1000_cu,
      ai_call_zar_per_call: WALLET_OVERAGE_RATES.ai_call_zar_per_call,
      session_zar_per_10k: WALLET_OVERAGE_RATES.session_zar_per_10k,
      agent_run_zar_per_run: WALLET_OVERAGE_RATES.agent_run_zar_per_run,
    },
  },
  {
    id: 'pro', name: 'Pro', usd_monthly: 149,
    quotas: { connectors: Infinity, storage_gb: 50, compute_units: 15000, embedded_sessions: 25000, agent_runs: 50, flow_runs_per_month: 1200 },
    overages: {
      storage_zar_per_gb: WALLET_OVERAGE_RATES.storage_zar_per_gb,
      compute_zar_per_1000_cu: WALLET_OVERAGE_RATES.compute_zar_per_1000_cu,
      ai_call_zar_per_call: WALLET_OVERAGE_RATES.ai_call_zar_per_call,
      session_zar_per_10k: WALLET_OVERAGE_RATES.session_zar_per_10k,
      agent_run_zar_per_run: WALLET_OVERAGE_RATES.agent_run_zar_per_run,
    },
  },
  {
    id: 'enterprise', name: 'Enterprise', usd_monthly: 1000,
    quotas: { connectors: Infinity, storage_gb: 500, compute_units: 200000, embedded_sessions: Infinity, agent_runs: 1000, flow_runs_per_month: Infinity },
    overages: {
      storage_zar_per_gb: WALLET_OVERAGE_RATES.storage_zar_per_gb,
      compute_zar_per_1000_cu: WALLET_OVERAGE_RATES.compute_zar_per_1000_cu,
      ai_call_zar_per_call: WALLET_OVERAGE_RATES.ai_call_zar_per_call,
      session_zar_per_10k: 0,
      agent_run_zar_per_run: WALLET_OVERAGE_RATES.agent_run_zar_per_run,
    },
  },
]

/**
 * Recommend a Nubi tier for the given usage and compute total ZAR cost.
 *
 * @param {{ storage_gb, compute_units, embedded_sessions, agent_runs, connectors, flow_runs_per_month }} usage
 * @param {number|null} fxRate
 * @param {{ minTierId?: string }} [opts]  minTierId floors the recommendation
 *   (e.g. 'pro' for warehouse workloads — the heavy-query pool is Pro+).
 * @returns {{ tier, base_zar, overage_zar, total_zar, overages, is_exact_fit }}
 */
export function recommendNubi(usage, fxRate, opts = {}) {
  const rate = fxRate ?? 16.26
  const minIdx = opts.minTierId
    ? Math.max(NUBI_TIERS_CALC.findIndex((t) => t.id === opts.minTierId), 0)
    : 0

  for (const tier of NUBI_TIERS_CALC.slice(minIdx)) {
    const q = tier.quotas
    const fits =
      (q.connectors === Infinity || q.connectors >= usage.connectors) &&
      (q.storage_gb === Infinity || q.storage_gb >= usage.storage_gb) &&
      (q.compute_units === Infinity || q.compute_units >= usage.compute_units) &&
      (q.embedded_sessions === Infinity || q.embedded_sessions >= usage.embedded_sessions) &&
      (q.agent_runs === Infinity || q.agent_runs >= usage.agent_runs) &&
      (q.flow_runs_per_month === Infinity || q.flow_runs_per_month >= (usage.flow_runs_per_month ?? 0))

    if (fits) {
      const base_zar = computeZar(tier.usd_monthly, rate)
      return { tier, base_zar, overage_zar: 0, total_zar: base_zar, overages: [], is_exact_fit: true }
    }
  }

  // No exact-fit tier — show overages on the highest-quota paid tier with defined
  // overage rates (iterate backward so we pick the most generous included quota,
  // giving the smallest/most realistic overage estimate for the calculator).
  for (let i = NUBI_TIERS_CALC.length - 1; i >= Math.max(minIdx, 1); i--) {
    const tier = NUBI_TIERS_CALC[i]
    if (!tier.overages) continue
    const q = tier.quotas
    const ov = tier.overages
    const overageItems = []
    let overage_zar = 0

    if (q.storage_gb !== Infinity && usage.storage_gb > q.storage_gb) {
      const gb = usage.storage_gb - q.storage_gb
      const cost = gb * ov.storage_zar_per_gb
      overage_zar += cost
      overageItems.push({ label: `${gb} GB extra storage`, zar: cost })
    }
    if (q.compute_units !== Infinity && usage.compute_units > q.compute_units) {
      const cu = usage.compute_units - q.compute_units
      const cost = (cu / 1000) * ov.compute_zar_per_1000_cu
      overage_zar += cost
      overageItems.push({ label: `${cu.toLocaleString()} extra CUs`, zar: cost })
    }
    if (q.embedded_sessions !== Infinity && usage.embedded_sessions > q.embedded_sessions) {
      const sessions = usage.embedded_sessions - q.embedded_sessions
      const cost = (sessions / 10000) * ov.session_zar_per_10k
      overage_zar += cost
      overageItems.push({ label: `${sessions.toLocaleString()} extra embed sessions`, zar: cost })
    }
    if (q.agent_runs !== Infinity && usage.agent_runs > q.agent_runs && ov.agent_run_zar_per_run) {
      const runs = usage.agent_runs - q.agent_runs
      const cost = runs * ov.agent_run_zar_per_run
      overage_zar += cost
      overageItems.push({ label: `${runs} extra agent runs`, zar: cost })
    }

    const base_zar = computeZar(tier.usd_monthly, rate)
    return {
      tier,
      base_zar,
      overage_zar: Math.ceil(overage_zar),
      total_zar: base_zar + Math.ceil(overage_zar),
      overages: overageItems,
      is_exact_fit: false,
    }
  }

  const tier = NUBI_TIERS_CALC[NUBI_TIERS_CALC.length - 1]
  const base_zar = computeZar(tier.usd_monthly, rate)
  return { tier, base_zar, overage_zar: 0, total_zar: base_zar, overages: [], is_exact_fit: true }
}

// ---------------------------------------------------------------------------
// Public API — fetch from /api/v1/pricing with graceful fallback
// ---------------------------------------------------------------------------

const _backendUrl = import.meta.env?.VITE_BACKEND_URL ?? ''
const BASE = (import.meta.env?.DEV || !_backendUrl) ? '/api/v1' : _backendUrl + '/api/v1'

/**
 * @typedef {{
 *   tiers: object[],
 *   fx: { rate: number, updated_at: string | null, fallback: boolean },
 *   competitors_bi: object[],
 *   competitors_orchestration: object[],
 *   lakehouse: { scan_usd_per_tib: number, storage_usd_per_gb: number, free_scan_tib: number },
 * }} PricingData
 */

/**
 * Fetch public pricing data.  Never throws — returns fallback data on any error.
 *
 * @returns {Promise<PricingData>}
 */
export async function fetchPricingData() {
  const lakehouseFallback = {
    scan_usd_per_tib: LAKEHOUSE_SCAN_USD_PER_TIB,
    storage_usd_per_gb: LAKEHOUSE_STORAGE_USD_PER_GB,
    free_scan_tib: LAKEHOUSE_FREE_SCAN_TIB,
  }
  try {
    const res = await fetch(`${BASE}/pricing`, { credentials: 'omit' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    return {
      tiers: Array.isArray(data?.tiers) && data.tiers.length ? data.tiers : FALLBACK_TIERS,
      fx: data?.fx ?? { rate: 16.26, updated_at: null, fallback: true },
      competitors_bi: Array.isArray(data?.competitors_bi) && data.competitors_bi.length
        ? data.competitors_bi
        : FALLBACK_COMPETITORS_BI,
      competitors_orchestration: Array.isArray(data?.competitors_orchestration) && data.competitors_orchestration.length
        ? data.competitors_orchestration
        : FALLBACK_COMPETITORS_ORCHESTRATION,
      // Lakehouse pricing constants — always from frontend (authoritative source).
      lakehouse: lakehouseFallback,
    }
  } catch {
    return {
      tiers: FALLBACK_TIERS,
      fx: { rate: 16.26, updated_at: null, fallback: true },
      competitors_bi: FALLBACK_COMPETITORS_BI,
      competitors_orchestration: FALLBACK_COMPETITORS_ORCHESTRATION,
      lakehouse: lakehouseFallback,
    }
  }
}
