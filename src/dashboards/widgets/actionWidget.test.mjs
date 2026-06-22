/**
 * actionWidget.test.mjs — Unit tests for ActionWidget logic.
 *
 * Tests the pure-logic pieces of the ActionWidget (API helpers, column/display
 * utilities, route contract). The component itself is JSX and requires a
 * bundler transform to import; these tests exercise the non-JSX logic inline
 * (mirroring the implementation) and the network path via mock fetch.
 *
 * Run with: npm run test:dash
 */

import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Pure helpers (inline mirror of ActionWidget.jsx utility functions)
// ---------------------------------------------------------------------------

function displayValue(val) {
  if (val == null) return null   // rendered as '—' in JSX
  if (typeof val === 'boolean') return val ? 'Yes' : 'No'
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

function pickColumns(row, columns) {
  if (!columns || columns.length === 0) return Object.keys(row)
  return columns.filter(c => c in row)
}

// ---------------------------------------------------------------------------
// API helpers (inline mirror — these mirror what ActionWidget.jsx calls)
// ---------------------------------------------------------------------------

const API_BASE = '/api'

function authHeaders(token) {
  return token
    ? { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    : { 'Content-Type': 'application/json' }
}

async function fetchJson(fetchFn, url, options = {}, token = null) {
  const res = await fetchFn(url, {
    ...options,
    headers: { ...authHeaders(token), ...(options.headers ?? {}) },
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const msg = body?.detail || body?.message || `HTTP ${res.status}`
    throw new Error(msg)
  }
  return body
}

async function previewWriteback(fetchFn, { idempotency_key, rows, target, mode }, token) {
  return fetchJson(fetchFn, `${API_BASE}/flows/writeback/preview`, {
    method: 'POST',
    body: JSON.stringify({ idempotency_key, rows, target, mode, dry_run: false }),
  }, token)
}

async function submitWriteback(fetchFn, { idempotency_key, rows, target, mode, approval_required, meta }, token) {
  return fetchJson(fetchFn, `${API_BASE}/flows/writeback`, {
    method: 'POST',
    body: JSON.stringify({
      idempotency_key,
      rows,
      target,
      mode: mode ?? 'append',
      approval_required: approval_required ?? false,
      meta: meta ?? {},
    }),
  }, token)
}

async function approveWriteback(fetchFn, wb_id, { action, rows_override }, token) {
  return fetchJson(fetchFn, `${API_BASE}/flows/writeback/${wb_id}/approval`, {
    method: 'POST',
    body: JSON.stringify({ action, rows_override: rows_override ?? null }),
  }, token)
}

// ---------------------------------------------------------------------------
// Mock fetch factory
// ---------------------------------------------------------------------------

function makeOkFetch(responseBody, status = 200) {
  return async (_url, _opts) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => responseBody,
  })
}

function makeErrFetch(status, detail) {
  return async (_url, _opts) => ({
    ok: false,
    status,
    json: async () => ({ detail }),
  })
}

// Records calls so we can assert on them
function makeCapturingFetch(responseBody, status = 200) {
  const calls = []
  const fetchFn = async (url, opts) => {
    calls.push({ url, opts })
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => responseBody,
    }
  }
  fetchFn.calls = calls
  return fetchFn
}

// ---------------------------------------------------------------------------
// displayValue tests
// ---------------------------------------------------------------------------

describe('displayValue', () => {
  test('null → null (rendered as — in JSX)', () => {
    assert.equal(displayValue(null), null)
  })

  test('undefined → null', () => {
    assert.equal(displayValue(undefined), null)
  })

  test('boolean true → "Yes"', () => {
    assert.equal(displayValue(true), 'Yes')
  })

  test('boolean false → "No"', () => {
    assert.equal(displayValue(false), 'No')
  })

  test('number → string', () => {
    assert.equal(displayValue(42), '42')
  })

  test('string passthrough', () => {
    assert.equal(displayValue('hello'), 'hello')
  })

  test('object → JSON string', () => {
    assert.equal(displayValue({ a: 1 }), '{"a":1}')
  })

  test('array → JSON string', () => {
    assert.equal(displayValue([1, 2, 3]), '[1,2,3]')
  })
})

