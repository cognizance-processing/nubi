/**
 * embedLock.test.mjs — Unit tests for decodeJwtPayload helper.
 *
 * Run with: node --test 'src/dashboards/embedLock.test.mjs'
 * (Node 18+ built-in test runner, no JSX needed)
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Inline copy of decodeJwtPayload so this test file has zero imports from JSX.
// The real implementation lives in src/dashboards/embedLock.js.
// ---------------------------------------------------------------------------

function decodeJwtPayload(token) {
  try {
    const parts = token.split('.')
    if (parts.length < 2) return null
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(Buffer.from(b64, 'base64').toString('utf8'))
  } catch {
    return null
  }
}

// ---------------------------------------------------------------------------
// Helpers to build test JWTs
// ---------------------------------------------------------------------------

function b64url(obj) {
  return Buffer.from(JSON.stringify(obj)).toString('base64url')
}

function makeJwt(payload) {
  const header = b64url({ alg: 'HS256', typ: 'JWT' })
  const body = b64url(payload)
  return `${header}.${body}.fakesig`
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('decodeJwtPayload — valid JWT returns parsed payload', () => {
  const payload = { sub: 'org_123', locked_params: { region: 'ZA' } }
  const token = makeJwt(payload)
  const result = decodeJwtPayload(token)
  assert.deepEqual(result, payload)
})

test('decodeJwtPayload — valid JWT with no locked_params returns payload without that field', () => {
  const payload = { sub: 'org_456', exp: 9999999999 }
  const token = makeJwt(payload)
  const result = decodeJwtPayload(token)
  assert.deepEqual(result, payload)
  assert.equal('locked_params' in result, false)
})

test('decodeJwtPayload — invalid string (not a JWT) returns null', () => {
  const result = decodeJwtPayload('not-a-jwt')
  assert.equal(result, null)
})

test('decodeJwtPayload — empty string returns null', () => {
  const result = decodeJwtPayload('')
  assert.equal(result, null)
})

test('decodeJwtPayload — only one segment (no dot) returns null', () => {
  const result = decodeJwtPayload('onlyonepart')
  assert.equal(result, null)
})

test('decodeJwtPayload — non-JSON payload returns null', () => {
  const header = b64url({ alg: 'HS256' })
  const badBody = Buffer.from('not json !!!').toString('base64url')
  const token = `${header}.${badBody}.sig`
  const result = decodeJwtPayload(token)
  assert.equal(result, null)
})

test('decodeJwtPayload — locked_params values are accessible', () => {
  const locked = { country: 'ZA', currency: 'ZAR' }
  const token = makeJwt({ locked_params: locked })
  const result = decodeJwtPayload(token)
  assert.deepEqual(result.locked_params, locked)
})
