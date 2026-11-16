/** Calm scroll: the capped follow and the settle target (pure parts of scroll.ts). */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { FOLLOW_VMAX, followStep, settleDuration, settleTarget } from './scroll'
import { compileStory } from './stage/story'
import hero from './scenes/hero/index'
import columns from './scenes/columns/index'
import cache from './scenes/cache/index'
import security from './scenes/security/index'
import board from './scenes/board/index'
import agents from './scenes/agents/index'

const story = compileStory([hero, columns, cache, security, board, agents])
const morphs = story.segs.filter(s => s.kind === 'morph')

test('a flick cannot rush a morph: within a beat the follow speed is capped', () => {
  let cur = 0
  for (let i = 0; i < 60; i++) cur = followStep(cur, 1, 1 / 60)          // one second at 60 fps
  assert.ok(cur <= FOLLOW_VMAX + 1e-9, `moved ${cur} beats in 1 s`)
  assert.ok(cur > 0.6, 'but it still keeps up')
})

test('the follow converges without overshoot, in both directions', () => {
  for (const [a, b] of [[0, 0.4], [3, 2.2], [10, 2]]) {
    let cur = a
    for (let i = 0; i < 1200; i++) { const n = followStep(cur, b, 1 / 60); assert.ok(Math.abs(b - n) <= Math.abs(b - cur) + 1e-12); cur = n }
    assert.equal(cur, b)
  }
})

test('long jumps catch up faster than in-beat motion', () => {
  const near = followStep(0, 1, 0.1), far = followStep(0, 8, 0.1)
  assert.ok(far > near * 3)
})

const atRest = (T: number) => story.segs.some(s => s.kind === 'rest' && T >= s.t0 - 1e-9 && T <= s.t1 + 1e-9)

test('settle: rest points stay put; a half-played morph finishes in the direction of travel', () => {
  for (const l of story.labels) assert.equal(settleTarget(story, l.T, 1), null)
  for (const s of morphs) {
    const mid = (s.t0 + s.t1) / 2
    const fwd = settleTarget(story, mid, 1)!, back = settleTarget(story, mid, -1)!
    assert.ok(fwd >= s.t1 && atRest(fwd), `forward lands at rest after ${s.t1}`)
    assert.ok(back <= s.t0 && atRest(back), `backward lands at rest before ${s.t0}`)
    assert.ok(settleTarget(story, s.t0 + (s.t1 - s.t0) * 0.05, 1)! <= s.t0, 'barely started: go back')
    assert.ok(settleTarget(story, s.t0 + (s.t1 - s.t0) * 0.95, -1)! >= s.t1, 'barely undone: finish it')
  }
})

test('settle lands inside the rest, so a small scroll back does not restart the morph', () => {
  for (const s of morphs) {
    const fwd = settleTarget(story, (s.t0 + s.t1) / 2, 1)!
    assert.equal(settleTarget(story, fwd, 1), null)
    assert.equal(settleTarget(story, fwd - 0.05, -1), null, 'a 0.05-beat nudge back stays at rest')
  }
})

test('settle glides are unhurried', () => {
  assert.ok(settleDuration(0.05) >= 0.8)
  assert.ok(settleDuration(1.4) <= 1.6)
})
