/**
 * Tests for queryParams.logic.js — SQL placeholder extraction, the
 * auto-param reconciliation rule, and the "Add filter parameter" snippet
 * builder. Run with the repo's test:dash convention.
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  extractPlaceholders,
  reconcileAutoParams,
  buildFilterParamSnippet,
  defaultForFilterParamType,
  validateNewParamName,
} from './queryParams.logic.js'

// ── extractPlaceholders ─────────────────────────────────────────────────────

test('extractPlaceholders finds a bare {{name}}', () => {
  assert.deepEqual(extractPlaceholders('SELECT * FROM t WHERE x = {{val}}'), ['val'])
})

test('extractPlaceholders finds a filtered output {{name | inclause}}', () => {
  assert.deepEqual(
    extractPlaceholders('WHERE region IN {{ region | inclause }}'),
    ['region'],
  )
})

test('extractPlaceholders finds dotted attribute access {{name.from}}', () => {
  const sql = 'WHERE d >= {{ myrange.from }} AND d < {{ myrange.to }}'
  assert.deepEqual(extractPlaceholders(sql), ['myrange'])
})

test('extractPlaceholders finds a bare {% if name %} guard with no output token', () => {
  assert.deepEqual(
    extractPlaceholders("{% if branch %}\n  'Filtered'\n{% else %}\n  'All'\n{% endif %}"),
    ['branch'],
  )
})

test('extractPlaceholders finds {% elif name %}', () => {
  assert.deepEqual(
    extractPlaceholders('{% if a %}x{% elif b %}y{% endif %}'),
    ['a', 'b'],
  )
})

test('extractPlaceholders dedupes repeated references and preserves first-seen order', () => {
  const sql = '{% if region %} AND r = {{ region }} {% endif %} AND r2 = {{ region }}'
  assert.deepEqual(extractPlaceholders(sql), ['region'])
})

test('extractPlaceholders returns [] for SQL with no placeholders', () => {
  assert.deepEqual(extractPlaceholders('SELECT * FROM demo'), [])
})

test('extractPlaceholders handles null/empty input without throwing', () => {
  assert.deepEqual(extractPlaceholders(''), [])
  assert.deepEqual(extractPlaceholders(null), [])
  assert.deepEqual(extractPlaceholders(undefined), [])
})

test('extractPlaceholders matches the real board idiom (multi-dim guarded filter)', () => {
  const sql = `
    WHERE 1=1
    {% if country_filter %} AND country_desc IN {{ country_filter | inclause }} {% endif %}
    {% if region %} AND region_desc IN {{ region | inclause }} {% endif %}
  `
  assert.deepEqual(extractPlaceholders(sql), ['country_filter', 'region'])
})

// ── reconcileAutoParams ──────────────────────────────────────────────────────

test('reconcileAutoParams adds a newly-found name as auto:true', () => {
  const result = reconcileAutoParams([], 'WHERE x = {{val}}')
  assert.deepEqual(result, [
    { name: 'val', type: 'text', default: null, required: false, auto: true },
  ])
})

test('reconcileAutoParams drops an auto param whose placeholder was deleted', () => {
  const prev = [{ name: 'val', type: 'text', default: null, required: false, auto: true }]
  const result = reconcileAutoParams(prev, 'SELECT * FROM demo')
  assert.deepEqual(result, [])
})

test('reconcileAutoParams KEEPS a manually-added param even when its placeholder is deleted', () => {
  const prev = [{ name: 'region', type: 'multiselect', default: [], required: false, auto: false }]
  const result = reconcileAutoParams(prev, 'SELECT * FROM demo')
  assert.deepEqual(result, prev)
})

test('reconcileAutoParams KEEPS a param with no auto flag at all (loaded from a saved query)', () => {
  const prev = [{ name: 'legacy', type: 'text', default: 'x', required: false }]
  const result = reconcileAutoParams(prev, 'SELECT * FROM demo')
  assert.deepEqual(result, prev)
})

test('reconcileAutoParams keeps an auto param whose placeholder is still present', () => {
  const prev = [{ name: 'val', type: 'text', default: null, required: false, auto: true }]
  const result = reconcileAutoParams(prev, 'WHERE x = {{val}}')
  assert.deepEqual(result, prev)
})

test('reconcileAutoParams returns the SAME array reference when nothing changed', () => {
  const prev = [{ name: 'val', type: 'text', default: null, required: false, auto: true }]
  const result = reconcileAutoParams(prev, 'WHERE x = {{val}}')
  assert.equal(result, prev)
})

test('reconcileAutoParams handles a mix: keep manual, drop stale auto, add new', () => {
  const prev = [
    { name: 'region', type: 'multiselect', default: [], required: false, auto: false },
    { name: 'stale', type: 'text', default: null, required: false, auto: true },
  ]
  const result = reconcileAutoParams(prev, 'WHERE r IN {{region|inclause}} AND b = {{branch}}')
  assert.deepEqual(result, [
    { name: 'region', type: 'multiselect', default: [], required: false, auto: false },
    { name: 'branch', type: 'text', default: null, required: false, auto: true },
  ])
})

test('reconcileAutoParams does not add a duplicate for a name already declared manually', () => {
  const prev = [{ name: 'region', type: 'multiselect', default: [], required: false, auto: false }]
  const result = reconcileAutoParams(prev, 'WHERE r IN {{region|inclause}}')
  assert.equal(result, prev)
})

// ── buildFilterParamSnippet / defaultForFilterParamType ─────────────────────

test('buildFilterParamSnippet: single value uses = and a text default of null', () => {
  const { snippet, param } = buildFilterParamSnippet({ name: 'branch', uiType: 'single' })
  assert.equal(snippet, '{% if branch %} AND <column> = {{ branch }} {% endif %}')
  assert.deepEqual(param, { name: 'branch', type: 'text', default: null, required: false, auto: false })
})

test('buildFilterParamSnippet: multiselect uses IN + inclause and defaults to []', () => {
  const { snippet, param } = buildFilterParamSnippet({ name: 'region', uiType: 'multiselect' })
  assert.equal(snippet, '{% if region %} AND <column> IN {{ region | inclause }} {% endif %}')
  assert.deepEqual(param, { name: 'region', type: 'multiselect', default: [], required: false, auto: false })
})

test('buildFilterParamSnippet: daterange uses two-sided dotted access', () => {
  const { snippet, param } = buildFilterParamSnippet({ name: 'window', uiType: 'daterange' })
  assert.equal(
    snippet,
    '{% if window %} AND <column> >= {{ window.from }} AND <column> < {{ window.to }} {% endif %}',
  )
  assert.deepEqual(param, { name: 'window', type: 'daterange', default: null, required: false, auto: false })
})

test('buildFilterParamSnippet snippet is valid input to extractPlaceholders (round-trip)', () => {
  for (const uiType of ['single', 'multiselect', 'daterange']) {
    const { snippet } = buildFilterParamSnippet({ name: 'roundtrip', uiType })
    assert.deepEqual(extractPlaceholders(snippet), ['roundtrip'])
  }
})

test('buildFilterParamSnippet never uses | sqlsafe (values always stay bound placeholders)', () => {
  for (const uiType of ['single', 'multiselect', 'daterange']) {
    const { snippet } = buildFilterParamSnippet({ name: 'x', uiType })
    assert.equal(snippet.includes('sqlsafe'), false)
  }
})

test('defaultForFilterParamType: [] for multiselect, null otherwise', () => {
  assert.deepEqual(defaultForFilterParamType('multiselect'), [])
  assert.equal(defaultForFilterParamType('single'), null)
  assert.equal(defaultForFilterParamType('daterange'), null)
})

// ── validateNewParamName ─────────────────────────────────────────────────────

test('validateNewParamName accepts a fresh valid identifier', () => {
  assert.equal(validateNewParamName('region', []), null)
  assert.equal(validateNewParamName('_private', ['other']), null)
})

test('validateNewParamName rejects empty/blank names', () => {
  assert.match(validateNewParamName('', []), /required/i)
  assert.match(validateNewParamName('   ', []), /required/i)
})

test('validateNewParamName rejects a name starting with a digit or containing a space', () => {
  assert.match(validateNewParamName('1region', []), /letters/i)
  assert.match(validateNewParamName('my region', []), /letters/i)
  assert.match(validateNewParamName('region-name', []), /letters/i)
})

test('validateNewParamName rejects a name already declared on the query', () => {
  assert.match(validateNewParamName('region', ['region', 'branch']), /already exists/i)
})
