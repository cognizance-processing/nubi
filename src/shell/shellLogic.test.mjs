/**
 * shellLogic.test.mjs — unit tests for the app-shell / admin pure helpers.
 *
 * Run with:
 *   node --test src/shell/shellLogic.test.mjs
 *   # or via the project test:dash script (glob src/**\/*.test.mjs)
 *
 * Covers env selection, right-rail filtering/labels, git push/pull notice
 * formatting and settings-form validation — the logic that drives the topbar,
 * right rail, environment switcher, git sync panel and settings forms.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  defaultEnvKey,
  resolveActiveEnv,
  envDotClass,
  buildEnvRows,
  isCustomEnv,
  visibleRailItems,
  railItemAriaLabel,
  formatBadgeCount,
  shortSha,
  formatPushNotice,
  formatPullNotice,
  isValidEmail,
  normalizeEnvKey,
  isUnchangedName,
} from './shellLogic.js'

// ── defaultEnvKey ────────────────────────────────────────────────────────────

test('defaultEnvKey returns the is_default row key', () => {
  assert.equal(defaultEnvKey([{ key: 'dev' }, { key: 'prod', is_default: true }]), 'prod')
})

test('defaultEnvKey falls back to prod for empty/null/no-default lists', () => {
  assert.equal(defaultEnvKey(null), 'prod')
  assert.equal(defaultEnvKey([]), 'prod')
  assert.equal(defaultEnvKey([{ key: 'dev' }, { key: 'stg' }]), 'prod')
})

// ── resolveActiveEnv ─────────────────────────────────────────────────────────

test('resolveActiveEnv honours a saved key present in the list', () => {
  const list = [{ key: 'prod', is_default: true }, { key: 'dev' }]
  assert.equal(resolveActiveEnv('dev', list), 'dev')
})

test('resolveActiveEnv ignores a stale saved key not in the list', () => {
  const list = [{ key: 'prod', is_default: true }, { key: 'dev' }]
  assert.equal(resolveActiveEnv('gone', list), 'prod')
})

test('resolveActiveEnv trusts the saved key when the list is unavailable (offline)', () => {
  assert.equal(resolveActiveEnv('staging', null), 'staging')
})

test('resolveActiveEnv with no saved key defaults to the project default', () => {
  const list = [{ key: 'prod' }, { key: 'dev', is_default: true }]
  assert.equal(resolveActiveEnv(null, list), 'dev')
})

// ── envDotClass ──────────────────────────────────────────────────────────────

test('envDotClass colours prod/dev/custom distinctly', () => {
  assert.equal(envDotClass('prod'), 'bg-emerald-500')
  assert.equal(envDotClass('dev'), 'bg-sky-500')
  assert.equal(envDotClass('staging'), 'bg-violet-500')
})

// ── buildEnvRows ─────────────────────────────────────────────────────────────

test('buildEnvRows falls back to prod/dev pair before the API loads', () => {
  const { apiMode, rows } = buildEnvRows(null, 'prod')
  assert.equal(apiMode, false)
  assert.deepEqual(rows.map(r => r.key), ['prod', 'dev'])
})

test('buildEnvRows uses the API list once loaded', () => {
  const list = [{ key: 'prod', is_default: true }, { key: 'dev' }, { key: 'staging' }]
  const { apiMode, rows } = buildEnvRows(list, 'prod')
  assert.equal(apiMode, true)
  assert.deepEqual(rows.map(r => r.key), ['prod', 'dev', 'staging'])
})

test('buildEnvRows appends a ghost row for an active key absent from the list', () => {
  const { rows } = buildEnvRows([{ key: 'prod', is_default: true }], 'legacy')
  const ghost = rows.find(r => r.key === 'legacy')
  assert.ok(ghost)
  assert.equal(ghost._ghost, true)
})

test('buildEnvRows does not duplicate when active key is present', () => {
  const { rows } = buildEnvRows([{ key: 'prod', is_default: true }, { key: 'dev' }], 'dev')
  assert.equal(rows.filter(r => r.key === 'dev').length, 1)
})

// ── isCustomEnv ──────────────────────────────────────────────────────────────

test('isCustomEnv only flags user-created envs in API mode', () => {
  assert.equal(isCustomEnv({ key: 'staging' }, true), true)
  assert.equal(isCustomEnv({ key: 'prod', is_default: true }, true), false)
  assert.equal(isCustomEnv({ key: 'dev', protected: true }, true), false)
  assert.equal(isCustomEnv({ key: 'legacy', _ghost: true }, true), false)
  // not in API mode (fallback list) → never deletable
  assert.equal(isCustomEnv({ key: 'staging' }, false), false)
})

// ── visibleRailItems ─────────────────────────────────────────────────────────

test('visibleRailItems drops hidden items and tolerates non-arrays', () => {
  const items = [
    { id: 'notifications' },
    { id: 'chat', hidden: true },
    { id: 'git' },
  ]
  assert.deepEqual(visibleRailItems(items).map(i => i.id), ['notifications', 'git'])
  assert.deepEqual(visibleRailItems(null), [])
})

// ── railItemAriaLabel ────────────────────────────────────────────────────────

test('railItemAriaLabel describes action + panel + badge', () => {
  assert.equal(railItemAriaLabel({ active: false, label: 'AI Chat' }), 'Open AI Chat')
  assert.equal(railItemAriaLabel({ active: true, label: 'AI Chat' }), 'Close AI Chat')
  assert.equal(
    railItemAriaLabel({ active: false, label: 'Notifications', badge: 3 }),
    'Open Notifications (3 unread)',
  )
})

// ── formatBadgeCount ─────────────────────────────────────────────────────────

test('formatBadgeCount hides zero and clamps at 99+', () => {
  assert.equal(formatBadgeCount(0), '')
  assert.equal(formatBadgeCount(undefined), '')
  assert.equal(formatBadgeCount(5), '5')
  assert.equal(formatBadgeCount(99), '99')
  assert.equal(formatBadgeCount(100), '99+')
})

// ── shortSha ─────────────────────────────────────────────────────────────────

test('shortSha takes the first 7 chars, safe on null', () => {
  assert.equal(shortSha('abcdef1234567'), 'abcdef1')
  assert.equal(shortSha(null), '')
})

// ── formatPushNotice ─────────────────────────────────────────────────────────

test('formatPushNotice — nothing to commit', () => {
  assert.equal(formatPushNotice({ committed: false }), 'Nothing to commit')
})

test('formatPushNotice — committed, pushed, with plural', () => {
  assert.equal(
    formatPushNotice({ committed: true, files: 3, sha: 'abcdef1234', pushed: true }),
    'Committed 3 files @ abcdef1, pushed to remote',
  )
})

test('formatPushNotice — singular file, not pushed, with warnings', () => {
  assert.equal(
    formatPushNotice({ committed: true, files: 1, sha: 'beadfeed', pushed: false, warnings: ['no remote'] }),
    'Committed 1 file @ beadfee (no remote)',
  )
})

// ── formatPullNotice ─────────────────────────────────────────────────────────

test('formatPullNotice — up to date', () => {
  assert.equal(formatPullNotice({ up_to_date: true }), 'Already up to date.')
})

test('formatPullNotice — take_env overwrite', () => {
  assert.equal(
    formatPullNotice({ strategy: 'take_env', sha: 'cafebabe1' }),
    'Branch overwritten from environment @ cafebab',
  )
})

test('formatPullNotice — pulled with per-kind counts', () => {
  assert.equal(
    formatPullNotice({ pulled: true, sha: 'deadbeef', updated: { board: 2, query: 1 } }),
    'Pulled 2 boards, 1 query @ deadbee',
  )
})

test('formatPullNotice — pulled with no detail falls back to "changes"', () => {
  assert.equal(
    formatPullNotice({ pulled: true, sha: 'deadbeef', updated: {} }),
    'Pulled changes @ deadbee',
  )
})

test('formatPullNotice — no repo surfaces the warning', () => {
  assert.equal(formatPullNotice({ pulled: false, warning: 'No branch.' }), 'No branch.')
  assert.equal(formatPullNotice({}), 'Nothing to pull.')
})

// ── isValidEmail ─────────────────────────────────────────────────────────────

test('isValidEmail accepts well-formed addresses', () => {
  assert.equal(isValidEmail('a@b.co'), true)
  assert.equal(isValidEmail('  teammate@example.com  '), true)
})

test('isValidEmail rejects malformed / empty input', () => {
  assert.equal(isValidEmail('abc'), false)
  assert.equal(isValidEmail('a@b'), false)
  assert.equal(isValidEmail('a @b.co'), false)
  assert.equal(isValidEmail(''), false)
  assert.equal(isValidEmail(null), false)
})

// ── normalizeEnvKey ──────────────────────────────────────────────────────────

test('normalizeEnvKey lowercases and strips disallowed chars', () => {
  assert.equal(normalizeEnvKey('  Staging 2! '), 'staging2')
  assert.equal(normalizeEnvKey('My_Env-1'), 'my_env-1')
  assert.equal(normalizeEnvKey('***'), '')
})

// ── isUnchangedName ──────────────────────────────────────────────────────────

test('isUnchangedName flags empty and unchanged edits', () => {
  assert.equal(isUnchangedName('', 'Acme'), true)
  assert.equal(isUnchangedName('   ', 'Acme'), true)
  assert.equal(isUnchangedName('Acme', 'Acme'), true)
  assert.equal(isUnchangedName('  Acme  ', 'Acme'), true)
})

test('isUnchangedName allows a real rename', () => {
  assert.equal(isUnchangedName('Acme Inc', 'Acme'), false)
})