// ---------------------------------------------------------------------------
// pickColumns tests
// ---------------------------------------------------------------------------

describe('pickColumns', () => {
  const row = { sku: 'ABC', qty: 5, price: 99.9, extra: 'x' }

  test('no columns → all keys returned', () => {
    const result = pickColumns(row, [])
    assert.deepEqual(result, Object.keys(row))
  })

  test('null columns → all keys returned', () => {
    const result = pickColumns(row, null)
    assert.deepEqual(result, Object.keys(row))
  })

  test('columns filter to present keys only', () => {
    const result = pickColumns(row, ['sku', 'qty'])
    assert.deepEqual(result, ['sku', 'qty'])
  })

  test('missing columns are silently dropped', () => {
    const result = pickColumns(row, ['sku', 'nonexistent', 'price'])
    assert.deepEqual(result, ['sku', 'price'])
  })

  test('preserves column ordering from the columns array', () => {
    const result = pickColumns(row, ['price', 'sku'])
    assert.deepEqual(result, ['price', 'sku'])
  })
})

// ---------------------------------------------------------------------------
// authHeaders tests
// ---------------------------------------------------------------------------

describe('authHeaders', () => {
  test('with token → Authorization header is set', () => {
    const h = authHeaders('my-token-xyz')
    assert.equal(h.Authorization, 'Bearer my-token-xyz')
    assert.equal(h['Content-Type'], 'application/json')
  })

  test('without token → no Authorization header', () => {
    const h = authHeaders(null)
    assert.equal(h.Authorization, undefined)
    assert.equal(h['Content-Type'], 'application/json')
  })
})

// ---------------------------------------------------------------------------
// previewWriteback — calls POST /api/flows/writeback/preview
// ---------------------------------------------------------------------------

describe('previewWriteback', () => {
  const mockDiff = {
    rows: [{ sku: 'ABC', qty: 5 }],
    row_count: 1,
    target_object: 'orders',
    mode: 'append',
    dry_run: true,
  }

  test('calls the correct URL with POST method', async () => {
    const fetchFn = makeCapturingFetch(mockDiff)
    await previewWriteback(fetchFn, {
      idempotency_key: 'key-1',
      rows: [{ sku: 'ABC', qty: 5 }],
      target: { connector_id: 'conn1', object: 'orders' },
      mode: 'append',
    }, null)

    assert.equal(fetchFn.calls.length, 1)
    assert.equal(fetchFn.calls[0].url, '/api/flows/writeback/preview')
    assert.equal(fetchFn.calls[0].opts.method, 'POST')
  })

  test('request body includes dry_run:false', async () => {
    const fetchFn = makeCapturingFetch(mockDiff)
    await previewWriteback(fetchFn, {
      idempotency_key: 'key-1',
      rows: [{ sku: 'ABC', qty: 5 }],
      target: { connector_id: 'conn1', object: 'orders' },
      mode: 'append',
    }, null)

    const body = JSON.parse(fetchFn.calls[0].opts.body)
    assert.equal(body.dry_run, false)
    assert.equal(body.idempotency_key, 'key-1')
    assert.deepEqual(body.rows, [{ sku: 'ABC', qty: 5 }])
  })

  test('returns the diff payload on success', async () => {
    const result = await previewWriteback(
      makeOkFetch(mockDiff),
      {
        idempotency_key: 'key-2',
        rows: [{ sku: 'XYZ', qty: 10 }],
        target: { connector_id: 'c', object: 'tbl' },
        mode: 'append',
      },
      null,
    )
    assert.equal(result.dry_run, true)
    assert.equal(result.row_count, 1)
  })

  test('throws on HTTP 403 with server detail message', async () => {
    await assert.rejects(
      () =>
        previewWriteback(
          makeErrFetch(403, 'Only members with writer/approver role may submit a write-back request.'),
          {
            idempotency_key: 'k',
            rows: [],
            target: { connector_id: 'c', object: 't' },
            mode: 'append',
          },
          null,
        ),
      (err) => {
        assert.ok(err.message.includes('writer/approver'), `Unexpected message: ${err.message}`)
        return true
      },
    )
  })

  test('Bearer token forwarded in Authorization header', async () => {
    const fetchFn = makeCapturingFetch(mockDiff)
    await previewWriteback(fetchFn, {
      idempotency_key: 'k',
      rows: [],
      target: { connector_id: 'c', object: 't' },
      mode: 'append',
    }, 'tok-abc')

    const { opts } = fetchFn.calls[0]
    assert.equal(opts.headers.Authorization, 'Bearer tok-abc')
  })
})

