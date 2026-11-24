/**
 * Tests for queryEstimate.logic.ts — what the "This query will process X when
 * run" line asks about for what's on screen, how a highlighted part is
 * picked, and how bytes are shown (BigQuery's units).
 * Run with the repo's test:dash convention.
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  bodyFromKey,
  estimateMessage,
  formatBytes,
  pickSelection,
  planQueryEstimate,
  readEstimateResponse,
  usesFilterTags,
} from './queryEstimate.logic.js'

const base = {
  selection: '',
  sql: 'SELECT 1',
  savedSql: null,
  queryId: null,
  namedParams: {},
  hasParamValues: false,
  datastoreId: '',
}

test('formatBytes uses 1,024-based units like the BigQuery console', () => {
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatBytes(1023), '1023 B')
  assert.equal(formatBytes(1024), '1 KiB')
  assert.equal(formatBytes(638054), '623.1 KiB') // the console's own example
  assert.equal(formatBytes(5060194464), '4.71 GiB')
  assert.equal(formatBytes(1.5 * 1024 ** 3), '1.5 GiB')
  assert.equal(formatBytes(1024 ** 4), '1 TiB')
  // rounding up into the next unit never shows "1024 KiB"
  assert.equal(formatBytes(1024 * 1024 - 40), '1 MiB')
  assert.equal(formatBytes(-1), '')
  assert.equal(formatBytes(Number.NaN), '')
})

test('pickSelection: nothing, whitespace or the whole query means "run the query"', () => {
  assert.equal(pickSelection('', 'SELECT 1'), '')
  assert.equal(pickSelection('   \n', 'SELECT 1'), '')
  assert.equal(pickSelection(undefined, 'SELECT 1'), '')
  assert.equal(pickSelection(' SELECT 1 \n', 'SELECT 1'), '')
  assert.equal(pickSelection('  SELECT a FROM t \n', 'WITH x AS (SELECT a FROM t) SELECT * FROM x'), 'SELECT a FROM t')
})

test('usesFilterTags: {{ }} / {% %} / {# #} need a saved query; {{ vars.* }} does not', () => {
  assert.equal(usesFilterTags('SELECT 1'), false)
  assert.equal(usesFilterTags("WHERE m = {{ Actual_FY }}"), true)
  assert.equal(usesFilterTags('{% if Area %} AND Area IN {{ Area }} {% endif %}'), true)
  assert.equal(usesFilterTags('{# note #} SELECT 1'), true)
  assert.equal(usesFilterTags('FROM {{ vars.dataset }}.sales'), false)
  assert.equal(usesFilterTags('FROM {{vars.dataset}}.sales'), false)
  assert.equal(usesFilterTags(null), false)
})

test('a highlighted part is estimated on its own, with the connector', () => {
  const plan = planQueryEstimate({ ...base, selection: 'SELECT a FROM t', queryId: 'q1', savedSql: 'x', datastoreId: 'ds1' })
  assert.deepEqual(plan && plan.kind === 'request' && plan.body, { sql: 'SELECT a FROM t', datastore_id: 'ds1' })
  assert.equal(plan && plan.scope, 'selection')
})

test('a highlighted part with filter tags is not sent', () => {
  const plan = planQueryEstimate({ ...base, selection: 'WHERE m = {{ Actual_FY }}', queryId: 'q1' })
  assert.deepEqual(plan, { kind: 'blocked', scope: 'selection', reason: 'filter-tags' })
})

test('a saved query is estimated by id with the filter values Run sends', () => {
  const plan = planQueryEstimate({
    ...base, sql: 'saved sql', savedSql: 'saved sql', queryId: 'q1',
    namedParams: { Actual_FY: '2026-05' }, datastoreId: 'ds1',
  })
  assert.equal(plan && plan.kind, 'request')
  assert.deepEqual(plan && plan.kind === 'request' && plan.body, {
    query_id: 'q1', named_params: { Actual_FY: '2026-05' }, datastore_id: 'ds1',
  })
  assert.equal(plan && plan.scope, 'query')
})

test('unsaved changes to a saved query are sized as typed when they are plain SQL', () => {
  const plan = planQueryEstimate({ ...base, sql: 'SELECT a FROM t', savedSql: 'SELECT * FROM t', queryId: 'q1', datastoreId: 'ds1' })
  assert.equal(plan && plan.scope, 'edited')
  assert.deepEqual(plan && plan.kind === 'request' && plan.body, { sql: 'SELECT a FROM t', datastore_id: 'ds1' })
})

test('unsaved changes with filter tags ask for Update; emptied SQL shows nothing', () => {
  const tagged = planQueryEstimate({ ...base, sql: 'SELECT {{ x }} -- edit', savedSql: 'SELECT {{ x }}', queryId: 'q1' })
  assert.deepEqual(tagged, { kind: 'blocked', scope: 'edited', reason: 'needs-update' })
  assert.equal(planQueryEstimate({ ...base, sql: '  ', savedSql: 'SELECT 1', queryId: 'q1' }), null)
})

test('a saved query is checked again after Update (same request, new save)', () => {
  const before = planQueryEstimate({ ...base, sql: 's', savedSql: 's', queryId: 'q1', savedVersion: 0 })
  const again = planQueryEstimate({ ...base, sql: 's', savedSql: 's', queryId: 'q1', savedVersion: 0 })
  const after = planQueryEstimate({ ...base, sql: 's2', savedSql: 's2', queryId: 'q1', savedVersion: 1 })
  const key = (p) => (p && p.kind === 'request' ? p.key : '')
  assert.equal(key(before), key(again))
  assert.notEqual(key(before), key(after))
  // the key carries the body the check sends
  assert.deepEqual(bodyFromKey(key(after)), { query_id: 'q1' })
})

test('an unsaved query is estimated as the editor text', () => {
  const plan = planQueryEstimate({ ...base, sql: 'SELECT 2' })
  assert.deepEqual(plan && plan.kind === 'request' && plan.body, { sql: 'SELECT 2' })
})

test('an unsaved query with filter tags or values asks to be saved; empty SQL shows nothing', () => {
  assert.deepEqual(planQueryEstimate({ ...base, sql: 'SELECT {{ x }}' }), { kind: 'blocked', scope: 'query', reason: 'needs-save' })
  assert.deepEqual(planQueryEstimate({ ...base, hasParamValues: true }), { kind: 'blocked', scope: 'query', reason: 'needs-save' })
  assert.equal(planQueryEstimate({ ...base, sql: '  \n ' }), null)
})

test('readEstimateResponse: bytes, a failed BigQuery dry run, other databases', () => {
  assert.deepEqual(
    readEstimateResponse({ supported: true, est_bytes_scanned: 5060194464, connector_type: 'bigquery' }),
    { status: 'ok', bytes: 5060194464 },
  )
  assert.deepEqual(readEstimateResponse({ supported: true, est_bytes_scanned: 0, connector_type: 'bigquery' }), { status: 'ok', bytes: 0 })
  assert.deepEqual(readEstimateResponse({ supported: false, est_bytes_scanned: null, connector_type: 'bigquery' }), { status: 'failed' })
  assert.deepEqual(readEstimateResponse({ supported: true, est_bytes_scanned: null, est_rows: 10, connector_type: 'duckdb' }), { status: 'unsupported' })
  assert.deepEqual(readEstimateResponse({ supported: false, connector_type: 'postgres' }), { status: 'unsupported' })
  assert.deepEqual(readEstimateResponse(null), { status: 'unsupported' })
})

test('estimateMessage wording', () => {
  const req = (scope) => ({ kind: 'request' as const, scope, key: 'k', body: {} })
  const ok = { status: 'ok' as const, bytes: 5060194464 }
  assert.equal(estimateMessage(req('query'), ok)?.text, 'This query will process 4.71 GiB when run.')
  assert.equal(estimateMessage(req('selection'), ok)?.text, 'The highlighted part will process 4.71 GiB when run.')
  assert.equal(estimateMessage(req('edited'), ok)?.text, 'With your unsaved changes, this query would process 4.71 GiB.')
  assert.deepEqual(estimateMessage(req('query'), { status: 'checking' }), { tone: 'busy', text: 'Checking the size…' })
  assert.equal(estimateMessage(req('query'), { status: 'failed' })?.tone, 'muted')
  assert.equal(
    estimateMessage(req('query'), { status: 'unsupported' })?.text,
    'Only BigQuery connectors can show how much data a query will process.',
  )
  assert.match(estimateMessage(req('query'), { status: 'failed', timedOut: true })?.text ?? '', /took too long/)
  assert.equal(estimateMessage(req('query'), { status: 'idle' }), null)
  assert.equal(estimateMessage(null, ok), null)
  assert.equal(estimateMessage({ kind: 'blocked', scope: 'selection', reason: 'filter-tags' }, { status: 'idle' })?.tone, 'warn')
  assert.equal(
    estimateMessage({ kind: 'blocked', scope: 'edited', reason: 'needs-update' }, { status: 'idle' })?.text,
    'Press Update to see how much data your changes will process.',
  )
})
