/**
 * connectorPayload.test.ts — clearing a connector field while editing must
 * reach the server (PUT /connectors merges config, so an omitted key keeps the
 * old value). Run: npm run test:dash
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { connectorConfigPayload } from './connectorPayload.js'

test('create: blank fields are dropped', () => {
  assert.deepEqual(
    connectorConfigPayload({ host: 'h', schema: '', user: undefined }, {}, false),
    { host: 'h' },
  )
})

test('edit: a field the user emptied is sent as null so the server clears it', () => {
  const initial = { host: 'h', schema: 'analytics', user: 'u', connector_type: 'postgres' }
  const form = { ...initial, schema: '', user: 'u2' }
  assert.deepEqual(connectorConfigPayload(form, initial, true), {
    host: 'h', user: 'u2', connector_type: 'postgres', schema: null,
  })
})

test('edit: fields that were already blank are not sent', () => {
  assert.deepEqual(connectorConfigPayload({ host: 'h', schema: '' }, { host: 'h', schema: '' }, true), { host: 'h' })
})
