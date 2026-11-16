import { test } from 'node:test'
import assert from 'node:assert/strict'
import { withStepperChildren } from './stepperChildren.js'

test('withStepperChildren: includes nested step widgets, any depth', () => {
  const inner = { id: 'txt', type: 'text', source: { provider: 'p', result: 'r' } }
  const nested = { id: 's2', type: 'stepper', props: { steps: [{ label: 'b', widget: inner }] } }
  const widgets = [
    { id: 'kpi', type: 'kpi' },
    { id: 's1', type: 'stepper', props: { steps: [{ label: 'a', widget: nested }, null, { label: 'empty' }] } },
  ]
  assert.deepEqual(withStepperChildren(widgets).map((w) => w.id), ['kpi', 's1', 's2', 'txt'])
})

test('withStepperChildren: tolerates junk', () => {
  assert.deepEqual(withStepperChildren(undefined), [])
  assert.deepEqual(withStepperChildren([null, 3, { id: 'a', type: 'stepper', props: { steps: 'x' } }]).map((w) => w.id), ['a'])
})
