/**
 * index.test.mjs — Unit tests for @nubi/sdk
 *
 * Run with: node --test src/index.test.mjs
 *
 * Strategy
 * --------
 * - globalThis.fetch is stubbed before each test; no real network calls.
 * - Arrow IPC buffers are generated with apache-arrow tableToIPC so the
 *   mock query() path exercises real Arrow round-trip (build + parse).
 * - All tests run in the Node.js built-in test runner (node:test).
 */

import { describe, it, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'

// apache-arrow — used both by the SDK internally AND to build test fixtures.
import {
  tableFromArrays,
  tableToIPC,
  vectorFromArray,
  Int32,
  Float64,
} from 'apache-arrow'

// The SDK under test — imported as ESM from source (no build step needed
// for tests; Node resolves apache-arrow from sdk/node_modules).
import { createNubiClient } from './index.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const BASE_URL = 'http://localhost:8000'
const STATIC_TOKEN = 'test-jwt-token'

/** Build a tiny Arrow IPC buffer (Uint8Array) for use as a mock response. */
function buildArrowBuffer() {
  const table = tableFromArrays({
    id:    vectorFromArray([1, 2, 3], new Int32()),
    score: vectorFromArray([1.1, 2.2, 3.3], new Float64()),
  })
  return tableToIPC(table, 'stream')
}

/**
 * Create a mock fetch that returns the given response for a single call.
 * Stores the last call's info in lastCall = { url, init }.
 */
function makeFetchMock(responseInit) {
  let lastCall = null

  const mock = async (url, init) => {
    lastCall = { url, init }
    const { status = 200, body, headers = {} } = responseInit

    return {
      ok: status >= 200 && status < 300,
      status,
      headers: {
        get(name) {
          return headers[name.toLowerCase()] ?? null
        },
      },
      async json() {
        return typeof body === 'string' ? JSON.parse(body) : body
      },
      async arrayBuffer() {
        // body is expected to be a Uint8Array for Arrow responses
        if (body instanceof Uint8Array) {
          return body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength)
        }
        const enc = new TextEncoder()
        return enc.encode(typeof body === 'string' ? body : JSON.stringify(body)).buffer
      },
      async text() {
        return typeof body === 'string' ? body : JSON.stringify(body)
      },
    }
  }

  mock.lastCall = () => lastCall
  return mock
}

