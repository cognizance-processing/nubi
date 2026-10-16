/**
 * shellLogic.test.ts — unit tests for the app-shell / admin pure helpers.
 *
 * Run via the project test:dash script (glob src/**\/*.test.{mjs,ts,tsx}).
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
  assert.equal(envDotClass('prod'), 'bg-success')
  assert.equal(envDotClass('dev'), 'bg-accent')
  assert.equal(envDotClass('staging'), 'bg-subtle')
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

// ── shell polish: breadcrumb, chords, spotlight ──────────────────────────────

import {
  crumbTrail,
  chordKeyFor,
  isTypingTarget,
  markKeyboardNav,
  consumeKeyboardNav,
  parseSpotlightQuery,
  pushRecent,
  scoreText,
  resultCountLabel,
} from './shellLogic.js'

test('formatBadgeCount honours a tighter cap', () => {
  assert.equal(formatBadgeCount(9, 9), '9')
  assert.equal(formatBadgeCount(10, 9), '9+')
  assert.equal(formatBadgeCount(30, 9), '9+')
})

test('crumbTrail is empty at depth 1 and for unknown sections', () => {
  assert.deepEqual(crumbTrail('/home'), [])
  assert.deepEqual(crumbTrail('/connectors'), [])
  assert.deepEqual(crumbTrail('/'), [])
  assert.deepEqual(crumbTrail('/nope/thing'), [])
})

test('crumbTrail uses the label map, not title-cased slugs', () => {
  assert.deepEqual(crumbTrail('/settings/ai-providers'), [
    { label: 'Settings', to: '/settings' },
    { label: 'AI providers', to: '/settings/ai-providers' },
  ])
  assert.equal(crumbTrail('/settings/mcp')[1].label, 'MCP')
  assert.equal(crumbTrail('/settings/access-grants')[1].label, 'Access grants')
  assert.equal(crumbTrail('/queries/blend')[1].label, 'Blend')
})

test('crumbTrail names id segments and accepts a page-supplied last label', () => {
  const t = crumbTrail('/admin/orgs/3f9a1c22-0b7d-4a55-9c1e-77d0a5d1c001')
  assert.deepEqual(t.map(c => c.label), ['Admin', 'Organisations', 'Organisation'])
  const named = crumbTrail('/queries/q_9f31', 'Revenue by region')
  assert.equal(named[named.length - 1].label, 'Revenue by region')
  assert.equal(named[named.length - 1].to, '/queries/q_9f31')
})

test('chordKeyFor maps routes to their g-chord key', () => {
  assert.equal(chordKeyFor('/queries'), 'q')
  assert.equal(chordKeyFor('/dashboards'), 'd')
  assert.equal(chordKeyFor('/docs'), undefined)
})

test('isTypingTarget recognises form fields and editors', () => {
  assert.equal(isTypingTarget({ tagName: 'INPUT' }), true)
  assert.equal(isTypingTarget({ tagName: 'textarea' }), true)
  assert.equal(isTypingTarget({ tagName: 'DIV', isContentEditable: true }), true)
  assert.equal(isTypingTarget({ tagName: 'DIV', closest: (s: string) => (s.includes('monaco') ? {} : null) }), true)
  assert.equal(isTypingTarget({ tagName: 'BUTTON', closest: () => null }), false)
  assert.equal(isTypingTarget(null), false)
})

test('keyboard-nav flag is read-and-clear', () => {
  assert.equal(consumeKeyboardNav(), false)
  markKeyboardNav()
  assert.equal(consumeKeyboardNav(), true)
  assert.equal(consumeKeyboardNav(), false)
})

test('parseSpotlightQuery splits scope prefixes from the term', () => {
  assert.deepEqual(parseSpotlightQuery('revenue'), { scope: 'all', term: 'revenue' })
  assert.deepEqual(parseSpotlightQuery('d q3 review'), { scope: 'dashboards', term: 'q3 review' })
  assert.deepEqual(parseSpotlightQuery('q '), { scope: 'queries', term: '' })
  assert.deepEqual(parseSpotlightQuery('c pg'), { scope: 'connectors', term: 'pg' })
  assert.deepEqual(parseSpotlightQuery('> theme'), { scope: 'actions', term: 'theme' })
  assert.deepEqual(parseSpotlightQuery('>theme'), { scope: 'actions', term: 'theme' })
  // a letter with no space is just a search term
  assert.deepEqual(parseSpotlightQuery('d'), { scope: 'all', term: 'd' })
  assert.deepEqual(parseSpotlightQuery('data'), { scope: 'all', term: 'data' })
})

test('pushRecent moves to front, de-duplicates and caps', () => {
  assert.deepEqual(pushRecent(['a', 'b'], 'c'), ['c', 'a', 'b'])
  assert.deepEqual(pushRecent(['a', 'b', 'c'], 'b'), ['b', 'a', 'c'])
  assert.equal(pushRecent(['1', '2', '3'], '4', 3).length, 3)
})

test('scoreText prefers prefixes and word starts; rejects missing terms', () => {
  assert.equal(scoreText('Dashboards', ['zzz']), -1)
  assert.ok(scoreText('Dashboards', ['dash']) > scoreText('My dashboards', ['dash']))
  assert.ok(scoreText('My dashboards', ['dash']) > scoreText('Redash', ['dash']))
  assert.equal(scoreText('AI providers', ['ai', 'prov']) > 0, true)
})

test('resultCountLabel pluralises', () => {
  assert.equal(resultCountLabel(0), 'No results')
  assert.equal(resultCountLabel(1), '1 result')
  assert.equal(resultCountLabel(12), '12 results')
})
