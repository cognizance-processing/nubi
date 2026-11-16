/**
 * The in-browser engine must fail loudly: a start that errors or stalls
 * rejects with a readable message, and handing a result to the engine never
 * blocks or throws at the caller.
 *
 * Run: node --import tsx --test src/lib/engineStart.test.ts
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import { startEngine, createShareQueue } from './engineStart.js'

function fakeEngine(instantiate) {
  const worker = {
    listeners: {},
    terminated: false,
    addEventListener(type, fn) { this.listeners[type] = fn },
    terminate() { this.terminated = true },
  }
  class AsyncDuckDB {
    constructor(_logger, _worker) {}
    instantiate(_main, _pthread, progress) { return instantiate(progress, worker) }
  }
  const load = async () => ({
    duckdb: { selectBundle: async (b) => b.eh, AsyncDuckDB, VoidLogger: class {} },
    bundles: { eh: { mainModule: '/m.wasm', mainWorker: '/w.js' } },
  })
  return { worker, load, createWorker: () => worker }
}

describe('startEngine', () => {
  test('resolves with the engine once it has started', async () => {
    const { load, createWorker } = fakeEngine(async () => null)
    const db = await startEngine({ load, createWorker, stallMs: 200 })
    assert.ok(db)
  })

  test('rejects when the load stops making progress', async () => {
    const { load, createWorker, worker } = fakeEngine(() => new Promise(() => {}))
    await assert.rejects(
      startEngine({ load, createWorker, stallMs: 60 }),
      /could not start \(no progress for/,
    )
    assert.equal(worker.terminated, true)
  })

  test('a slow load that keeps reporting progress is not a stall', async () => {
    const { load, createWorker } = fakeEngine((progress) => new Promise((resolve) => {
      let ticks = 0
      const timer = setInterval(() => {
        progress({})
        if (++ticks === 8) { clearInterval(timer); resolve(null) }
      }, 30)
    }))
    assert.ok(await startEngine({ load, createWorker, stallMs: 100 }))
  })

  test('rejects with the reason when the worker errors', async () => {
    const { load, createWorker } = fakeEngine((_p, worker) => new Promise(() => {
      setTimeout(() => worker.listeners.error({ message: 'blocked by policy' }), 10)
    }))
    await assert.rejects(
      startEngine({ load, createWorker, stallMs: 5000 }),
      /could not start \(blocked by policy\)/,
    )
  })

  test('rejects when the engine code itself cannot be loaded', async () => {
    await assert.rejects(
      startEngine({ load: async () => { throw new Error('chunk failed') }, createWorker: () => null, stallMs: 50 }),
      /could not start \(chunk failed\)/,
    )
  })
})

describe('createShareQueue', () => {
  test('returns before the engine is ready and registers every name in order', async () => {
    const seen = []
    let release
    const gate = new Promise((r) => { release = r })
    const register = async (name) => { await gate; seen.push(name) }
    const done = createShareQueue(register).share(['cell_1', undefined, 'orders'], {})
    assert.deepEqual(seen, [])
    release()
    await done
    assert.deepEqual(seen, ['cell_1', 'orders'])
  })

  test('reports a failure through onError instead of throwing', async () => {
    const errors = []
    const register = async () => { throw new Error('engine down') }
    await createShareQueue(register).share(['cell_1', 'alias'], {}, (e) => errors.push(e.message))
    assert.deepEqual(errors, ['engine down'])
  })

  test('an alias failing is not reported', async () => {
    const errors = []
    const register = async (name) => { if (name !== 'cell_1') throw new Error('bad name') }
    await createShareQueue(register).share(['cell_1', 'alias'], {}, (e) => errors.push(e.message))
    assert.deepEqual(errors, [])
  })
})