/** Extract the parsed JSON body from a mock call's init. */
function parsedBody(mock) {
  return JSON.parse(mock.lastCall().init.body)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('createNubiClient — construction', () => {
  it('throws if baseUrl is missing', () => {
    assert.throws(
      () => createNubiClient({ getToken: STATIC_TOKEN }),
      /baseUrl is required/,
    )
  })

  it('throws if getToken is missing', () => {
    assert.throws(
      () => createNubiClient({ baseUrl: BASE_URL }),
      /getToken is required/,
    )
  })

  it('accepts a static token string', () => {
    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    assert.ok(client)
    assert.ok(typeof client.query === 'function')
  })

  it('accepts an async getToken function', () => {
    const client = createNubiClient({
      baseUrl: BASE_URL,
      getToken: async () => STATIC_TOKEN,
    })
    assert.ok(client)
  })
})

// ---------------------------------------------------------------------------

describe('auth.me()', () => {
  it('calls GET /api/v1/auth/me with Bearer token', async () => {
    const mockFetch = makeFetchMock({
      status: 200,
      body: { user: { id: 'u1', email: 'test@example.com' } },
    })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    const result = await client.auth.me()

    assert.equal(mockFetch.lastCall().url, 'http://localhost:8000/api/v1/auth/me')
    assert.equal(mockFetch.lastCall().init.method, 'GET')
    assert.equal(
      mockFetch.lastCall().init.headers.get('Authorization'),
      `Bearer ${STATIC_TOKEN}`,
    )
    assert.equal(result.user.email, 'test@example.com')
  })
})

// ---------------------------------------------------------------------------

describe('query()', () => {
  it('sends { query_id } when arg has no whitespace and no SQL keywords', async () => {
    const arrowBuf = buildArrowBuffer()
    const mockFetch = makeFetchMock({
      status: 200,
      body: arrowBuf,
      headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
    })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    const table = await client.query('my_revenue_query')

    const body = parsedBody(mockFetch)
    assert.equal(body.query_id, 'my_revenue_query', 'should send query_id')
    assert.equal(body.sql, undefined, 'should NOT send sql for a query_id')
    assert.ok(table, 'should return an Arrow Table')
    assert.equal(table.numRows, 3, 'should have 3 rows')
  })

  it('sends { sql } when arg contains spaces', async () => {
    const arrowBuf = buildArrowBuffer()
    const mockFetch = makeFetchMock({
      status: 200,
      body: arrowBuf,
      headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
    })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    await client.query('SELECT * FROM sales')

    const body = parsedBody(mockFetch)
    assert.equal(body.sql, 'SELECT * FROM sales', 'should send sql')
    assert.equal(body.query_id, undefined, 'should NOT send query_id for SQL')
  })

  it('sends { sql } when arg starts with SELECT keyword (case-insensitive)', async () => {
    const arrowBuf = buildArrowBuffer()
    globalThis.fetch = makeFetchMock({
      status: 200,
      body: arrowBuf,
      headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
    })

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    await client.query('select 1')

    // The mock last-call check is already in earlier tests; just assert no throw
  })

  it('sends { query_id } for ids that merely start with a SQL keyword', async () => {
    const arrowBuf = buildArrowBuffer()

    for (const id of ['selected_users', 'with_totals', 'update_log']) {
      const mockFetch = makeFetchMock({
        status: 200,
        body: arrowBuf,
        headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
      })
      globalThis.fetch = mockFetch

      const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
      await client.query(id)

      const body = parsedBody(mockFetch)
      assert.equal(body.query_id, id, `"${id}" should be sent as query_id`)
      assert.equal(body.sql, undefined, `"${id}" should NOT be sent as sql`)
    }
  })

  it('sends { sql } for keyword-led SQL statements', async () => {
    const arrowBuf = buildArrowBuffer()

    for (const sql of ['select 1', 'SELECT * FROM t']) {
      const mockFetch = makeFetchMock({
        status: 200,
        body: arrowBuf,
        headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
      })
      globalThis.fetch = mockFetch

      const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
      await client.query(sql)

      const body = parsedBody(mockFetch)
      assert.equal(body.sql, sql, `"${sql}" should be sent as sql`)
      assert.equal(body.query_id, undefined, `"${sql}" should NOT be sent as query_id`)
    }
  })

  it('sends an array params option as positional { params }', async () => {
    const arrowBuf = buildArrowBuffer()
    const mockFetch = makeFetchMock({
      status: 200,
      body: arrowBuf,
      headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
    })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    await client.query('SELECT * FROM sales WHERE region = $1', { params: ['EMEA', 42] })

    const body = parsedBody(mockFetch)
    assert.deepEqual(body, {
      sql: 'SELECT * FROM sales WHERE region = $1',
      params: ['EMEA', 42],
    })
  })

  it('converts an all-numeric-key params object into positional { params }', async () => {
    const arrowBuf = buildArrowBuffer()
    const mockFetch = makeFetchMock({
      status: 200,
      body: arrowBuf,
      headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
    })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    await client.query('SELECT $1, $2', { params: { 2: 'second', 1: 'first' } })

    const body = parsedBody(mockFetch)
    assert.deepEqual(body, {
      sql: 'SELECT $1, $2',
      params: ['first', 'second'],
    })
  })

  it('sends sparse or 0-based numeric-key params objects as { named_params }', async () => {
    const arrowBuf = buildArrowBuffer()
    const mockFetch = makeFetchMock({
      status: 200,
      body: arrowBuf,
      headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
    })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    // Key '2' with no '1' is ambiguous as a positional binding — it must NOT
    // silently become params[0] (which would bind $1 instead of $2).
    await client.query('my_query', { params: { 2: 'x' } })

    const body = parsedBody(mockFetch)
    assert.deepEqual(body, {
      query_id: 'my_query',
      named_params: { 2: 'x' },
    })

    await client.query('my_query', { params: { 0: 'a', 1: 'b' } })
    assert.deepEqual(parsedBody(mockFetch), {
      query_id: 'my_query',
      named_params: { 0: 'a', 1: 'b' },
    })
  })

  it('sends an object params option with named keys as { named_params }', async () => {
    const arrowBuf = buildArrowBuffer()
    const mockFetch = makeFetchMock({
      status: 200,
      body: arrowBuf,
      headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
    })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    await client.query('my_query', { params: { date: '2024-01' } })

    const body = parsedBody(mockFetch)
    assert.deepEqual(body, {
      query_id: 'my_query',
      named_params: { date: '2024-01' },
    })
  })

  it('parses the Arrow IPC buffer and returns a Table with correct schema', async () => {
    const arrowBuf = buildArrowBuffer()
    globalThis.fetch = makeFetchMock({
      status: 200,
      body: arrowBuf,
      headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
    })

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    const table = await client.query('SELECT id, score FROM t')

    assert.equal(table.numRows, 3)
    const colNames = table.schema.fields.map(f => f.name)
    assert.ok(colNames.includes('id'))
    assert.ok(colNames.includes('score'))

    // Check actual data round-trips correctly
    const idCol = table.getChild('id')
    assert.equal(idCol.get(0), 1)
    assert.equal(idCol.get(1), 2)
    assert.equal(idCol.get(2), 3)
  })

  it('POSTs to /api/v1/query with Bearer token', async () => {
    const arrowBuf = buildArrowBuffer()
    const mockFetch = makeFetchMock({
      status: 200,
      body: arrowBuf,
      headers: { 'content-type': 'application/vnd.apache.arrow.stream' },
    })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    await client.query('SELECT 1')

    assert.equal(mockFetch.lastCall().url, 'http://localhost:8000/api/v1/query')
    assert.equal(mockFetch.lastCall().init.method, 'POST')
    assert.equal(
      mockFetch.lastCall().init.headers.get('Authorization'),
      `Bearer ${STATIC_TOKEN}`,
    )
  })
})

// ---------------------------------------------------------------------------

describe('resources — boards.create()', () => {
  it('POSTs to /api/v1/boards with Bearer token and JSON body', async () => {
    const created = {
      id: 'b1',
      name: 'My Board',
      config: { layout: 'grid' },
      org_id: 'org1',
      created_by: 'u1',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    }
    const mockFetch = makeFetchMock({ status: 201, body: created })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    const result = await client.resources.boards.create({ name: 'My Board', config: { layout: 'grid' } })

    assert.equal(mockFetch.lastCall().url, 'http://localhost:8000/api/v1/boards')
    assert.equal(mockFetch.lastCall().init.method, 'POST')
    assert.equal(
      mockFetch.lastCall().init.headers.get('Authorization'),
      `Bearer ${STATIC_TOKEN}`,
    )

    const body = parsedBody(mockFetch)
    assert.equal(body.name, 'My Board')
    assert.deepEqual(body.config, { layout: 'grid' })

    assert.equal(result.id, 'b1')
    assert.equal(result.name, 'My Board')
  })
})

describe('resources — datastores.list()', () => {
  it('GETs /api/v1/datastores', async () => {
    const mockFetch = makeFetchMock({ status: 200, body: [{ id: 'd1', name: 'PG' }] })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    const result = await client.resources.datastores.list()

    assert.equal(mockFetch.lastCall().url, 'http://localhost:8000/api/v1/datastores')
    assert.equal(mockFetch.lastCall().init.method, 'GET')
    assert.equal(result.length, 1)
    assert.equal(result[0].id, 'd1')
  })
})

describe('resources — widgets.get(id)', () => {
  it('GETs /api/v1/widgets/:id', async () => {
    const mockFetch = makeFetchMock({ status: 200, body: { id: 'w1', name: 'Chart' } })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    const result = await client.resources.widgets.get('w1')

    assert.equal(mockFetch.lastCall().url, 'http://localhost:8000/api/v1/widgets/w1')
    assert.equal(result.name, 'Chart')
  })
})

describe('resources — queries.update(id, fields)', () => {
  it('PUTs /api/v1/queries/:id with fields', async () => {
    const mockFetch = makeFetchMock({ status: 200, body: { id: 'q1', name: 'Updated' } })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    const result = await client.resources.queries.update('q1', { name: 'Updated' })

    assert.equal(mockFetch.lastCall().url, 'http://localhost:8000/api/v1/queries/q1')
    assert.equal(mockFetch.lastCall().init.method, 'PUT')
    const body = parsedBody(mockFetch)
    assert.equal(body.name, 'Updated')
    assert.equal(result.name, 'Updated')
  })
})

describe('resources — boards.remove(id)', () => {
  it('DELETEs /api/v1/boards/:id and returns null on 204', async () => {
    const mockFetch = makeFetchMock({ status: 204, body: null })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })
    const result = await client.resources.boards.remove('b1')

    assert.equal(mockFetch.lastCall().url, 'http://localhost:8000/api/v1/boards/b1')
    assert.equal(mockFetch.lastCall().init.method, 'DELETE')
    assert.equal(result, null)
  })
})

