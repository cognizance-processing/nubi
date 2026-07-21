/**
 * liveState.test.mjs — Unit tests for draft-vs-live resolution.
 *
 * Run with:
 *   node --test src/dashboards/liveState.test.mjs
 *
 * The contract that matters: "Live" must never over-claim. Reporting a board as
 * live when it is actually serving the unpushed draft would make the Live/Edit
 * switch a lie, so the ambiguous cases (no env list, failed resolution, missing
 * resolved_version) all have to fall to "not live".
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import {
  resolveLiveEnvKey,
  resolveDraftEnvKey,
  resolveLiveRender,
  canPushToLive,
  summarisePush,
  FALLBACK_LIVE_ENV,
  FALLBACK_DRAFT_ENV,
} from './liveState.js'

// The env pair the backend seeds for every project (environments/store.py).
const SEEDED = [
  { key: 'dev', name: 'Development', is_default: false, protected: false, position: 0 },
  { key: 'prod', name: 'Production', is_default: true, protected: true, position: 1 },
]

describe('resolveLiveEnvKey', () => {
  test('picks the default environment', () => {
    assert.equal(resolveLiveEnvKey(SEEDED), 'prod')
  })

  test('honours a custom default rather than assuming prod', () => {
    const custom = [
      { key: 'dev', protected: false, position: 0 },
      { key: 'staging', protected: true, position: 1 },
      { key: 'production', is_default: true, protected: true, position: 2 },
    ]
    assert.equal(resolveLiveEnvKey(custom), 'production')
  })

  test('with no default, takes the last protected env in the pipeline', () => {
    const noDefault = [
      { key: 'dev', protected: false, position: 0 },
      { key: 'staging', protected: true, position: 1 },
      { key: 'live', protected: true, position: 2 },
    ]
    assert.equal(resolveLiveEnvKey(noDefault), 'live')
  })

  test('falls back to the seeded key when the env list is unusable', () => {
    // listEnvironments() degrades to null offline — must not crash the page.
    assert.equal(resolveLiveEnvKey(null), FALLBACK_LIVE_ENV)
    assert.equal(resolveLiveEnvKey([]), FALLBACK_LIVE_ENV)
    assert.equal(resolveLiveEnvKey(undefined), FALLBACK_LIVE_ENV)
    assert.equal(resolveLiveEnvKey([{ key: 'dev', protected: false }]), FALLBACK_LIVE_ENV)
  })
})

describe('resolveDraftEnvKey', () => {
  test('picks the earliest unprotected environment', () => {
    assert.equal(resolveDraftEnvKey(SEEDED), 'dev')
  })

  test('never returns a protected env — checkpointing into one is refused', () => {
    const envs = [
      { key: 'sandbox', protected: false, position: 3 },
      { key: 'prod', is_default: true, protected: true, position: 1 },
    ]
    assert.equal(resolveDraftEnvKey(envs), 'sandbox')
  })

  test('falls back to the seeded key when nothing is usable', () => {
    assert.equal(resolveDraftEnvKey(null), FALLBACK_DRAFT_ENV)
    assert.equal(resolveDraftEnvKey([]), FALLBACK_DRAFT_ENV)
    assert.equal(resolveDraftEnvKey([{ key: 'prod', protected: true }]), FALLBACK_DRAFT_ENV)
  })
})

describe('resolveLiveRender', () => {
  test('reports a pushed board as live, with its version', () => {
    const board = {
      config: { spec: { title: 'B', widgets: [] } },
      resolved_version: { id: 'v-uuid', version: 7 },
    }
    const r = resolveLiveRender(board)
    assert.equal(r.isLive, true)
    assert.equal(r.version, 7)
    assert.equal(r.spec.title, 'B')
  })

  test('a never-pushed board serves its draft and is NOT live', () => {
    // This is every existing board until someone pushes it — the back-compat
    // case. `?env=` returns the draft with resolved_version: null.
    const board = { config: { spec: { title: 'B' } }, resolved_version: null }
    const r = resolveLiveRender(board)
    assert.equal(r.isLive, false)
    assert.equal(r.version, null)
    assert.equal(r.spec.title, 'B')
  })

  test('an absent resolved_version is not live (never assume)', () => {
    const r = resolveLiveRender({ config: { spec: {} } })
    assert.equal(r.isLive, false)
    assert.equal(r.version, null)
  })

  test('carries legacy html boards through', () => {
    const r = resolveLiveRender({ config: { html: '<p>hi</p>' }, resolved_version: null })
    assert.equal(r.html, '<p>hi</p>')
    assert.equal(r.spec, null)
  })

  test('tolerates a null/empty board without throwing', () => {
    for (const input of [null, undefined, {}, { config: null }]) {
      const r = resolveLiveRender(input)
      assert.equal(r.isLive, false)
      assert.equal(r.spec, null)
      assert.equal(r.html, null)
    }
  })

  test('a non-numeric version is not treated as a version', () => {
    const r = resolveLiveRender({ config: {}, resolved_version: { id: 'x', version: 'seven' } })
    assert.equal(r.isLive, true)     // a pointer exists…
    assert.equal(r.version, null)    // …but we won't print garbage as "v seven"
  })
})

describe('canPushToLive', () => {
  test('a never-pushed board can always be pushed — that is the first publish', () => {
    assert.equal(canPushToLive({ isLive: false, dirty: false }), true)
  })

  test('a live board with unsaved edits can be pushed', () => {
    assert.equal(canPushToLive({ isLive: true, dirty: true }), true)
  })

  test('a live board with a clean draft offers nothing', () => {
    assert.equal(canPushToLive({ isLive: true, dirty: false }), false)
  })
})

describe('summarisePush', () => {
  test('reports the new version and how many resources went with it', () => {
    const s = summarisePush({ version: 3, deduped: false }, { promoted: [{}, {}, {}] })
    assert.deepEqual(s, { version: 3, deduped: false, promoted: 3 })
  })

  test('surfaces a deduped checkpoint (nothing changed since the last version)', () => {
    const s = summarisePush({ version: 3, deduped: true }, { promoted: [{}] })
    assert.equal(s.deduped, true)
    assert.equal(s.version, 3)
  })

  test('tolerates missing/!array fields rather than throwing mid-publish', () => {
    assert.deepEqual(summarisePush(null, null), { version: null, deduped: false, promoted: 0 })
    assert.deepEqual(summarisePush({}, {}), { version: null, deduped: false, promoted: 0 })
    assert.equal(summarisePush({ version: 1 }, { promoted: 'nope' }).promoted, 0)
  })
})
