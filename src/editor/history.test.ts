/**
 * history.test.mjs — node:test suite for the pure history reducer.
 *
 * Run: node --test src/editor/history.test.mjs
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  createHistory,
  push,
  undo,
  redo,
  canUndo,
  canRedo,
  HISTORY_LIMIT,
} from './history.js'

// ---------------------------------------------------------------------------
// Basic sequencing
// ---------------------------------------------------------------------------

test('createHistory sets present and empty stacks', () => {
  const h = createHistory('a')
  assert.equal(h.present, 'a')
  assert.deepEqual(h.past, [])
  assert.deepEqual(h.future, [])
})

test('push moves present to past and sets new present', () => {
  const h0 = createHistory('a')
  const h1 = push(h0, 'b')
  assert.equal(h1.present, 'b')
  assert.deepEqual(h1.past, ['a'])
  assert.deepEqual(h1.future, [])
})

test('push clears future', () => {
  const h0 = createHistory('a')
  const h1 = push(h0, 'b')
  const h2 = undo(h1)            // future = ['b']
  assert.equal(canRedo(h2), true)
  const h3 = push(h2, 'c')      // new push must clear future
  assert.deepEqual(h3.future, [])
  assert.equal(canRedo(h3), false)
})

test('push is a no-op when state is identical (===)', () => {
  const state = { x: 1 }
  const h0 = createHistory(state)
  const h1 = push(h0, state)
  assert.equal(h1, h0, 'same reference returned')
})

test('undo steps back through history', () => {
  let h = createHistory('a')
  h = push(h, 'b')
  h = push(h, 'c')

  const h2 = undo(h)
  assert.equal(h2.present, 'b')
  assert.deepEqual(h2.past, ['a'])
  assert.deepEqual(h2.future, ['c'])

  const h1 = undo(h2)
  assert.equal(h1.present, 'a')
  assert.deepEqual(h1.past, [])
  assert.deepEqual(h1.future, ['b', 'c'])
})

test('undo at empty past is a no-op', () => {
  const h = createHistory('a')
  assert.equal(canUndo(h), false)
  const h2 = undo(h)
  assert.equal(h2, h, 'same reference returned')
})

test('redo steps forward through future', () => {
  let h = createHistory('a')
  h = push(h, 'b')
  h = push(h, 'c')
  h = undo(h)
  h = undo(h)

  const h2 = redo(h)
  assert.equal(h2.present, 'b')
  assert.deepEqual(h2.future, ['c'])

  const h3 = redo(h2)
  assert.equal(h3.present, 'c')
  assert.deepEqual(h3.future, [])
})

test('redo at empty future is a no-op', () => {
  const h = createHistory('a')
  assert.equal(canRedo(h), false)
  const h2 = redo(h)
  assert.equal(h2, h, 'same reference returned')
})

test('undo then redo returns to original present', () => {
  let h = createHistory('a')
  h = push(h, 'b')
  h = push(h, 'c')

  const original = h.present  // 'c'
  h = undo(h)
  h = redo(h)
  assert.equal(h.present, original)
  assert.deepEqual(h.future, [])
})

// ---------------------------------------------------------------------------
// canUndo / canRedo
// ---------------------------------------------------------------------------

test('canUndo is false on fresh history, true after push', () => {
  const h0 = createHistory('x')
  assert.equal(canUndo(h0), false)
  const h1 = push(h0, 'y')
  assert.equal(canUndo(h1), true)
})

test('canRedo is false after push, true after undo', () => {
  let h = createHistory('x')
  h = push(h, 'y')
  assert.equal(canRedo(h), false)
  h = undo(h)
  assert.equal(canRedo(h), true)
})

// ---------------------------------------------------------------------------
// Cap enforcement
// ---------------------------------------------------------------------------

test(`cap: pushing >${HISTORY_LIMIT} entries keeps past.length === ${HISTORY_LIMIT}`, () => {
  let h = createHistory(0)
  const pushCount = HISTORY_LIMIT + 50 // push well beyond the cap
  for (let i = 1; i <= pushCount; i++) {
    h = push(h, i)
  }
  assert.equal(h.past.length, HISTORY_LIMIT, `past.length must not exceed ${HISTORY_LIMIT}`)
  assert.equal(h.present, pushCount, 'present is the last pushed value')
})

test('cap: oldest entries are dropped when cap exceeded', () => {
  let h = createHistory(0)
  // push 202 states: 1..202
  for (let i = 1; i <= HISTORY_LIMIT + 2; i++) {
    h = push(h, i)
  }
  // past should contain (HISTORY_LIMIT) items: states 2..201
  // (state 0 and state 1 are the two oldest that get dropped at HISTORY_LIMIT+2 pushes)
  assert.equal(h.past.length, HISTORY_LIMIT)
  // The oldest retained in past is state 2 (index 0 of past)
  assert.equal(h.past[0], 2)
  // The newest entry in past is HISTORY_LIMIT + 1
  assert.equal(h.past[h.past.length - 1], HISTORY_LIMIT + 1)
})

test('redo is cleared after a new push following undo', () => {
  let h = createHistory('a')
  h = push(h, 'b')
  h = push(h, 'c')
  h = undo(h) // future = ['c']
  assert.equal(canRedo(h), true)
  h = push(h, 'd') // clears future
  assert.equal(canRedo(h), false)
  assert.deepEqual(h.future, [])
  assert.equal(h.present, 'd')
})

test('full round-trip: push many, undo all, redo all', () => {
  const states = ['a', 'b', 'c', 'd', 'e']
  let h = createHistory(states[0])
  for (let i = 1; i < states.length; i++) {
    h = push(h, states[i])
  }

  // Undo all the way
  for (let i = states.length - 2; i >= 0; i--) {
    h = undo(h)
    assert.equal(h.present, states[i])
  }
  assert.equal(canUndo(h), false)

  // Redo all the way
  for (let i = 1; i < states.length; i++) {
    h = redo(h)
    assert.equal(h.present, states[i])
  }
  assert.equal(canRedo(h), false)
})

// ---------------------------------------------------------------------------
// Gesture coalescing — one undo step per user decision, not per emitted value
// ---------------------------------------------------------------------------

test('coalesce replaces the present instead of growing the past', () => {
  // A colour drag emits dozens of values for one decision. Without coalescing
  // each one became its own undo step, so Ctrl+Z walked back through the drag
  // one shade at a time.
  let h = createHistory({ n: 0 })
  h = push(h, { n: 1 })                       // gesture opens on the first value
  h = push(h, { n: 2 }, { coalesce: true })
  h = push(h, { n: 3 }, { coalesce: true })

  assert.equal(h.past.length, 1, 'the whole gesture is one entry')
  assert.deepEqual(h.present, { n: 3 })

  const back = undo(h)
  assert.deepEqual(back.present, { n: 0 }, 'undo lands before the gesture, not mid-drag')
})

test('a coalesced push with no history yet still records an entry', () => {
  // Nothing to replace — dropping it would lose the state outright.
  let h = createHistory({ n: 0 })
  h = push(h, { n: 1 }, { coalesce: true })
  assert.equal(h.past.length, 1)
  assert.deepEqual(h.present, { n: 1 })
})

test('a coalesced push still clears the redo branch', () => {
  let h = createHistory({ n: 0 })
  h = push(h, { n: 1 })
  h = push(h, { n: 2 })
  h = undo(h)
  assert.equal(h.future.length, 1, 'precondition: a redo is available')
  h = push(h, { n: 9 }, { coalesce: true })
  assert.equal(h.future.length, 0, 'editing after an undo abandons the redo branch')
})

test('normal pushes after a gesture start a new entry again', () => {
  let h = createHistory({ n: 0 })
  h = push(h, { n: 1 })
  h = push(h, { n: 2 }, { coalesce: true })
  h = push(h, { n: 3 })
  assert.equal(h.past.length, 2)
  assert.deepEqual(undo(h).present, { n: 2 }, 'the gesture result is its own step')
})