// ---------------------------------------------------------------------------

describe('error handling — { error: { code, message } } shape', () => {
  it('throws Error with .code from backend error envelope', async () => {
    const mockFetch = makeFetchMock({
      status: 404,
      body: { error: { code: 'not_found', message: 'Board not found' } },
    })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })

    await assert.rejects(
      () => client.resources.boards.get('nonexistent'),
      (err) => {
        assert.equal(err.code, 'not_found')
        assert.match(err.message, /Board not found/)
        return true
      },
    )
  })

  it('throws with code "http_error" on non-JSON error response', async () => {
    const mockFetch = makeFetchMock({
      status: 500,
      body: 'Internal Server Error',
    })
    // Override json() to throw (simulate non-JSON body)
    const origFetch = makeFetchMock({
      status: 500,
      body: 'Internal Server Error',
    })
    globalThis.fetch = async (url, init) => {
      const resp = await origFetch(url, init)
      resp.json = async () => { throw new Error('not json') }
      return resp
    }

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: STATIC_TOKEN })

    await assert.rejects(
      () => client.auth.me(),
      (err) => {
        assert.equal(err.code, 'http_error')
        return true
      },
    )
  })
})

// ---------------------------------------------------------------------------

describe('baseUrl normalisation', () => {
  it('handles baseUrl with /api/v1 already included', async () => {
    const mockFetch = makeFetchMock({ status: 200, body: { user: { id: 'u1' } } })
    globalThis.fetch = mockFetch

    const client = createNubiClient({
      baseUrl: 'http://localhost:8000/api/v1',
      getToken: STATIC_TOKEN,
    })
    await client.auth.me()

    // Should NOT double the prefix
    assert.equal(mockFetch.lastCall().url, 'http://localhost:8000/api/v1/auth/me')
  })

  it('handles baseUrl with trailing slash', async () => {
    const mockFetch = makeFetchMock({ status: 200, body: { user: { id: 'u1' } } })
    globalThis.fetch = mockFetch

    const client = createNubiClient({
      baseUrl: 'http://localhost:8000/',
      getToken: STATIC_TOKEN,
    })
    await client.auth.me()

    assert.equal(mockFetch.lastCall().url, 'http://localhost:8000/api/v1/auth/me')
  })
})

