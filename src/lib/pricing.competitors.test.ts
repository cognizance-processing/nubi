/**
 * pricing.competitors.test.mjs — guards the pricing-calculator fairness fixes:
 *   - Nubi has no hosted warehouse: no storage / compute-unit billing meter
 *   - flow/pipeline runs are NOT a tier-gating meter (Nubi has no warehouse
 *     compute dimension for their cost to flow through)
 *   - AI billing is TOKEN-based (real-time provider cost + markup, with a
 *     free monthly token allowance per tier) — not the old flat per-call rate
 *   - competitor models are finite, non-negative, and free of the old
 *     embedded-sessions→viewers inflation
 *   - the reconciled per-competitor numbers (Hex $50, Lightdash $150/dev,
 *     Metabase $500 base, Luzmo session tiers) hold
 *   - Enterprise SLA copy matches the contractual 99.95% in the backend
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  recommendNubi,
  computeZar,
  formatTokens,
  WALLET_OVERAGE_RATES,
  AI_TOKEN_ALLOWANCE,
  FALLBACK_COMPETITORS_BI,
  FALLBACK_COMPETITORS_ORCHESTRATION,
  FALLBACK_TIERS,
} from './pricing.js'

const BI_USAGE = { embedded_sessions: 10000, agent_runs: 20, connectors: 5 }
const SEATS = { editors: 8, viewers: 500 }
const ZERO_USAGE = { embedded_sessions: 0, agent_runs: 0, connectors: 1 }
const ZERO_SEATS = { editors: 1, viewers: 0 }
const ORCH_USAGE = {
  flow_runs_per_month: 5000, serverless_minutes: 5000, workers: 2, deployments: 1,
  seats: 5, hours_per_month: 730, block_runs: 10000, compute_hours: 10,
  assets_per_run: 2, actions_per_month: 500000, dcu_per_hour: 12,
}

// ---------------------------------------------------------------------------
// No storage / compute-unit dimension — Nubi has no hosted warehouse
// ---------------------------------------------------------------------------

test('wallet overage rates have no storage, compute-unit, or flat per-call AI dimension', () => {
  // Cast loosely: the assertions below are a regression guard that these keys
  // were deliberately REMOVED from the real (now-narrower) type.
  const rates = WALLET_OVERAGE_RATES as Record<string, any>
  assert.equal(rates.storage_zar_per_gb, undefined)
  assert.equal(rates.compute_zar_per_1000_cu, undefined)
  // The old flat per-call rate is retired — AI is now real-time token pass-through.
  assert.equal(rates.ai_call_zar_per_call, undefined)
  assert.equal(rates.ai_token_markup_pct, 7.5, 'must mirror backend NUBI_TOKEN_MARKUP_PCT')
  assert.ok(rates.ai_token_reference_usd_per_1m > 0)
})

test('AI_TOKEN_ALLOWANCE mirrors backend tiers.py max_ai_tokens_per_month', () => {
  assert.equal(AI_TOKEN_ALLOWANCE.free, 100_000)
  assert.equal(AI_TOKEN_ALLOWANCE.starter, 1_000_000)
  assert.equal(AI_TOKEN_ALLOWANCE.team, 5_000_000)
  assert.equal(AI_TOKEN_ALLOWANCE.pro, 15_000_000)
  assert.equal(AI_TOKEN_ALLOWANCE.enterprise, 100_000_000)
})

test('formatTokens renders compact K/M token counts', () => {
  assert.equal(formatTokens(100_000), '100K')
  assert.equal(formatTokens(1_000_000), '1M')
  assert.equal(formatTokens(2_500_000), '2.5M')
  assert.equal(formatTokens(0), '0')
})

test('recommendNubi prices agent-run overage at R2/run (no fitting tier)', () => {
  // Connectors and embedded sessions are unlimited on Enterprise, so the only
  // dimension that can force the "no exact fit" overage path is agent_runs
  // (bounded at 1,000/mo even on Enterprise). 1,200 runs → 200 over × R2.
  const rec = recommendNubi(
    { embedded_sessions: 0, agent_runs: 1200, connectors: 1 },
    16.26,
  )
  const agentItem = rec.overages.find((o) => /agent/i.test(o.label))
  assert.ok(agentItem, 'expected an agent-run overage line')
  assert.equal(agentItem.zar, 200 * 2)
})

test('recommendNubi prices AI-token overage at reference cost + markup (no fitting tier)', () => {
  // AI tokens are bounded even on Enterprise (100,000,000/mo), so a token
  // count above that forces the "no exact fit" overage path.
  const tokensOver = 2_000_000
  const rec = recommendNubi(
    { embedded_sessions: 0, agent_runs: 0, ai_tokens: AI_TOKEN_ALLOWANCE.enterprise + tokensOver, connectors: 1 },
    16.26,
  )
  const aiItem = rec.overages.find((o) => /ai token/i.test(o.label))
  assert.ok(aiItem, 'expected an AI-token overage line')
  const expectedUsd = (tokensOver / 1_000_000) * WALLET_OVERAGE_RATES.ai_token_reference_usd_per_1m
  const expectedMarkedUp = expectedUsd * (1 + WALLET_OVERAGE_RATES.ai_token_markup_pct / 100)
  const expectedZar = expectedMarkedUp * 16.26
  assert.ok(Math.abs(aiItem.zar - expectedZar) < 1e-9, `${aiItem.zar} !== ${expectedZar}`)
})

test('recommendNubi treats missing ai_tokens as 0 (back-compat with callers that omit it)', () => {
  const rec = recommendNubi({ embedded_sessions: 0, agent_runs: 0, connectors: 1 }, null)
  assert.equal(rec.tier.id, 'free', 'omitting ai_tokens must not force a paid tier')
})

// ---------------------------------------------------------------------------
// Flow runs are NOT a billing meter — must never bump the tier
// ---------------------------------------------------------------------------

test('recommendNubi does not bump the tier on flow_runs_per_month', () => {
  const rec = recommendNubi(
    { embedded_sessions: 0, agent_runs: 0, connectors: 1, flow_runs_per_month: 100000 },
    null,
  )
  assert.equal(rec.tier.id, 'free', 'huge flow-run count must not force a paid tier')
})

// ---------------------------------------------------------------------------
// Competitor models — finite, non-negative, no NaN (incl. zero usage)
// ---------------------------------------------------------------------------

test('every BI competitor model returns a finite, non-negative number or null', () => {
  for (const comp of FALLBACK_COMPETITORS_BI) {
    for (const [usage, seats] of [[BI_USAGE, SEATS], [ZERO_USAGE, ZERO_SEATS]]) {
      const usd = comp.model(usage, seats)
      assert.ok(usd === null || (Number.isFinite(usd) && usd >= 0), `${comp.id} → ${usd}`)
    }
  }
})

test('every orchestration competitor model returns a finite, positive number', () => {
  for (const comp of FALLBACK_COMPETITORS_ORCHESTRATION) {
    const usd = comp.model({ ...ORCH_USAGE })
    assert.ok(Number.isFinite(usd) && usd > 0, `${comp.id} → ${usd}`)
  }
})

// ---------------------------------------------------------------------------
// Reconciled competitor numbers (single source of truth w/ competitors.py)
// ---------------------------------------------------------------------------

function bi(id) {
  return FALLBACK_COMPETITORS_BI.find((c) => c.id === id)
}

test('Hex Team is $50/editor (not the stale $75)', () => {
  // 8 editors → 8 × 50 = 400 (Hex's compute-hours add-on is not modelled here).
  assert.equal(bi('hex_team').model({}, { editors: 8 }), 400)
})

test('Lightdash Cloud Pro is per-developer $150 (not $3,000 flat)', () => {
  assert.equal(bi('lightdash_pro').model({}, { editors: 8 }), 1200)
})

test('Metabase Pro scales on viewers only — no sessions→viewers inflation', () => {
  // 10k sessions must NOT inflate the bill; only the 500 viewers count.
  const withSessions = bi('metabase_pro').model({ embedded_sessions: 10000 }, { viewers: 500 })
  const withoutSessions = bi('metabase_pro').model({ embedded_sessions: 0 }, { viewers: 500 })
  assert.equal(withSessions, withoutSessions, 'sessions must not affect Metabase cost')
  assert.equal(withSessions, 500 + (500 - 10) * 10) // $500 base + $10 × 490 = $5,400
})

test('Luzmo is session-tiered ($149 / $449 / custom)', () => {
  assert.equal(bi('luzmo_starter').model({ embedded_sessions: 3000 }), 149)
  assert.equal(bi('luzmo_starter').model({ embedded_sessions: 10000 }), 449)
  assert.equal(bi('luzmo_starter').model({ embedded_sessions: 50000 }), null) // custom
})

test('Holistics Standard is $1,000/mo flat — not per-seat', () => {
  // Standard tier: $1,000/mo regardless of viewer count.
  assert.equal(bi('holistics_standard').model({ embedded_sessions: 5000 }, { viewers: 500 }), 1000)
  assert.equal(bi('holistics_standard').model({ embedded_sessions: 0 }, { viewers: 0 }), 1000)
})

test('Holistics SCS is $2,000/mo flat', () => {
  assert.equal(bi('holistics_scs').model({}), 2000)
})

test('Embeddable Lite is $499/mo base with $200/500-session overage', () => {
  // Under 1,000 sessions → base only.
  assert.equal(bi('embeddable_lite').model({ embedded_sessions: 500 }), 499)
  // 1,500 sessions → 500 overage = 1 block → $499 + $200 = $699.
  assert.equal(bi('embeddable_lite').model({ embedded_sessions: 1500 }), 699)
  // 2,000 sessions → 1,000 overage = 2 blocks → $499 + $400 = $899.
  assert.equal(bi('embeddable_lite').model({ embedded_sessions: 2000 }), 899)
})

// ---------------------------------------------------------------------------
// Coming-soon gating: SAML/SCIM/white-label/custom-domain must be marked
// ---------------------------------------------------------------------------

test('Pro FALLBACK_TIER features flag SAML and white-label as coming soon', () => {
  const pro = FALLBACK_TIERS.find((t) => t.id === 'pro')
  const samlFeature = pro.features.find((f) => /saml/i.test(f))
  assert.ok(samlFeature, 'pro tier must list SAML feature')
  assert.match(samlFeature.toLowerCase(), /coming soon/, 'pro SAML must be marked coming soon')
  const wlFeature = pro.features.find((f) => /white.label/i.test(f))
  assert.ok(wlFeature, 'pro tier must list white-label feature')
  assert.match(wlFeature.toLowerCase(), /coming soon/, 'pro white-label must be marked coming soon')
})

test('Enterprise FALLBACK_TIER features flag SAML+SCIM as coming soon', () => {
  const ent = FALLBACK_TIERS.find((t) => t.id === 'enterprise')
  const samlFeature = ent.features.find((f) => /saml/i.test(f))
  assert.ok(samlFeature, 'enterprise tier must list SAML feature')
  assert.match(samlFeature.toLowerCase(), /coming soon/, 'enterprise SAML/SCIM must be marked coming soon')
})

// ---------------------------------------------------------------------------
// FX rounding + SLA copy
// ---------------------------------------------------------------------------

test('computeZar matches backend ceil-to-R10 with 2% buffer', () => {
  assert.equal(computeZar(0, 16.26), 0)
  assert.equal(computeZar(9, 16.26), 150) // Starter
  assert.equal(computeZar(149, 16.26), 2480) // Pro
})

test('Enterprise SLA copy matches the contractual 99.95% in tiers.py', () => {
  const ent = FALLBACK_TIERS.find((t) => t.id === 'enterprise')
  assert.equal(ent.sla.uptime, '99.95%')
  assert.match(ent.sla.response_time, /30 min/)
})
