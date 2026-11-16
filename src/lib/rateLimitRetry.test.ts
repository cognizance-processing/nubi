import test from 'node:test'
import assert from 'node:assert/strict'
import { fetchRetryingRateLimit, retryDelayMs } from './rateLimitRetry.js'

const reply = (status: number, retryAfter?: string) =>
  new Response(null, { status, headers: retryAfter ? { 'Retry-After': retryAfter } : {} })

const noJitter = () => 0

test('retryDelayMs follows Retry-After, backs off without it, and caps the wait', () => {
  assert.equal(retryDelayMs('2', 0), 2000)
  assert.equal(retryDelayMs('0', 3), 0)
  assert.equal(retryDelayMs(null, 0), 1000)
  assert.equal(retryDelayMs('soon', 2), 4000)
  assert.equal(retryDelayMs('600', 0), 10_000)
})

test('a rate limited request is retried after the advertised wait', async () => {
  const replies = [reply(429, '2'), reply(200)]
  const waits: number[] = []
  const response = await fetchRetryingRateLimit(
    async () => replies.shift()!,
    async (ms) => { waits.push(ms) },
    noJitter,
  )
  assert.equal(response.status, 200)
  assert.deepEqual(waits, [2000])
})

test('other responses are returned without a retry', async () => {
  for (const status of [200, 400, 500]) {
    let calls = 0
    const response = await fetchRetryingRateLimit(async () => { calls++; return reply(status) }, async () => {}, noJitter)
    assert.equal(response.status, status)
    assert.equal(calls, 1)
  }
})

test('gives up after five attempts and returns the last 429', async () => {
  let calls = 0
  const response = await fetchRetryingRateLimit(async () => { calls++; return reply(429, '1') }, async () => {}, noJitter)
  assert.equal(response.status, 429)
  assert.equal(calls, 5)
})
