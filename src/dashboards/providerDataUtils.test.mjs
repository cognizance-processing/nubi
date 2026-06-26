/**
 * providerDataUtils.test.mjs — Unit tests for decodeMultiTableIPC().
 *
 * Pure module (apache-arrow only) — runs with bare `node --test`.
 *
 *   node --test src/dashboards/providerDataUtils.test.mjs
 *   # or via the project script:
 *   npm run test:dash
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import * as arrow from 'apache-arrow'

import { decodeMultiTableIPC } from './providerDataUtils.js'

// ---------------------------------------------------------------------------
// Frame builder — mirrors the backend's tables_to_multi_ipc_stream format.
// ---------------------------------------------------------------------------

/**
 * Build a multi-table IPC frame from { name, ipcBytes } entries.
 * Frame: [u32 count][ (u32 nameLen)(name)(u32 ipcLen)(ipc) ... ]  (big-endian)
 */
function buildFrame(entries) {
  let totalLen = 4
  const encoded = entries.map(({ name, ipcBytes }) => {
    const nameBytes = new TextEncoder().encode(name)
    totalLen += 4 + nameBytes.length + 4 + ipcBytes.length
    return { nameBytes, ipcBytes }
  })

  const buf = new ArrayBuffer(totalLen)
  const view = new DataView(buf)
  let offset = 0
  view.setUint32(offset, entries.length, false)
  offset += 4
  for (const { nameBytes, ipcBytes } of encoded) {
    view.setUint32(offset, nameBytes.length, false)
    offset += 4
    new Uint8Array(buf, offset, nameBytes.length).set(nameBytes)
    offset += nameBytes.length
    view.setUint32(offset, ipcBytes.length, false)
    offset += 4
    new Uint8Array(buf, offset, ipcBytes.length).set(ipcBytes)
    offset += ipcBytes.length
  }
  return buf
}

function ipcOf(columns) {
  return arrow.tableToIPC(arrow.tableFromArrays(columns), 'stream')
}

// ---------------------------------------------------------------------------
// Empty frame
// ---------------------------------------------------------------------------

test('zero-table frame decodes to empty object', () => {
  const buf = buildFrame([])
  const tables = decodeMultiTableIPC(buf)
  assert.deepEqual(Object.keys(tables), [])
})

// ---------------------------------------------------------------------------
// Single table
// ---------------------------------------------------------------------------

test('single-table frame decodes by name with correct rows', () => {
  const buf = buildFrame([
    { name: 'sales', ipcBytes: ipcOf({ revenue: arrow.vectorFromArray([100, 200], new arrow.Int32()) }) },
  ])
  const tables = decodeMultiTableIPC(buf)
  assert.deepEqual(Object.keys(tables), ['sales'])
  assert.equal(tables.sales.numRows, 2)
  assert.equal(tables.sales.getChild('revenue').get(0), 100)
  assert.equal(tables.sales.getChild('revenue').get(1), 200)
})

// ---------------------------------------------------------------------------
// Multiple tables
// ---------------------------------------------------------------------------

test('multi-table frame decodes all slices keyed by name', () => {
  const buf = buildFrame([
    { name: 'sales', ipcBytes: ipcOf({ revenue: arrow.vectorFromArray([100], new arrow.Int32()) }) },
    { name: 'summary', ipcBytes: ipcOf({ count: arrow.vectorFromArray([5, 6, 7], new arrow.Int32()) }) },
  ])
  const tables = decodeMultiTableIPC(buf)
  assert.deepEqual(Object.keys(tables).sort(), ['sales', 'summary'])
  assert.equal(tables.sales.numRows, 1)
  assert.equal(tables.summary.numRows, 3)
})

test('decoded tables preserve column schema', () => {
  const buf = buildFrame([
    {
      name: 't',
      ipcBytes: ipcOf({
        a: arrow.vectorFromArray([1], new arrow.Int32()),
        b: arrow.vectorFromArray(['x']),
      }),
    },
  ])
  const tables = decodeMultiTableIPC(buf)
  const names = tables.t.schema.fields.map((f) => f.name)
  assert.deepEqual(names.sort(), ['a', 'b'])
})

// ---------------------------------------------------------------------------
// UTF-8 table names
// ---------------------------------------------------------------------------

test('multi-byte UTF-8 table names round-trip', () => {
  const buf = buildFrame([
    { name: 'véntas', ipcBytes: ipcOf({ x: arrow.vectorFromArray([1], new arrow.Int32()) }) },
  ])
  const tables = decodeMultiTableIPC(buf)
  assert.ok('véntas' in tables)
})

// ---------------------------------------------------------------------------
// Decode failure → empty table fallback (best-effort)
// ---------------------------------------------------------------------------

test('corrupt IPC bytes fall back to an empty table without throwing', () => {
  // Hand-build a frame whose ipc bytes are garbage.
  const name = 'broken'
  const nameBytes = new TextEncoder().encode(name)
  const garbage = new Uint8Array([1, 2, 3, 4, 5])
  const totalLen = 4 + 4 + nameBytes.length + 4 + garbage.length
  const buf = new ArrayBuffer(totalLen)
  const view = new DataView(buf)
  let off = 0
  view.setUint32(off, 1, false); off += 4
  view.setUint32(off, nameBytes.length, false); off += 4
  new Uint8Array(buf, off, nameBytes.length).set(nameBytes); off += nameBytes.length
  view.setUint32(off, garbage.length, false); off += 4
  new Uint8Array(buf, off, garbage.length).set(garbage)

  let tables
  assert.doesNotThrow(() => { tables = decodeMultiTableIPC(buf) })
  assert.ok('broken' in tables)
  assert.equal(tables.broken.numRows, 0)
})
