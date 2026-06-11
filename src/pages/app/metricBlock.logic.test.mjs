/**
 * Tests for metricBlock.logic.js — the pure logic behind the query editor's
 * "Expose as metric" panel. Run with the repo's test:dash convention.
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  deriveSlug,
  isValidSlug,
  blankMetricDraft,
  metricToDraft,
  draftToMetricBlock,
  validateMetricDraft,
} from './metricBlock.logic.js'

// ── deriveSlug ──────────────────────────────────────────────────────────────

test('deriveSlug lowercases and collapses non-alphanumerics', () => {
  assert.equal(deriveSlug('Total Revenue (ZAR)'), 'total_revenue_zar')
  assert.equal(deriveSlug('  Gross   Margin %  '), 'gross_margin')
  assert.equal(deriveSlug('revenue'), 'revenue')
  assert.equal(deriveSlug('Active-Users/Day'), 'active_users_day')
})

test('deriveSlug returns empty for garbage / empty input', () => {
  assert.equal(deriveSlug(''), '')
  assert.equal(deriveSlug('***'), '')
  assert.equal(deriveSlug(null), '')
  assert.equal(deriveSlug(undefined), '')
})

// ── isValidSlug ─────────────────────────────────────────────────────────────

test('isValidSlug accepts good slugs, rejects bad ones', () => {
  assert.equal(isValidSlug('revenue'), true)
  assert.equal(isValidSlug('total_revenue_zar'), true)
  assert.equal(isValidSlug('r2d2'), true)
  assert.equal(isValidSlug(''), false)
  assert.equal(isValidSlug('2fast'), false) // can't start with a digit
  assert.equal(isValidSlug('Has Space'), false)
  assert.equal(isValidSlug('UPPER'), false)
  assert.equal(isValidSlug('dash-ed'), false)
})

// ── blankMetricDraft ────────────────────────────────────────────────────────

test('blankMetricDraft is disabled and produces no block', () => {
  const draft = blankMetricDraft()
  assert.equal(draft.enabled, false)
  assert.equal(draftToMetricBlock(draft), null)
  assert.deepEqual(validateMetricDraft(draft), {})
})

// ── metricToDraft (parse) ───────────────────────────────────────────────────

test('metricToDraft(absent) → blank+disabled, slug seeded from query name', () => {
  const draft = metricToDraft(null, 'My Query')
  assert.equal(draft.enabled, false)
  assert.equal(draft.slug, 'my_query')
  assert.equal(draft.slugEdited, false)
})

test('metricToDraft(block) round-trips a full block', () => {
  const block = {
    slug: 'revenue',
    measure: { name: 'revenue', agg: 'sum', expr: 'amount', type: 'additive', format: 'currency' },
    dimensions: [
      { name: 'region', expr: null, type: 'text' },
      { name: 'product', expr: 'upper(product)', type: 'text' },
    ],
    time_dimension: { column: 'order_date', grains: ['day', 'week', 'month'], default_grain: 'day' },
    default_filters: [],
    rls_keys: ['tenant_id'],
    owner: null,
    description: 'Total revenue',
  }
  const draft = metricToDraft(block, 'Orders')
  assert.equal(draft.enabled, true)
  assert.equal(draft.slug, 'revenue')
  assert.equal(draft.slugEdited, true)
  assert.equal(draft.measure.format, 'currency')
  assert.equal(draft.dimensions.length, 2)
  assert.equal(draft.dimensions[1].expr, 'upper(product)')
  assert.equal(draft.hasTime, true)
  assert.deepEqual(draft.time.grains, ['day', 'week', 'month'])
  assert.deepEqual(draft.rls_keys, ['tenant_id'])
})

// ── draftToMetricBlock (build) ──────────────────────────────────────────────

test('draftToMetricBlock builds a clean block, dropping blank dims', () => {
  const draft = {
    enabled: true,
    slug: 'revenue',
    slugEdited: true,
    description: '  Total revenue  ',
    measure: { name: 'revenue', agg: 'sum', expr: ' amount ', type: 'additive', format: 'currency' },
    dimensions: [
      { name: 'region', expr: '', type: 'text' },
      { name: '', expr: 'ignored', type: 'text' }, // dropped — no name
    ],
    hasTime: true,
    time: { column: 'order_date', grains: ['day', 'month'], default_grain: 'day' },
    rls_keys: ['tenant_id', ' '],
    default_filters: [],
  }
  const block = draftToMetricBlock(draft)
  assert.equal(block.slug, 'revenue')
  assert.equal(block.measure.expr, 'amount')
  assert.equal(block.description, 'Total revenue')
  assert.equal(block.dimensions.length, 1)
  assert.equal(block.dimensions[0].name, 'region')
  assert.equal(block.dimensions[0].expr, null) // blank expr → null
  assert.deepEqual(block.time_dimension.grains, ['day', 'month'])
  assert.deepEqual(block.rls_keys, ['tenant_id']) // blank rls key dropped
  assert.equal(block.owner, null)
})

test('draftToMetricBlock disabled → null', () => {
  assert.equal(draftToMetricBlock({ enabled: false }), null)
  assert.equal(draftToMetricBlock(null), null)
})

test('draftToMetricBlock: no time column → time_dimension null', () => {
  const block = draftToMetricBlock({
    enabled: true,
    slug: 'orders',
    slugEdited: true,
    measure: { name: 'orders', agg: 'count', expr: '*', type: 'additive', format: 'number' },
    dimensions: [],
    hasTime: true,
    time: { column: '', grains: [], default_grain: 'day' },
    rls_keys: [],
  })
  assert.equal(block.time_dimension, null)
})

test('draftToMetricBlock derives slug from measure name when not edited', () => {
  const block = draftToMetricBlock({
    enabled: true,
    slug: '',
    slugEdited: false,
    measure: { name: 'Net Revenue', agg: 'sum', expr: 'amount', type: 'additive', format: 'currency' },
    dimensions: [],
    hasTime: false,
    time: { column: '', grains: [], default_grain: 'day' },
    rls_keys: [],
  })
  assert.equal(block.slug, 'net_revenue')
})

// ── round-trip ──────────────────────────────────────────────────────────────

test('round-trip: block → draft → block preserves the essentials', () => {
  const original = {
    slug: 'revenue',
    measure: { name: 'revenue', agg: 'sum', expr: 'amount', type: 'additive', format: 'currency' },
    dimensions: [{ name: 'region', expr: null, type: 'text' }],
    time_dimension: { column: 'order_date', grains: ['day', 'week', 'month'], default_grain: 'day' },
    default_filters: [],
    rls_keys: ['tenant_id'],
    owner: null,
    description: 'Total revenue',
  }
  const rebuilt = draftToMetricBlock(metricToDraft(original, 'Orders'))
  assert.equal(rebuilt.slug, original.slug)
  assert.deepEqual(rebuilt.measure, original.measure)
  assert.deepEqual(rebuilt.dimensions, original.dimensions)
  assert.deepEqual(rebuilt.time_dimension, original.time_dimension)
  assert.deepEqual(rebuilt.rls_keys, original.rls_keys)
  assert.equal(rebuilt.description, original.description)
})

// ── validation ──────────────────────────────────────────────────────────────

test('validateMetricDraft flags missing measure name + slug', () => {
  const errors = validateMetricDraft({
    enabled: true,
    slug: '',
    slugEdited: true,
    measure: { name: '', agg: 'sum', expr: 'amount' },
    dimensions: [],
    time: {},
    rls_keys: [],
  })
  assert.ok(errors.measureName)
  assert.ok(errors.slug)
})

test('validateMetricDraft flags a bad slug format', () => {
  const errors = validateMetricDraft({
    enabled: true,
    slug: 'Bad Slug',
    slugEdited: true,
    measure: { name: 'rev', agg: 'sum', expr: 'amount' },
    dimensions: [],
    time: {},
    rls_keys: [],
  })
  assert.ok(errors.slug)
  assert.ok(!errors.measureName)
})

test('validateMetricDraft requires expr for non-count aggs, not for count', () => {
  const withSum = validateMetricDraft({
    enabled: true, slug: 'rev', slugEdited: true,
    measure: { name: 'rev', agg: 'sum', expr: '' },
    dimensions: [], time: {}, rls_keys: [],
  })
  assert.ok(withSum.expr)

  const withCount = validateMetricDraft({
    enabled: true, slug: 'orders', slugEdited: true,
    measure: { name: 'orders', agg: 'count', expr: '' },
    dimensions: [], time: {}, rls_keys: [],
  })
  assert.ok(!withCount.expr)
})

test('validateMetricDraft passes for a complete valid draft', () => {
  const errors = validateMetricDraft({
    enabled: true,
    slug: 'revenue',
    slugEdited: true,
    measure: { name: 'revenue', agg: 'sum', expr: 'amount', type: 'additive', format: 'currency' },
    dimensions: [{ name: 'region', expr: '', type: 'text' }],
    hasTime: true,
    time: { column: 'order_date', grains: ['day'], default_grain: 'day' },
    rls_keys: ['tenant_id'],
  })
  assert.deepEqual(errors, {})
})
