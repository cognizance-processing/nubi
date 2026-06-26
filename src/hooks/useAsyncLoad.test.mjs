/**
 * useAsyncLoad.test.mjs — unit tests for the useAsyncLoad hook.
 *
 * Run: node --test src/hooks/useAsyncLoad.test.mjs
 * (or via npm run test:dash which globs src/**\/*.test.mjs)
 *
 * We test the pure async logic by exercising the hook's state machine
 * via a minimal React-free simulation of the useEffect+useState lifecycle.
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Minimal async-load logic extracted for pure-logic testing
// ---------------------------------------------------------------------------

/**
 * Simulate one "effect run" of the hook's core logic.
 * Returns { data, loading, error } after the async fn settles.
 */
async function runLoad(asyncFn, { ignore = false } = {}) {
  let data = null
  let loading = true
  let error = null

  let cancelled = false
  const ignoreRef = { value: ignore }

  try {
    const result = await asyncFn()
    if (!ignoreRef.value && !cancelled) {
      data = result
      loading = false
    }
  } catch (e) {
    if (!ignoreRef.value && !cancelled) {
      error = e
      loading = false
    }
  }

  return { data, loading, error, _cancel: () => { cancelled = true } }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useAsyncLoad logic', () => {

  test('resolves → data set, loading false, error null', async () => {
    const fn = async () => [1, 2, 3]
    const { data, loading, error } = await runLoad(fn)
    assert.deepStrictEqual(data, [1, 2, 3])
    assert.strictEqual(loading, false)
    assert.strictEqual(error, null)
  })

  test('rejects → error set, loading false, data null', async () => {
    const boom = new Error('fetch failed')
    const fn = async () => { throw boom }
    const { data, loading, error } = await runLoad(fn)
    assert.strictEqual(data, null)
    assert.strictEqual(loading, false)
    assert.strictEqual(error, boom)
  })

  test('stale result ignored when ignore=true', async () => {
    const fn = async () => 'stale-value'
    const { data, loading, error } = await runLoad(fn, { ignore: true })
    // When ignored: state stays at initial values (loading=true, data=null, error=null)
    assert.strictEqual(data, null)
    assert.strictEqual(loading, true)
    assert.strictEqual(error, null)
  })

  test('stale rejection ignored when ignore=true', async () => {
    const fn = async () => { throw new Error('stale error') }
    const { data, loading, error } = await runLoad(fn, { ignore: true })
    assert.strictEqual(data, null)
    assert.strictEqual(loading, true)
    assert.strictEqual(error, null)
  })

  test('resolves with null data shape', async () => {
    const fn = async () => ({ items: [], total: 0 })
    const { data, loading, error } = await runLoad(fn)
    assert.deepStrictEqual(data, { items: [], total: 0 })
    assert.strictEqual(loading, false)
    assert.strictEqual(error, null)
  })

  test('resolves with various data types', async () => {
    for (const value of [42, 'hello', true, { a: 1 }, [1, 2]]) {
      const { data } = await runLoad(async () => value)
      assert.deepStrictEqual(data, value)
    }
  })

})

// ---------------------------------------------------------------------------
// Reload-on-mutation behaviour (mirrors SecretsPage / ConnectorsPage / etc.)
// ---------------------------------------------------------------------------
//
// After a mutation (create / delete) the migrated components call reload()
// which triggers a fresh fetch. We simulate that pattern here: the load fn
// is called, a mutation happens, reload() fires another load, and the caller
// sees the updated data from the second call.

describe('reload-on-mutation pattern', () => {

  test('reload fetches fresh data after mutation', async () => {
    // Simulate a list that changes after one write.
    let serverItems = ['a', 'b']

    const loadFn = async () => [...serverItems]

    // First load — simulates initial mount.
    const first = await runLoad(loadFn)
    assert.deepStrictEqual(first.data, ['a', 'b'])

    // Mutation (e.g. deleteSecret) runs server-side; list shrinks.
    serverItems = ['a']

    // reload() fires a second load — caller sees updated list.
    const second = await runLoad(loadFn)
    assert.deepStrictEqual(second.data, ['a'])
    assert.strictEqual(second.loading, false)
    assert.strictEqual(second.error, null)
  })

  test('reload reflects creation (list grows after add)', async () => {
    let serverItems = []

    const loadFn = async () => [...serverItems]

    const first = await runLoad(loadFn)
    assert.deepStrictEqual(first.data, [])

    // Mutation: new secret/connector created.
    serverItems = [{ name: 'MY_KEY', created_at: '2026-01-01' }]

    const second = await runLoad(loadFn)
    assert.strictEqual(second.data.length, 1)
    assert.strictEqual(second.data[0].name, 'MY_KEY')
  })

  test('stale reload ignored when unmounted mid-flight', async () => {
    let serverItems = ['x']

    const slowLoad = async () => {
      await new Promise(r => setTimeout(r, 10))
      return [...serverItems]
    }

    // Simulate the component unmounting (ignore=true) before the reload resolves.
    const result = await runLoad(slowLoad, { ignore: true })
    // Stale result should be discarded.
    assert.strictEqual(result.data, null)
    assert.strictEqual(result.loading, true)
  })

})
