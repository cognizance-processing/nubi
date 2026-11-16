/**
 * sqlHistory.test.ts — pure-logic tests for the browser-local SQL run history.
 * Mocks `localStorage` in-memory (Node has no global localStorage) so these
 * run under `node --test`, same as history.test.ts / paramWiring.test.ts.
 */
import assert from 'node:assert/strict'
import { beforeEach, test } from 'node:test'

function installFakeLocalStorage() {
  const store = new Map<string, string>()
  ;(globalThis as any).localStorage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => { store.set(k, v) },
    removeItem: (k: string) => { store.delete(k) },
  }
  return store
}

beforeEach(() => {
  installFakeLocalStorage()
})

test('loadSqlHistory returns [] when nothing stored', async () => {
  const { loadSqlHistory } = await import('./sqlHistory.js')
  assert.deepEqual(loadSqlHistory(), [])
})

test('pushSqlHistory adds an entry with id + ranAt, newest first', async () => {
  const { pushSqlHistory } = await import('./sqlHistory.js')
  const after = pushSqlHistory({ sql: 'SELECT 1', ok: true })
  assert.equal(after.length, 1)
  assert.equal(after[0].sql, 'SELECT 1')
  assert.ok(after[0].id)
  assert.ok(after[0].ranAt > 0)
})

test('pushSqlHistory persists across loads', async () => {
  const { pushSqlHistory, loadSqlHistory } = await import('./sqlHistory.js')
  pushSqlHistory({ sql: 'SELECT 1', ok: true })
  pushSqlHistory({ sql: 'SELECT 2', ok: true })
  const loaded = loadSqlHistory()
  assert.equal(loaded.length, 2)
  assert.equal(loaded[0].sql, 'SELECT 2') // newest first
  assert.equal(loaded[1].sql, 'SELECT 1')
})

test('re-running the same SQL replaces the top entry instead of duplicating', async () => {
  const { pushSqlHistory } = await import('./sqlHistory.js')
  pushSqlHistory({ sql: 'SELECT 1', ok: false, error: 'boom' })
  const after = pushSqlHistory({ sql: 'SELECT 1', ok: true })
  assert.equal(after.length, 1)
  assert.equal(after[0].ok, true)
})

test('a different SQL after a repeat does not collapse older distinct entries', async () => {
  const { pushSqlHistory } = await import('./sqlHistory.js')
  pushSqlHistory({ sql: 'SELECT 1', ok: true })
  pushSqlHistory({ sql: 'SELECT 2', ok: true })
  const after = pushSqlHistory({ sql: 'SELECT 3', ok: true })
  assert.equal(after.length, 3)
  assert.deepEqual(after.map(e => e.sql), ['SELECT 3', 'SELECT 2', 'SELECT 1'])
})

test('history is capped at 50 entries, oldest dropped', async () => {
  const { pushSqlHistory } = await import('./sqlHistory.js')
  let last
  for (let i = 0; i < 55; i++) {
    last = pushSqlHistory({ sql: `SELECT ${i}`, ok: true })
  }
  assert.equal(last!.length, 50)
  assert.equal(last![0].sql, 'SELECT 54')
  assert.equal(last![49].sql, 'SELECT 5') // the oldest 5 (0..4) were dropped
})

test('clearSqlHistory empties storage', async () => {
  const { pushSqlHistory, clearSqlHistory, loadSqlHistory } = await import('./sqlHistory.js')
  pushSqlHistory({ sql: 'SELECT 1', ok: true })
  const cleared = clearSqlHistory()
  assert.deepEqual(cleared, [])
  assert.deepEqual(loadSqlHistory(), [])
})
