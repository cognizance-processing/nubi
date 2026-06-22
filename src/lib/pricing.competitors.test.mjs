/**
 * pricing.competitors.test.mjs — guards the pricing-calculator fairness fixes:
 *   - storage overage rate matches the backend (R0.33/GB, not the old R1.50)
 *   - flow/pipeline runs are NOT a tier-gating meter (cost flows through compute)
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
  WALLET_OVERAGE_RATES,
  FALLBACK_COMPETITORS_BI,
  FALLBACK_COMPETITORS_ORCHESTRATION,
  FALLBACK_TIERS,
} from './pricing.js'

const BI_USAGE = { storage_gb: 25, compute_units: 10000, embedded_sessions: 10000, agent_runs: 20, connectors: 5 }
const SEATS = { editors: 8, viewers: 500 }
const ZERO_USAGE = { storage_gb: 0, compute_units: 0, embedded_sessions: 0, agent_runs: 0, connectors: 1 }
const ZERO_SEATS = { editors: 1, viewers: 0 }
const ORCH_USAGE = {
  flow_runs_per_month: 5000, serverless_minutes: 5000, workers: 2, deployments: 1,
  seats: 5, hours_per_month: 730, block_runs: 10000, compute_hours: 10,
  assets_per_run: 2, actions_per_month: 500000, dcu_per_hour: 12,
}

// ---------------------------------------------------------------------------
// Storage overage rate — must match backend tiers.py (R0.33/GB)
// ---------------------------------------------------------------------------

test('storage overage rate is R0.33/GB (R2 parity), not the stale R1.50', () => {
  assert.equal(WALLET_OVERAGE_RATES.storage_zar_per_gb, 0.33)
})

test('recommendNubi prices storage overage at R0.33/GB', () => {
  // 600 GB used — beyond Enterprise's 500 GB → 100 GB billable at R0.33.
  const rec = recommendNubi(
    { storage_gb: 600, compute_units: 1000, embedded_sessions: 0, agent_runs: 0, connectors: 1 },
    16.26,
  )
  const storageItem = rec.overages.find((o) => /storage/i.test(o.label))
  assert.ok(storageItem, 'expected a storage overage line')
  assert.equal(storageItem.zar, 100 * 0.33) // 33, not 150 (the old R1.50 rate)
})

// ---------------------------------------------------------------------------
// Flow runs are NOT a billing meter — must never bump the tier
// ---------------------------------------------------------------------------

test('recommendNubi does not bump the tier on flow_runs_per_month', () => {
  const rec = recommendNubi(
    { storage_gb: 1, compute_units: 10, embedded_sessions: 0, agent_runs: 0, connectors: 1, flow_runs_per_month: 100000 },
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
  // 8 editors, no compute → 8 × 50 = 400.
  assert.equal(bi('hex_team').model({ compute_units: 0 }, { editors: 8 }), 400)
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
