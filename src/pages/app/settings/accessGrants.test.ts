/**
 * accessGrants.test.mjs — Unit tests for the validateGrant helper.
 *
 * Run with: node --test 'src/pages/app/settings/accessGrants.test.mjs'
 * (Node 18+ built-in test runner; pure JS — no JSX, no React)
 *
 * validateGrant is a pure function exported from AccessGrantsSettings.jsx.
 * Because that file is JSX, we inline the logic here to keep the test
 * dependency-free.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Inline copy of validateGrant (mirrors AccessGrantsSettings.jsx export).
// ---------------------------------------------------------------------------

const VALID_SUBJECT_TYPES = ['user', 'group', 'role']

function validateGrant(grant) {
  if (!VALID_SUBJECT_TYPES.includes(grant?.subject_type)) {
    return { ok: false, error: `subject_type must be one of: ${VALID_SUBJECT_TYPES.join(', ')}.` }
  }
  if (!grant?.subject_id?.trim()) {
    return { ok: false, error: 'subject_id is required.' }
  }
  if (!grant?.dimension?.trim()) {
    return { ok: false, error: 'dimension is required.' }
  }
  if (!grant?.value?.trim()) {
    return { ok: false, error: 'value is required.' }
  }
  return { ok: true, error: null }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('validateGrant — valid grant passes', () => {
  const result = validateGrant({
    subject_type: 'user',
    subject_id: 'user_abc123',
    dimension: 'country',
    value: 'ZA',
  })
  assert.equal(result.ok, true)
  assert.equal(result.error, null)
})

test('validateGrant — valid grant with role type passes', () => {
  const result = validateGrant({
    subject_type: 'role',
    subject_id: 'admin',
    dimension: 'region',
    value: 'africa',
  })
  assert.equal(result.ok, true)
})

test('validateGrant — valid grant with group type passes', () => {
  const result = validateGrant({
    subject_type: 'group',
    subject_id: 'team_data',
    dimension: 'currency',
    value: 'ZAR',
  })
  assert.equal(result.ok, true)
})

test('validateGrant — invalid subject_type fails', () => {
  const result = validateGrant({
    subject_type: 'admin',
    subject_id: 'user_123',
    dimension: 'country',
    value: 'ZA',
  })
  assert.equal(result.ok, false)
  assert.ok(result.error.includes('subject_type'))
})

test('validateGrant — missing subject_type fails', () => {
  const result = validateGrant({
    subject_id: 'user_123',
    dimension: 'country',
    value: 'ZA',
  })
  assert.equal(result.ok, false)
  assert.ok(result.error.length > 0)
})

test('validateGrant — empty subject_id fails', () => {
  const result = validateGrant({
    subject_type: 'user',
    subject_id: '',
    dimension: 'country',
    value: 'ZA',
  })
  assert.equal(result.ok, false)
  assert.ok(result.error.includes('subject_id'))
})

test('validateGrant — whitespace-only subject_id fails', () => {
  const result = validateGrant({
    subject_type: 'user',
    subject_id: '   ',
    dimension: 'country',
    value: 'ZA',
  })
  assert.equal(result.ok, false)
  assert.ok(result.error.includes('subject_id'))
})

test('validateGrant — missing dimension fails', () => {
  const result = validateGrant({
    subject_type: 'user',
    subject_id: 'user_123',
    dimension: '',
    value: 'ZA',
  })
  assert.equal(result.ok, false)
  assert.ok(result.error.includes('dimension'))
})

test('validateGrant — missing value fails', () => {
  const result = validateGrant({
    subject_type: 'user',
    subject_id: 'user_123',
    dimension: 'country',
    value: '',
  })
  assert.equal(result.ok, false)
  assert.ok(result.error.includes('value'))
})

test('validateGrant — null grant fails gracefully', () => {
  const result = validateGrant(null)
  assert.equal(result.ok, false)
  assert.ok(result.error.length > 0)
})
