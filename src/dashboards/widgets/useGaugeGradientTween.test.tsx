/**
 * useGaugeGradientTween — intro sweep on reset, update sweep on a new value,
 * snaps when disabled / reduced motion. Real React (react-dom/client + act)
 * on jsdom, with a hand-driven requestAnimationFrame clock.
 */
import { test, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<!doctype html><div id="root"></div>')
const g = globalThis as any
g.window = dom.window
g.document = dom.window.document
g.IS_REACT_ACT_ENVIRONMENT = true

let reducedMotion = false
dom.window.matchMedia = ((q: string) => ({
  matches: reducedMotion && q.includes('reduce'),
  addEventListener() {}, removeEventListener() {},
})) as any

// Manual rAF: frames run only when the test calls frame(t).
let queue = new Map<number, (t: number) => void>()
let nextId = 1
g.requestAnimationFrame = (cb: (t: number) => void) => { const id = nextId++; queue.set(id, cb); return id }
g.cancelAnimationFrame = (id: number) => { queue.delete(id) }

const React = await import('react')
const { createRoot } = await import('react-dom/client')
const { act } = await import('react')
const { useGaugeGradientTween, GAUGE_TWEEN_MS } = await import('./useGaugeGradientTween.js')

type Out = { value: number | null; tweening: boolean }
type Props = { target: number | null; enabled?: boolean; resetKey?: string; introFrom?: number }
let renders: Out[] = []
let root: any

function Probe({ target, enabled = true, resetKey = 'gauge:ring', introFrom = 0 }: Props) {
  renders.push(useGaugeGradientTween(target, { enabled, resetKey, introFrom }))
  return null
}
const last = () => renders[renders.length - 1]
async function render(props: Props) { await act(async () => { root.render(React.createElement(Probe, props)) }) }
async function frame(t: number) {
  await act(async () => {
    const cbs = [...queue.values()]
    queue = new Map()
    for (const cb of cbs) cb(t)
  })
}
// Run a tween to completion: first frame pins t0, then one past the duration.
async function finish(t0 = 0) { await frame(t0); await frame(t0 + GAUGE_TWEEN_MS) }

beforeEach(() => {
  renders = []; queue = new Map(); reducedMotion = false
  root = createRoot(document.getElementById('root'))
})
afterEach(async () => { await act(async () => root.unmount()) })

test('first value → intro sweep from the gauge min with cubicOut', async () => {
  await render({ target: 100, introFrom: 0 })
  // The very first render already draws the start, never the final arc.
  assert.deepEqual(renders[0], { value: 0, tweening: true })
  await frame(0)
  await frame(GAUGE_TWEEN_MS / 2)
  assert.deepEqual(last(), { value: 88, tweening: true })   // cubicOut(0.5) = 0.875
  await frame(GAUGE_TWEEN_MS)
  assert.deepEqual(last(), { value: 100, tweening: false })
})

test('ring: value refresh sweeps from the displayed value with cubicInOut, no final-value flash', async () => {
  await render({ target: 20 })
  await finish()
  assert.deepEqual(last(), { value: 20, tweening: false })

  const before = renders.length
  await render({ target: 72 })
  // Render the new value arrives on: still at 20 (no one-frame jump to 72).
  assert.deepEqual(renders[before], { value: 20, tweening: true })
  await frame(5000)
  await frame(5000 + GAUGE_TWEEN_MS / 2)
  assert.deepEqual(last(), { value: 46, tweening: true })   // cubicInOut(0.5) = 0.5
  assert.ok(renders.slice(before).every((r) => r.value >= 20 && r.value <= 72))
  await frame(5000 + GAUGE_TWEEN_MS)
  assert.deepEqual(last(), { value: 72, tweening: false })
})

test('needle ↔ ring (resetKey change) replays the intro from min', async () => {
  await render({ target: 50, resetKey: 'gauge:needle', introFrom: 10 })
  await finish()
  await render({ target: 50, resetKey: 'gauge:ring', introFrom: 10 })
  assert.deepEqual(last(), { value: 10, tweening: true })
  await finish(9000)
  assert.deepEqual(last(), { value: 50, tweening: false })
})

test('new target mid-tween retargets from the current display value', async () => {
  await render({ target: 0 })                 // equals introFrom → no intro
  assert.deepEqual(last(), { value: 0, tweening: false })
  await render({ target: 100 })
  await frame(0); await frame(GAUGE_TWEEN_MS / 2)
  const mid = last().value as number
  assert.equal(mid, 50)
  await render({ target: 10 })
  assert.deepEqual(last(), { value: mid, tweening: true })
  await finish(2000)
  assert.deepEqual(last(), { value: 10, tweening: false })
})

test('disabled (no gradient / animation off) → snaps, never tweens', async () => {
  await render({ target: 30, enabled: false })
  await render({ target: 80, enabled: false })
  assert.ok(renders.every((r) => !r.tweening))
  assert.equal(queue.size, 0)
  assert.deepEqual(last(), { value: 80, tweening: false })
})

test('turning off mid-tween snaps to the target in the same render', async () => {
  await render({ target: 0 })
  await render({ target: 100 })
  await frame(0); await frame(GAUGE_TWEEN_MS / 2)
  const before = renders.length
  await render({ target: 100, enabled: false })
  assert.deepEqual(renders[before], { value: 100, tweening: false })
  assert.equal(queue.size, 0)
})

test('prefers-reduced-motion → snaps, never tweens', async () => {
  reducedMotion = true
  await render({ target: 30 })
  await render({ target: 80 })
  assert.ok(renders.every((r) => !r.tweening))
  assert.deepEqual(last(), { value: 80, tweening: false })
})

test('StrictMode mount re-run keeps the intro (min start, cubicOut)', async () => {
  await act(async () => {
    root.render(React.createElement(React.StrictMode, null, React.createElement(Probe, { target: 100 })))
  })
  assert.equal(queue.size, 1)                  // first run's frame cancelled
  await frame(0); await frame(GAUGE_TWEEN_MS / 2)
  assert.deepEqual(last(), { value: 88, tweening: true })
})
