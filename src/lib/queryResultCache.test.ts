/**
 * queryResultCache.test.ts — the in-browser memory of board query results.
 *
 * Run with:
 *   node --import tsx --test src/lib/queryResultCache.test.ts
 */

import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import {
  CACHE_MAX_ENTRIES,
  CACHE_TTL_MS,
  _setNowForTests,
  approxTableBytes,
  buildCacheKey,
  cacheIdentity,
  cachedRun,
  clearQueryResultCache,
  queryResultCacheSize,
} from './queryResultCache.js'

let now = 1_000_000
beforeEach(() => {
  clearQueryResultCache()
  now = 1_000_000
  _setNowForTests(() => now)
})

function counter(result: any = { table: { numRows: 1 } }) {
  let calls = 0
  const run = async () => { calls += 1; return result }
  return { run, calls: () => calls }
}

const failure = (r: any) => !!r?.error

test('a second identical request reuses the first result', async () => {
  const c = counter()
  const a = await cachedRun('k', c.run, { isFailure: failure })
  const b = await cachedRun('k', c.run, { isFailure: failure })
  assert.equal(c.calls(), 1)
  assert.equal(a, b)
})

test('identical requests in flight at the same time share one call', async () => {
  let resolve: (v: any) => void = () => {}
  let calls = 0
  const run = () => { calls += 1; return new Promise((r) => { resolve = r }) }
  const p1 = cachedRun('k', run)
  const p2 = cachedRun('k', run)
  resolve({ table: {} })
  assert.deepEqual(await p1, await p2)
  assert.equal(calls, 1)
})

test('a failed result is not remembered, so Retry runs the query again', async () => {
  const bad = counter({ table: null, error: { message: 'boom' } })
  await cachedRun('k', bad.run, { isFailure: failure })
  await cachedRun('k', bad.run, { isFailure: failure })
  assert.equal(bad.calls(), 2)
  assert.equal(queryResultCacheSize(), 0)
})

test('a thrown error is not remembered', async () => {
  let calls = 0
  const run = async () => { calls += 1; throw new Error('network') }
  await assert.rejects(cachedRun('k', run))
  await assert.rejects(cachedRun('k', run))
  assert.equal(calls, 2)
})

test('results expire after the time limit', async () => {
  const c = counter()
  await cachedRun('k', c.run)
  now += CACHE_TTL_MS - 1
  await cachedRun('k', c.run)
  assert.equal(c.calls(), 1)
  now += 2
  await cachedRun('k', c.run)
  assert.equal(c.calls(), 2)
})

test('clearQueryResultCache forgets everything', async () => {
  const c = counter()
  await cachedRun('k', c.run)
  clearQueryResultCache()
  await cachedRun('k', c.run)
  assert.equal(c.calls(), 2)
})

test('the oldest results are dropped past the entry limit', async () => {
  for (let i = 0; i < CACHE_MAX_ENTRIES + 5; i++) await cachedRun(`k${i}`, counter().run)
  assert.equal(queryResultCacheSize(), CACHE_MAX_ENTRIES)
  const first = counter()
  await cachedRun('k0', first.run)
  assert.equal(first.calls(), 1, 'k0 was evicted, so it runs again')
})

test('the key changes with user, workspace, filters, params, limit and refresh', () => {
  const base = {
    identity: 'user-1',
    tenant: { 'X-Org-Id': 'org-a' },
    queryId: 'q1',
    namedParams: { month: '2026-10' },
    filters: [{ column: 'Area', value: 'NB' }],
    datastoreId: null,
    limit: 501,
    epoch: 0,
  }
  const k = buildCacheKey(base)
  assert.equal(k, buildCacheKey({ ...base, namedParams: { month: '2026-10' } }))
  assert.notEqual(k, buildCacheKey({ ...base, identity: 'user-2' }))
  assert.notEqual(k, buildCacheKey({ ...base, tenant: { 'X-Org-Id': 'org-b' } }))
  assert.notEqual(k, buildCacheKey({ ...base, queryId: 'q2' }))
  assert.notEqual(k, buildCacheKey({ ...base, namedParams: { month: '2026-11' } }))
  assert.notEqual(k, buildCacheKey({ ...base, filters: [{ column: 'Area', value: 'TX' }] }))
  assert.notEqual(k, buildCacheKey({ ...base, limit: 1001 }))
  assert.notEqual(k, buildCacheKey({ ...base, epoch: 1 }))
})

test('param key order does not matter', () => {
  const a = buildCacheKey({ identity: 'u', tenant: {}, queryId: 'q', namedParams: { a: 1, b: 2 } })
  const b = buildCacheKey({ identity: 'u', tenant: {}, queryId: 'q', namedParams: { b: 2, a: 1 } })
  assert.equal(a, b)
})

test('cacheIdentity reads the token subject and refuses anything else', () => {
  const payload = Buffer.from(JSON.stringify({ sub: 'abc-123', exp: 1 })).toString('base64url')
  assert.equal(cacheIdentity(`h.${payload}.s`), 'abc-123')
  assert.equal(cacheIdentity(null), null)
  assert.equal(cacheIdentity('not-a-jwt'), null)
  assert.equal(cacheIdentity('h.!!!.s'), null)
  const noSub = Buffer.from(JSON.stringify({ exp: 1 })).toString('base64url')
  assert.equal(cacheIdentity(`h.${noSub}.s`), null)
})

test('approxTableBytes sums Arrow data sizes and tolerates odd input', () => {
  assert.equal(approxTableBytes({ data: [{ byteLength: 10 }, { byteLength: 5 }] }), 15)
  assert.equal(approxTableBytes(null), 0)
  assert.equal(approxTableBytes({}), 0)
})