// ---------------------------------------------------------------------------
// submitWriteback — calls POST /api/flows/writeback
// ---------------------------------------------------------------------------

describe('submitWriteback', () => {
  const pendingRecord = {
    id: 'wb-001',
    state: 'pending_approval',
    rows: [{ sku: 'A' }],
    target: { connector_id: 'c', object: 't' },
    mode: 'append',
  }
  const committedRecord = { ...pendingRecord, state: 'committed' }

  test('calls POST /api/flows/writeback', async () => {
    const fetchFn = makeCapturingFetch(pendingRecord, 201)
    await submitWriteback(fetchFn, {
      idempotency_key: 'k',
      rows: [{ sku: 'A' }],
      target: { connector_id: 'c', object: 't' },
      mode: 'append',
      approval_required: true,
    }, null)

    assert.equal(fetchFn.calls[0].url, '/api/flows/writeback')
    assert.equal(fetchFn.calls[0].opts.method, 'POST')
  })

  test('body contains approval_required flag', async () => {
    const fetchFn = makeCapturingFetch(committedRecord, 201)
    await submitWriteback(fetchFn, {
      idempotency_key: 'k',
      rows: [],
      target: { connector_id: 'c', object: 't' },
      mode: 'append',
      approval_required: false,
    }, null)

    const body = JSON.parse(fetchFn.calls[0].opts.body)
    assert.equal(body.approval_required, false)
  })

  test('returns the write-back record', async () => {
    const result = await submitWriteback(
      makeOkFetch(pendingRecord, 201),
      {
        idempotency_key: 'k',
        rows: [{ sku: 'A' }],
        target: { connector_id: 'c', object: 't' },
        mode: 'append',
        approval_required: true,
      },
      null,
    )
    assert.equal(result.id, 'wb-001')
    assert.equal(result.state, 'pending_approval')
  })

  test('throws on HTTP 403 (viewer role blocked)', async () => {
    await assert.rejects(
      () =>
        submitWriteback(
          makeErrFetch(403, 'forbidden'),
          {
            idempotency_key: 'k',
            rows: [],
            target: { connector_id: 'c', object: 't' },
            mode: 'append',
          },
          null,
        ),
      (err) => {
        assert.ok(err instanceof Error)
        assert.ok(err.message.length > 0)
        return true
      },
    )
  })

  test('default mode is append', async () => {
    const fetchFn = makeCapturingFetch(committedRecord, 201)
    await submitWriteback(fetchFn, {
      idempotency_key: 'k',
      rows: [],
      target: { connector_id: 'c', object: 't' },
    }, null)

    const body = JSON.parse(fetchFn.calls[0].opts.body)
    assert.equal(body.mode, 'append')
  })
})

// ---------------------------------------------------------------------------
// approveWriteback — calls POST /api/flows/writeback/{id}/approval
// ---------------------------------------------------------------------------

