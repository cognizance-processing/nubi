import { test } from 'node:test'
import assert from 'node:assert/strict'
import { statusBadgeProps } from './connectionStatus.js'

test('online maps to success/Connected', () => {
  assert.deepEqual(statusBadgeProps('online'), { variant: 'success', label: 'Connected' })
})

test('offline maps to danger/Offline', () => {
  assert.deepEqual(statusBadgeProps('offline'), { variant: 'danger', label: 'Offline' })
})

test('unknown maps to default/Not tested', () => {
  assert.deepEqual(statusBadgeProps('unknown'), { variant: 'default', label: 'Not tested' })
})

test('checking maps to default/Checking…', () => {
  assert.deepEqual(statusBadgeProps('checking'), { variant: 'default', label: 'Checking…' })
})

test('an unrecognised state falls back to unknown, never throws', () => {
  assert.deepEqual(statusBadgeProps('bogus'), { variant: 'default', label: 'Not tested' })
  assert.deepEqual(statusBadgeProps(undefined), { variant: 'default', label: 'Not tested' })
})
