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
      'Up to 5 dashboards',
      '2 scheduled flows',
      '3 built-in connectors (CSV, DuckDB, Postgres)',
      '10,000 row query cap per execution',
      '100K AI tokens / month (chat, text-to-SQL)',
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
      '1,000 embedded sessions / month',
      '5 connectors',
      '10 dashboards · 3 scheduled flows',
      '1M AI tokens / month included',
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
      '5,000 embedded sessions / month',
      '15 connectors (incl. cloud)',
      '30 dashboards · 8 scheduled flows',
      '5M AI tokens / month included · 10 agent / kernel runs',
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
      '25,000 embedded sessions / month',
      '15M AI tokens / month included · 50 agent / kernel runs',
      'Bring your own AI provider key (skip the wallet)',
      'All connectors',
      '100 dashboards · 20 scheduled flows',
      'Full RLS with JWT claims',
      'Google OAuth · SAML SSO 1 IdP (coming soon)',
      'Full white-label + custom domain (coming soon)',
      '90-day audit log',
      'Usage wallet — prepaid credits, auto-topup',
      '99.5% uptime target (best-effort; contractual SLA on Enterprise)',
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
      'Unlimited embedded sessions',
      '100M AI tokens / month included · 1,000 agent / kernel runs',
      'Bring your own AI provider key (skip the wallet)',
      'All connectors + custom connector SDK',
      'Unlimited dashboards & scheduled flows',
      'Full RLS + host-signed JWT pass-through',
      'SAML unlimited IdPs + SCIM (coming soon)',
      'Full white-label + custom JS SDK (coming soon)',
      'Unlimited audit log + SIEM export',
      'Usage wallet — prepaid credits, auto-topup, spend cap',
      'BAA / HIPAA on request',
    ],
    cta_label: 'Contact sales',
    highlight: false,
    is_enterprise: true,
    has_sla: true,
    sla: {
      uptime: '99.95%',
      response_time: 'P1 (site-down) < 30 min, 24/7 · P2 (degraded) < 2 hr',
      support: 'Dedicated Customer Success Manager',
    },
  },
]

// ---------------------------------------------------------------------------
// Static fallback: BI / Embedded Analytics competitors
// ---------------------------------------------------------------------------

export interface CompetitorModel {
  id: string
  name: string
  url: string
  note: string
  highlight_seat_penalty: boolean
  /** usage = { embedded_sessions, agent_runs, connectors }; seats = { editors, viewers } */
  model: (usage?: Record<string, any>, seats?: Record<string, any>) => number | null
  [key: string]: any
}

/**
 * Each competitor model: pricing as a pure function (usage, seats) → USD/month.
 * Data sourced from publicly available pricing pages, June 2026.
 */