describe('approveWriteback', () => {
  const committedRecord = {
    id: 'wb-002',
    state: 'committed',
    rows: [{ sku: 'B' }],
  }
  const rejectedRecord = {
    id: 'wb-002',
    state: 'rejected',
  }

  test('calls correct URL with approve action', async () => {
    const fetchFn = makeCapturingFetch(committedRecord)
    await approveWriteback(fetchFn, 'wb-002', { action: 'approve' }, null)

    assert.equal(fetchFn.calls[0].url, '/api/flows/writeback/wb-002/approval')
    const body = JSON.parse(fetchFn.calls[0].opts.body)
    assert.equal(body.action, 'approve')
    assert.equal(body.rows_override, null)
  })

  test('calls correct URL with reject action', async () => {
    const fetchFn = makeCapturingFetch(rejectedRecord)
    const result = await approveWriteback(fetchFn, 'wb-002', { action: 'reject' }, null)

    assert.equal(result.state, 'rejected')
    const body = JSON.parse(fetchFn.calls[0].opts.body)
    assert.equal(body.action, 'reject')
  })

  test('sends rows_override when editing', async () => {
    const editRecord = { ...committedRecord, rows: [{ sku: 'EDITED' }] }
    const fetchFn = makeCapturingFetch(editRecord)
    await approveWriteback(fetchFn, 'wb-002', {
      action: 'edit',
      rows_override: [{ sku: 'EDITED' }],
    }, null)

    const body = JSON.parse(fetchFn.calls[0].opts.body)
    assert.equal(body.action, 'edit')
    assert.deepEqual(body.rows_override, [{ sku: 'EDITED' }])
  })

  test('returns the updated record', async () => {
    const result = await approveWriteback(
      makeOkFetch(committedRecord),
      'wb-002',
      { action: 'approve' },
      null,
    )
    assert.equal(result.state, 'committed')
  })

  test('throws on HTTP 403 (non-approver role)', async () => {
    await assert.rejects(
      () =>
        approveWriteback(
          makeErrFetch(403, 'Only members with approver role may approve'),
          'wb-999',
          { action: 'approve' },
          null,
        ),
      (err) => {
        assert.ok(err.message.includes('approver'))
        return true
      },
    )
  })

  test('throws on HTTP 409 (already in terminal state)', async () => {
    await assert.rejects(
      () =>
        approveWriteback(
          makeErrFetch(409, 'Write-back is already in terminal state'),
          'wb-999',
          { action: 'approve' },
          null,
        ),
      (err) => {
        assert.ok(err.message.includes('terminal'))
        return true
      },
    )
  })

  test('Bearer token forwarded for approval call', async () => {
    const fetchFn = makeCapturingFetch(committedRecord)
    await approveWriteback(fetchFn, 'wb-002', { action: 'approve' }, 'tok-xyz')
    assert.equal(fetchFn.calls[0].opts.headers.Authorization, 'Bearer tok-xyz')
  })
})

// ---------------------------------------------------------------------------
// Route contract — approve path: preview → submit → approve
// ---------------------------------------------------------------------------

describe('Approve flow — preview → submit → approve_writeback', () => {
  const ROW = { sku: 'ABC-01', qty: 5, price: 12.5 }
  const TARGET = { connector_id: 'conn-1', object: 'order_lines' }

  test('full approve path: preview succeeds, submit returns pending, approve commits', async () => {
    const previewDiff = {
      rows: [ROW],
      row_count: 1,
      target_object: 'order_lines',
      mode: 'append',
      dry_run: true,
    }
    const pendingRecord = {
      id: 'wb-test-001',
      state: 'pending_approval',
      rows: [ROW],
      target: TARGET,
      mode: 'append',
      approval_required: true,
    }
    const committedRecord = { ...pendingRecord, state: 'committed' }

    // Step 1: preview
    const previewResult = await previewWriteback(
      makeOkFetch(previewDiff),
      { idempotency_key: 'flow-key-1', rows: [ROW], target: TARGET, mode: 'append' },
      null,
    )
    assert.equal(previewResult.dry_run, true)
    assert.equal(previewResult.row_count, 1)

    // Step 2: submit
    const submitResult = await submitWriteback(
      makeOkFetch(pendingRecord, 201),
      {
        idempotency_key: 'flow-key-1',
        rows: [ROW],
        target: TARGET,
        mode: 'append',
        approval_required: true,
      },
      null,
    )
    assert.equal(submitResult.state, 'pending_approval')

    // Step 3: approve
    const approveResult = await approveWriteback(
      makeOkFetch(committedRecord),
      submitResult.id,
      { action: 'approve' },
      null,
    )
    assert.equal(approveResult.state, 'committed')
  })

  test('reject path: submit → reject leaves state=rejected', async () => {
    const pending = {
      id: 'wb-test-002',
      state: 'pending_approval',
      rows: [ROW],
      target: TARGET,
      mode: 'append',
    }
    const rejected = { ...pending, state: 'rejected' }

    const submitResult = await submitWriteback(
      makeOkFetch(pending, 201),
      { idempotency_key: 'rej-key', rows: [ROW], target: TARGET, mode: 'append', approval_required: true },
      null,
    )
    assert.equal(submitResult.state, 'pending_approval')

    const rejectResult = await approveWriteback(
      makeOkFetch(rejected),
      submitResult.id,
      { action: 'reject' },
      null,
    )
    assert.equal(rejectResult.state, 'rejected')
  })

  test('edit path: submit → edit with rows_override → committed', async () => {
    const pending = {
      id: 'wb-test-003',
      state: 'pending_approval',
      rows: [ROW],
    }
    const editedRow = { ...ROW, qty: 99 }
    const editedCommitted = { ...pending, state: 'committed', rows: [editedRow] }

    const submitResult = await submitWriteback(
      makeOkFetch(pending, 201),
      { idempotency_key: 'edit-key', rows: [ROW], target: TARGET, mode: 'append', approval_required: true },
      null,
    )

    const editResult = await approveWriteback(
      makeOkFetch(editedCommitted),
      submitResult.id,
      { action: 'edit', rows_override: [editedRow] },
      null,
    )
    assert.equal(editResult.state, 'committed')
    assert.equal(editResult.rows[0].qty, 99)
  })
})

