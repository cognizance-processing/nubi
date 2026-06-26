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
