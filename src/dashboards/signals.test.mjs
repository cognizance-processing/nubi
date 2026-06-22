/**
 * signals.test.mjs — Unit tests for src/viz/signals.js
 *
 * Run with:
 *   npm run test:dash
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { applySignal, SEVERITY_COLORS, DEFAULT_SIGNAL } from '../../src/viz/signals.js'

// ---------------------------------------------------------------------------
// No rules / empty
// ---------------------------------------------------------------------------

test('applySignal: null rules → DEFAULT_SIGNAL', () => {
  const result = applySignal(100, null)
  assert.deepEqual(result, DEFAULT_SIGNAL)
})

test('applySignal: empty rules array → DEFAULT_SIGNAL', () => {
  const result = applySignal(100, [])
  assert.deepEqual(result, DEFAULT_SIGNAL)
})

test('applySignal: null value → DEFAULT_SIGNAL (no crash)', () => {
  const rules = [{ op: 'gt', value: 0, color: 'red' }]
  const result = applySignal(null, rules)
  assert.deepEqual(result, DEFAULT_SIGNAL)
})

// ---------------------------------------------------------------------------
// matched: true with explicit color
// ---------------------------------------------------------------------------

test('gt rule: value above threshold → matched=true + correct color', () => {
  const rules = [{ op: 'gt', value: 100, color: '#ef4444' }]
  const result = applySignal(200, rules)
  assert.equal(result.matched, true)
  assert.equal(result.color, '#ef4444')
})

test('gt rule: value at threshold → NOT matched', () => {
  const rules = [{ op: 'gt', value: 100, color: '#ef4444' }]
  const result = applySignal(100, rules)
  assert.equal(result.matched, false)
})

test('gte rule: value at threshold → matched', () => {
  const rules = [{ op: 'gte', value: 50, color: '#f59e0b' }]
  const result = applySignal(50, rules)
  assert.equal(result.matched, true)
})

test('lt rule: value below threshold → matched', () => {
  const rules = [{ op: 'lt', value: 10, color: '#3b82f6' }]
  const result = applySignal(5, rules)
  assert.equal(result.matched, true)
  assert.equal(result.color, '#3b82f6')
})

test('lte rule: value at threshold → matched', () => {
  const rules = [{ op: 'lte', value: 10, color: '#3b82f6' }]
  const result = applySignal(10, rules)
  assert.equal(result.matched, true)
})

test('eq rule: exact match → matched', () => {
  const rules = [{ op: 'eq', value: 'error', color: '#ef4444' }]
  const result = applySignal('error', rules)
  assert.equal(result.matched, true)
})

test('ne rule: values differ → matched', () => {
  const rules = [{ op: 'ne', value: 'ok', color: '#f59e0b' }]
  const result = applySignal('warning', rules)
  assert.equal(result.matched, true)
})

test('between rule: value in range → matched', () => {
  const rules = [{ op: 'between', value: 50, value2: 80, color: '#f59e0b', label: 'Amber' }]
  const result = applySignal(65, rules)
  assert.equal(result.matched, true)
  assert.equal(result.color, '#f59e0b')
  assert.equal(result.label, 'Amber')
})

test('between rule: value out of range → not matched', () => {
  const rules = [{ op: 'between', value: 50, value2: 80, color: '#f59e0b' }]
  const result = applySignal(90, rules)
  assert.equal(result.matched, false)
})

test('contains rule: substring match (case-insensitive) → matched', () => {
  const rules = [{ op: 'contains', value: 'ERROR', color: '#ef4444' }]
  const result = applySignal('network error', rules)
  assert.equal(result.matched, true)
})

// ---------------------------------------------------------------------------
// severity fallback (no explicit color)
// ---------------------------------------------------------------------------

test('severity:red → resolves to SEVERITY_COLORS.red when color is absent', () => {
  const rules = [{ op: 'gt', value: 90, severity: 'red' }]
  const result = applySignal(95, rules)
  assert.equal(result.matched, true)
  assert.equal(result.color, SEVERITY_COLORS.red)
  assert.equal(result.severity, 'red')
})

test('severity:amber → resolves to SEVERITY_COLORS.amber', () => {
  const rules = [{ op: 'between', value: 50, value2: 90, severity: 'amber' }]
  const result = applySignal(70, rules)
  assert.equal(result.color, SEVERITY_COLORS.amber)
})

test('severity:green → resolves to SEVERITY_COLORS.green', () => {
  const rules = [{ op: 'lt', value: 50, severity: 'green' }]
  const result = applySignal(20, rules)
  assert.equal(result.color, SEVERITY_COLORS.green)
})

test('explicit color beats severity (color wins when both present)', () => {
  const rules = [{ op: 'gt', value: 0, color: '#123456', severity: 'red' }]
  const result = applySignal(1, rules)
  assert.equal(result.color, '#123456', 'explicit color must win over severity fallback')
})

// ---------------------------------------------------------------------------
// First-match-wins
// ---------------------------------------------------------------------------

test('first matching rule wins (rules are evaluated in order)', () => {
  const rules = [
    { op: 'gt', value: 90, color: '#ef4444', label: 'Critical' },
    { op: 'gt', value: 70, color: '#f59e0b', label: 'Warning' },
    { op: 'gt', value: 0,  color: '#10b981', label: 'OK' },
  ]
  // 95 matches all three — first rule must win.
  const result = applySignal(95, rules)
  assert.equal(result.label, 'Critical', 'first matching rule must win')
  assert.equal(result.color, '#ef4444')
})

test('second rule matches when first does not', () => {
  const rules = [
    { op: 'gt', value: 90, color: '#ef4444', label: 'Critical' },
    { op: 'gt', value: 70, color: '#f59e0b', label: 'Warning' },
  ]
  const result = applySignal(80, rules)
  assert.equal(result.label, 'Warning', 'second rule must fire when first does not match')
})

// ---------------------------------------------------------------------------
// label and severity fields in result
// ---------------------------------------------------------------------------

test('result includes label from matched rule', () => {
  const rules = [{ op: 'gt', value: 100, color: '#10b981', label: 'Healthy' }]
  const result = applySignal(200, rules)
  assert.equal(result.label, 'Healthy')
})

test('result label is null when rule has no label', () => {
  const rules = [{ op: 'gt', value: 0, color: '#10b981' }]
  const result = applySignal(5, rules)
  assert.equal(result.label, null)
})

test('result severity is null when rule has no severity', () => {
  const rules = [{ op: 'gt', value: 0, color: '#10b981' }]
  const result = applySignal(5, rules)
  assert.equal(result.severity, null)
})

// ---------------------------------------------------------------------------
// SEVERITY_COLORS export
// ---------------------------------------------------------------------------

test('SEVERITY_COLORS exports expected keys', () => {
  for (const key of ['red', 'amber', 'green', 'info', 'neutral']) {
    assert.ok(key in SEVERITY_COLORS, `SEVERITY_COLORS must have key "${key}"`)
    assert.equal(typeof SEVERITY_COLORS[key], 'string', `SEVERITY_COLORS.${key} must be a string`)
  }
})