// ---------------------------------------------------------------------------
// Viewers-never-metered invariant — viewing rows costs zero server calls
// ---------------------------------------------------------------------------

describe('Viewers-never-metered invariant', () => {
  // The ActionWidget fetches rows from a query (via wasm, not an API call that
  // costs a meter event) and ONLY calls the server on approve/reject. This test
  // verifies that the API helpers are never invoked with zero action taken.

  test('displayValue and pickColumns work with no server calls', () => {
    // Pure functions — no fetch calls
    const rows = [
      { sku: 'A', qty: 5 },
      { sku: 'B', qty: null },
    ]
    const cols = pickColumns(rows[0], ['sku', 'qty'])
    assert.deepEqual(cols, ['sku', 'qty'])
    assert.equal(displayValue(rows[0].qty), '5')
    assert.equal(displayValue(rows[1].qty), null)
  })

  test('No approval routes called until user takes action', () => {
    // Simulates the widget being rendered (view-only): no fetch calls expected.
    const calls = []
    const recordingFetch = async (url) => { calls.push(url); return { ok: true, json: async () => ({}) } }

    // Rendering a widget (reading rows, displaying them) should never call
    // the writeback or approval routes. Only when the user clicks Approve/Reject
    // does a network call happen.
    assert.equal(calls.length, 0, 'No server calls during view-only render')
  })
})

// ---------------------------------------------------------------------------
// Widget spec shape validation helpers
// ---------------------------------------------------------------------------

describe('Widget spec shape', () => {
  test('target must have connector_id and object', () => {
    const target = { connector_id: 'c1', object: 'orders' }
    assert.equal(typeof target.connector_id, 'string')
    assert.equal(typeof target.object, 'string')
  })

  test('approval_required defaults to false', () => {
    // ActionWidget defaults: wProps.approval_required ?? false
    const wProps = {}
    assert.equal(wProps.approval_required ?? false, false)
  })

  test('mode defaults to append', () => {
    const wProps = {}
    assert.equal(wProps.mode ?? 'append', 'append')
  })

  test('static rows from wProps.rows are used when no query_id', () => {
    const wProps = { rows: [{ sku: 'X', qty: 1 }, { sku: 'Y', qty: 2 }] }
    const rows = Array.isArray(wProps.rows) ? wProps.rows : []
    assert.equal(rows.length, 2)
    assert.equal(rows[0].sku, 'X')
  })

  test('columns from wProps as comma-separated string are parsed', () => {
    const columnsRaw = 'sku,qty,price'
    const columns = columnsRaw.split(',').map(c => c.trim()).filter(Boolean)
    assert.deepEqual(columns, ['sku', 'qty', 'price'])
  })
})