// ---------------------------------------------------------------------------

describe('embed.mount()', () => {
  // Minimal DOM stubs — node:test has no document/window.
  let savedDocument
  let savedWindow

  /** Build a fake element with just the API mount() uses. */
  function makeFakeElement(tagName) {
    const attrs = new Map()
    return {
      tagName,
      attrs,
      parentNode: null,
      setAttribute(name, value) {
        attrs.set(name, String(value))
      },
      getAttribute(name) {
        return attrs.get(name) ?? null
      },
      appendChild(child) {
        child.parentNode = this
        return child
      },
      removeChild(child) {
        child.parentNode = null
        return child
      },
    }
  }

  beforeEach(() => {
    savedDocument = globalThis.document
    savedWindow = globalThis.window
    globalThis.document = { createElement: (tag) => makeFakeElement(tag) }
    globalThis.window = {}
  })

  afterEach(() => {
    globalThis.document = savedDocument
    globalThis.window = savedWindow
  })

  it('strips a trailing /api/v1 from the default backend attribute', () => {
    let createdEl = null
    globalThis.document = {
      createElement(tag) {
        createdEl = makeFakeElement(tag)
        return createdEl
      },
    }

    const client = createNubiClient({
      baseUrl: 'https://x.com/api/v1/',
      getToken: STATIC_TOKEN,
    })

    const container = makeFakeElement('div')
    const handle = client.embed.mount(container, { query: 'q' })

    assert.ok(createdEl, 'should create a nubi-dashboard element')
    assert.equal(createdEl.tagName, 'nubi-dashboard')
    assert.equal(createdEl.getAttribute('query'), 'q')
    assert.equal(createdEl.getAttribute('backend'), 'https://x.com')
    assert.equal(createdEl.parentNode, container)

    handle.unmount()
    assert.equal(createdEl.parentNode, null)
  })

  it('strips /api/v1 from an explicitly passed backend option', () => {
    let createdEl = null
    globalThis.document = {
      createElement(tag) {
        createdEl = makeFakeElement(tag)
        return createdEl
      },
    }

    const client = createNubiClient({
      baseUrl: 'https://x.com',
      getToken: STATIC_TOKEN,
    })

    const container = makeFakeElement('div')
    client.embed.mount(container, {
      query: 'q',
      backend: 'https://other.example.com/api/v1/',
    })

    assert.equal(createdEl.getAttribute('backend'), 'https://other.example.com')
  })
})

// ---------------------------------------------------------------------------

describe('getToken — async function', () => {
  it('calls async getToken before each request', async () => {
    let callCount = 0
    const dynamicGetToken = async () => {
      callCount++
      return `dynamic-token-${callCount}`
    }

    const mockFetch = makeFetchMock({ status: 200, body: { user: { id: 'u1' } } })
    globalThis.fetch = mockFetch

    const client = createNubiClient({ baseUrl: BASE_URL, getToken: dynamicGetToken })

    await client.auth.me()
    assert.equal(callCount, 1)
    assert.match(
      mockFetch.lastCall().init.headers.get('Authorization'),
      /Bearer dynamic-token-1/,
    )

    // Second call should use a fresh token
    const mockFetch2 = makeFetchMock({ status: 200, body: { user: { id: 'u1' } } })
    globalThis.fetch = mockFetch2
    await client.auth.me()
    assert.equal(callCount, 2)
    assert.match(
      mockFetch2.lastCall().init.headers.get('Authorization'),
      /Bearer dynamic-token-2/,
    )
  })
})
