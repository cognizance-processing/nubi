/**
 * flowCodeView.logic.test.mjs — unit tests for the pure FlowCodeView logic.
 *
 * Run with:
 *   node --test src/flows/flowCodeView.logic.test.mjs
 *   # or via: npm run test:dash
 *
 * These lock in the cell→file projection, the codegen load-key derivation
 * (saved + unsaved + post-Apply), the error classification (the real
 * "doesn't work" bug: a 400 on a half-built spec), and the active-file
 * selection / source resolution.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  FLOW_PY_ID,
  fileMetaForTask,
  buildCellFiles,
  deriveLoadKey,
  classifyCodegenError,
  selectActiveId,
  activeSourceFor,
} from './flowCodeView.logic.js'

// ── fileMetaForTask ──────────────────────────────────────────────────────────

test('fileMetaForTask maps sql / query → editable .sql', () => {
  assert.equal(fileMetaForTask({ cell_type: 'sql' }).key, 'sql')
  assert.equal(fileMetaForTask({ kind: 'query' }).ext, 'sql')
  assert.equal(fileMetaForTask({ kind: 'query' }).lang, 'sql')
})

test('fileMetaForTask maps python → editable .py via config.code', () => {
  const m = fileMetaForTask({ kind: 'python' })
  assert.equal(m.key, 'code')
  assert.equal(m.ext, 'py')
})

test('fileMetaForTask maps note/markdown/noop → editable .md', () => {
  assert.equal(fileMetaForTask({ cell_type: 'markdown' }).key, 'markdown')
  assert.equal(fileMetaForTask({ kind: 'note' }).ext, 'md')
  // A Note cell persists as kind 'noop' (see notebooks.makeBlankCell) — it must
  // still project to the editable markdown file, not a read-only JSON dump.
  assert.equal(fileMetaForTask({ kind: 'noop' }).key, 'markdown')
})

test('fileMetaForTask falls back to read-only JSON for sourceless kinds', () => {
  for (const kind of ['agent', 'materialize', 'branch', 'map', 'bucket_load']) {
    const m = fileMetaForTask({ kind })
    assert.equal(m.key, null, `${kind} should be read-only JSON`)
    assert.equal(m.ext, 'json')
  }
})

// ── buildCellFiles ───────────────────────────────────────────────────────────

test('buildCellFiles: empty spec → no files (empty-spec case)', () => {
  assert.deepEqual(buildCellFiles(null), [])
  assert.deepEqual(buildCellFiles({}), [])
  assert.deepEqual(buildCellFiles({ tasks: [] }), [])
})

test('buildCellFiles numbers + slugs files and tags index', () => {
  const spec = {
    tasks: [
      { key: 'pull rev', kind: 'query', config: { sql: 'SELECT 1' } },
      { key: 'shape', kind: 'python', config: { code: 'x=1' } },
    ],
  }
  const files = buildCellFiles(spec)
  assert.equal(files.length, 2)
  assert.equal(files[0].name, '01_pull_rev.sql')
  assert.equal(files[0].index, 0)
  assert.equal(files[1].name, '02_shape.py')
  assert.equal(files[1].id, 'cell:1')
})

// ── deriveLoadKey ────────────────────────────────────────────────────────────

test('deriveLoadKey: nothing to generate → null (no perpetual loading guard)', () => {
  assert.equal(deriveLoadKey(null, null), null)
})

test('deriveLoadKey: unsaved flow keys on spec content', () => {
  const a = deriveLoadKey(null, { tasks: [{ key: 'a' }] })
  const b = deriveLoadKey(null, { tasks: [{ key: 'b' }] })
  assert.notEqual(a, b)
  // identical content → identical key (so the effect does NOT thrash/refetch).
  assert.equal(
    deriveLoadKey(null, { tasks: [{ key: 'a' }] }),
    deriveLoadKey(null, { tasks: [{ key: 'a' }] }),
  )
})

test('deriveLoadKey: saved flow re-keys when the spec content changes (post-Apply)', () => {
  // THE BUG: keying on `id:<flowId>` alone meant an Apply / edit never
  // re-generated flow.py — it stayed pinned to the persisted DB row. Keying on
  // content too means the saved-flow code view tracks the live, edited spec.
  const before = deriveLoadKey('flow-123', { tasks: [{ key: 'a', config: { sql: 'SELECT 1' } }] })
  const after = deriveLoadKey('flow-123', { tasks: [{ key: 'a', config: { sql: 'SELECT 2' } }] })
  assert.notEqual(before, after)
  // ...but is stable when nothing changed (no needless refetch).
  assert.equal(
    deriveLoadKey('flow-123', { tasks: [{ key: 'a' }] }),
    deriveLoadKey('flow-123', { tasks: [{ key: 'a' }] }),
  )
})

// ── classifyCodegenError — the core "doesn't work" fix ───────────────────────

test('classifyCodegenError: 404 → unavailable (endpoint not deployed)', () => {
  assert.equal(classifyCodegenError({ status: 404 }).kind, 'unavailable')
  assert.equal(classifyCodegenError({ message: 'Request failed: 404' }).kind, 'unavailable')
})

test('classifyCodegenError: 400 on a half-built spec → invalidSpec (gentle hint)', () => {
  // This is the actual reported bug. A flow with one empty SQL cell
  // (config.sql === '') is a HARD validation error on the backend, so
  // POST /flows/codegen returns 400. The old code rendered this as a red
  // "error" with no editor → user reads "code for flow.py doesn't work".
  const r = classifyCodegenError({
    status: 400,
    message: "Task 'cell_sql_ab12' (query): config must include 'query_id' or 'sql'.",
  })
  assert.equal(r.kind, 'invalidSpec')
  assert.match(r.message, /query_id' or 'sql'/)
})

test('classifyCodegenError: 422 also treated as invalidSpec', () => {
  assert.equal(classifyCodegenError({ status: 422 }).kind, 'invalidSpec')
})

test('classifyCodegenError: 500 / network → hard error', () => {
  assert.equal(classifyCodegenError({ status: 500, message: 'boom' }).kind, 'error')
  assert.equal(classifyCodegenError({ message: 'Failed to fetch' }).kind, 'error')
})

// ── selectActiveId ───────────────────────────────────────────────────────────

test('selectActiveId keeps flow.py selected', () => {
  assert.equal(selectActiveId(FLOW_PY_ID, []), FLOW_PY_ID)
})

test('selectActiveId keeps a live cell selected, falls back when it is deleted', () => {
  const files = [{ id: 'cell:0' }, { id: 'cell:1' }]
  assert.equal(selectActiveId('cell:1', files), 'cell:1')
  // task deleted → its file id no longer present → fall back to flow.py
  assert.equal(selectActiveId('cell:5', files), FLOW_PY_ID)
})

// ── activeSourceFor ──────────────────────────────────────────────────────────

test('activeSourceFor: flow.py prefers the live editor value', () => {
  assert.equal(
    activeSourceFor({ activeId: FLOW_PY_ID, pyValue: 'edited', pySource: 'gen' }),
    'edited',
  )
  assert.equal(
    activeSourceFor({ activeId: FLOW_PY_ID, pyValue: null, pySource: 'gen' }),
    'gen',
  )
  assert.equal(activeSourceFor({ activeId: FLOW_PY_ID, pyValue: null, pySource: null }), '')
})

test('activeSourceFor: source cell returns config[key] as a string', () => {
  const selectedCell = { key: 'sql', task: { config: { sql: 'SELECT 42' } } }
  assert.equal(activeSourceFor({ activeId: 'cell:0', selectedCell }), 'SELECT 42')
  // empty SQL cell → empty string (not "undefined")
  const empty = { key: 'sql', task: { config: { sql: '' } } }
  assert.equal(activeSourceFor({ activeId: 'cell:0', selectedCell: empty }), '')
})

test('activeSourceFor: read-only cell renders config JSON', () => {
  const selectedCell = { key: null, task: { config: { prompt: 'hi' } } }
  const out = activeSourceFor({ activeId: 'cell:0', selectedCell })
  assert.deepEqual(JSON.parse(out), { prompt: 'hi' })
})