export const FALLBACK_COMPETITORS_BI: CompetitorModel[] = [
  {
    id: 'metabase_pro',
    name: 'Metabase Pro',
    url: 'https://www.metabase.com/pricing',
    note: '$500/mo base (10 users incl.) + $10/extra interactive user',
    highlight_seat_penalty: true,
    // Metabase Pro Cloud is priced per interactive USER, not per embedded
    // session — so we scale on the team's interactive viewers only (no
    // sessions→viewers inflation), matching competitors.py ($500 + $10/user).
    model(_usage, { viewers }) {
      const base = 500
      return base + Math.max(0, viewers - 10) * 10
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
    note: '$150/developer/mo — viewers free',
    highlight_seat_penalty: true,
    // Per-developer seat (viewers free), matching competitors.py.
    model: (_usage, { editors }) => editors * 150,
  },
  {
    id: 'hex_team',
    name: 'Hex Team',
    url: 'https://hex.tech/pricing',
    note: '$50/editor/mo + compute hours (compute-hours add-on not modelled here)',
    highlight_seat_penalty: true,
    model(_usage, { editors }) {
      return editors * 50
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
    name: 'Luzmo',
    url: 'https://www.luzmo.com/pricing',
    note: '$149/mo (5K sessions) · $449/mo (20K sessions)',
    highlight_seat_penalty: false,
    // Session-metered (matches competitors.py): Starter ≤5K, Business ≤20K, then custom.
    model({ embedded_sessions }) {
      if (embedded_sessions <= 5000) return 149
      if (embedded_sessions <= 20000) return 449
      return null // custom / enterprise beyond 20K sessions
    },
  },
  {
    id: 'preset_professional',
    name: 'Preset Professional',
    url: 'https://preset.io/pricing',
    note: '$20/user/mo + $500/mo embed add-on',
    highlight_seat_penalty: true,
    // Per-user ($20) + flat embed add-on when embedding is used. No
    // sessions→viewers inflation — scales on the actual team size only.
    model({ embedded_sessions }, { editors, viewers }) {
      const seatCost = (editors + viewers) * 20
      const embedAddon = embedded_sessions > 0 ? 500 : 0
      return seatCost + embedAddon
    },
  },
]

// ---------------------------------------------------------------------------
// Static fallback: Data Orchestration competitors (June 2026)
// ---------------------------------------------------------------------------

export interface OrchestrationCompetitorModel {
  id: string
  name: string
  url: string
  note: string
  model_type: 'per-run' | 'per-seat' | 'flat' | 'infra' | 'per-action'
  model: (orchestration?: Record<string, any>) => number
  [key: string]: any
}

/**
 * Orchestration competitors.  The usage object here uses different keys:
 * { flow_runs_per_month, workers, seats }
 * to match orchestration pricing units (runs, workers, seats/users).
 *
 * Data sources: research artifact (orchestration-pricing-research).
 */
export const FALLBACK_COMPETITORS_ORCHESTRATION: OrchestrationCompetitorModel[] = [
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
// Nubi tier engine for the calculator — Free / Starter / Team / Pro / Enterprise
// ---------------------------------------------------------------------------

/**
 * Wallet overage rates (ZAR) charged from the usage wallet balance
 * beyond the tier's included quota.
 *
 * What draws from the wallet:
 *   - AI tokens (real-time provider token pass-through, cost + markup) →
 *     ai_token_markup_pct / ai_token_reference_usd_per_1m
 *   - Embedded sessions (CDN egress + edge compute) → session_zar_per_10k
 *   - Agent / kernel runs (on-demand remote-kernel escape hatch) → agent_run_zar_per_run
 *
 * AI billing model (matches backend app.ee.billing.token_billing):
 *   Each tier includes a free monthly LLM TOKEN allowance (prompt + completion
 *   tokens summed across every metered call — see AI_TOKEN_ALLOWANCE below).
 *   Tokens beyond that allowance are billed in REAL TIME at the provider's
 *   actual USD cost for that call × (1 + NUBI_TOKEN_MARKUP_PCT / 100) — a pure
 *   cost pass-through, not a flat per-call rate. There is therefore no single
 *   fixed ZAR/token rate: the real charge depends on which model you use
 *   (Haiku-class models cost a fraction of Opus-class). This calculator uses
 *   `ai_token_reference_usd_per_1m` (a Haiku-class blended reference — see
 *   backend/app/ee/billing/tiers.py's COGS notes) purely to ballpark an
 *   overage estimate; your actual bill is metered per call, per model.
 *   Bringing your own provider key (Settings → AI providers) makes calls to
 *   that vendor bypass the wallet entirely — you pay the vendor directly.
 *
 * What is ALWAYS FREE (zero wallet draw, zero server COGS):
 *   - Nubi has no hosted warehouse: every dashboard view runs DuckDB-WASM in
 *     the browser — no server scan, no server compute, no server storage.
 *   - Viewer seats at any tier: viewing a pre-computed dashboard is free.
 */
export const WALLET_OVERAGE_RATES = {
  session_zar_per_10k:            50,    // R50/10,000 embedded sessions (CDN egress; public exports)
  agent_run_zar_per_run:          2,     // R2/agent or kernel run (Team+ E2B)
  ai_token_markup_pct:            7.5,   // NUBI_TOKEN_MARKUP_PCT — matches backend/app/config.py
  ai_token_reference_usd_per_1m:  0.25,  // Haiku-class blended reference (illustrative only)
}

/**
 * Free monthly AI-token allowance per tier — mirrors
 * backend/app/ee/billing/tiers.py `TierLimits.max_ai_tokens_per_month`.
 * This is the ACTIVE AI billing meter (prompt + completion tokens summed
 * across every metered call); the legacy per-call `ai_calls` quota is retired.
 */
export const AI_TOKEN_ALLOWANCE = {
  free: 100_000,
  starter: 1_000_000,
  team: 5_000_000,
  pro: 15_000_000,
  enterprise: 100_000_000,
}

/**
 * Format a token count compactly, e.g. 1_000_000 → "1M", 100_000 → "100K".
 * @param {number} n
 * @returns {string}
 */
export function formatTokens(n) {
  const v = Number(n) || 0
  if (v >= 1_000_000) return (v / 1_000_000).toLocaleString('en-US', { maximumFractionDigits: 1 }) + 'M'
  if (v >= 1_000) return (v / 1_000).toLocaleString('en-US', { maximumFractionDigits: 0 }) + 'K'
  return v.toLocaleString('en-US')
}

const NUBI_TIERS_CALC = [
  {
    id: 'free', name: 'Free', usd_monthly: 0,
    quotas: { connectors: 3, embedded_sessions: 0, agent_runs: 0, ai_tokens: AI_TOKEN_ALLOWANCE.free },
    overages: null,
  },
  {
    id: 'starter', name: 'Starter', usd_monthly: 9,
    quotas: { connectors: 5, embedded_sessions: 1000, agent_runs: 0, ai_tokens: AI_TOKEN_ALLOWANCE.starter },
    overages: {
      session_zar_per_10k: WALLET_OVERAGE_RATES.session_zar_per_10k,
      agent_run_zar_per_run: null,
      ai_token_overage: true,
    },
  },
  {
    id: 'team', name: 'Team', usd_monthly: 49,
    quotas: { connectors: 15, embedded_sessions: 5000, agent_runs: 10, ai_tokens: AI_TOKEN_ALLOWANCE.team },
    overages: {
      session_zar_per_10k: WALLET_OVERAGE_RATES.session_zar_per_10k,
      agent_run_zar_per_run: WALLET_OVERAGE_RATES.agent_run_zar_per_run,
      ai_token_overage: true,
    },
  },
  {
    id: 'pro', name: 'Pro', usd_monthly: 149,
    quotas: { connectors: Infinity, embedded_sessions: 25000, agent_runs: 50, ai_tokens: AI_TOKEN_ALLOWANCE.pro },
    overages: {
      session_zar_per_10k: WALLET_OVERAGE_RATES.session_zar_per_10k,
      agent_run_zar_per_run: WALLET_OVERAGE_RATES.agent_run_zar_per_run,
      ai_token_overage: true,
    },
  },
  {
    id: 'enterprise', name: 'Enterprise', usd_monthly: 1000,
    quotas: { connectors: Infinity, embedded_sessions: Infinity, agent_runs: 1000, ai_tokens: AI_TOKEN_ALLOWANCE.enterprise },
    overages: {
      session_zar_per_10k: 0,
      agent_run_zar_per_run: WALLET_OVERAGE_RATES.agent_run_zar_per_run,
      ai_token_overage: true,
    },
  },
]

/**
 * Estimate the ZAR overage cost for `tokensOver` tokens beyond a tier's free
 * allowance, using the Haiku-class reference rate + markup (illustrative —
 * see WALLET_OVERAGE_RATES docstring). Mirrors the SHAPE of the backend's
 * `compute_token_charge` (cost × (1 + markup/100) × fx_rate) but with a fixed
 * reference USD/1M-token cost rather than a real per-call provider cost.
 *
 * @param {number} tokensOver
 * @param {number} rate  USD→ZAR rate
 * @returns {number}
 */
function estimateAiTokenOverageZar(tokensOver, rate) {
  if (tokensOver <= 0) return 0
  const usdCost = (tokensOver / 1_000_000) * WALLET_OVERAGE_RATES.ai_token_reference_usd_per_1m
  const usdMarkedUp = usdCost * (1 + WALLET_OVERAGE_RATES.ai_token_markup_pct / 100)
  return usdMarkedUp * rate
}

/**
 * Recommend a Nubi tier for the given usage and compute total ZAR cost.
 *
 * @param opts  minTierId floors the recommendation
 */
export function recommendNubi(
  usage: Record<string, any>,
  fxRate: number | null,
  opts: { minTierId?: string } = {},
) {
  const rate = fxRate ?? 16.26
  const minIdx = opts.minTierId
    ? Math.max(NUBI_TIERS_CALC.findIndex((t) => t.id === opts.minTierId), 0)
    : 0

  for (const tier of NUBI_TIERS_CALC.slice(minIdx)) {
    const q = tier.quotas
    // NOTE: flow/pipeline RUNS are NOT a separate billing meter — Nubi has no
    // hosted warehouse or compute-unit dimension, so tier fit is never gated
    // on flow_runs_per_month.
    const fits =
      (q.connectors === Infinity || q.connectors >= usage.connectors) &&
      (q.embedded_sessions === Infinity || q.embedded_sessions >= usage.embedded_sessions) &&
      (q.agent_runs === Infinity || q.agent_runs >= usage.agent_runs) &&
      (q.ai_tokens === Infinity || q.ai_tokens >= (usage.ai_tokens ?? 0))

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
    if (q.ai_tokens !== Infinity && (usage.ai_tokens ?? 0) > q.ai_tokens && ov.ai_token_overage) {
      const tokensOver = usage.ai_tokens - q.ai_tokens
      const cost = estimateAiTokenOverageZar(tokensOver, rate)
      overage_zar += cost
      overageItems.push({ label: `${formatTokens(tokensOver)} extra AI tokens (est.)`, zar: cost })
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
 * }} PricingData
 */

/**
 * Fetch public pricing data.  Never throws — returns fallback data on any error.
 *
 * @returns {Promise<PricingData>}
 */
export async function fetchPricingData() {
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
    }
  } catch {
    return {
      tiers: FALLBACK_TIERS,
      fx: { rate: 16.26, updated_at: null, fallback: true },
      competitors_bi: FALLBACK_COMPETITORS_BI,
      competitors_orchestration: FALLBACK_COMPETITORS_ORCHESTRATION,
    }
  }
}
