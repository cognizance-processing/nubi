/**
 * shellLogic.js — pure, framework-free helpers for the authenticated app
 * shell + admin surfaces (topbar, right rail, environments, git sync, settings).
 *
 * These were previously inlined inside components/contexts where they could not
 * be unit-tested without a DOM harness. Extracting them here keeps the logic
 * importable and covered by `node --test` (see shellLogic.test.mjs) while the
 * components stay thin presentation wrappers.
 *
 * Plain ES module (no JSX / React) so it runs directly under node --test.
 */

// ---------------------------------------------------------------------------
// Environments
// ---------------------------------------------------------------------------

/**
 * The project's default env key — the is_default row's key, falling back to
 * 'prod' when the list is missing/empty or has no default flagged.
 *
 * @param {Array<{key: string, is_default?: boolean}>|null|undefined} list
 * @returns {string}
 */
export function defaultEnvKey(list) {
  return (Array.isArray(list) && list.find(e => e.is_default)?.key) || 'prod'
}

/**
 * Decide which env key should be active given a saved (persisted) selection and
 * the loaded environment list. Used by EnvContext on (re)load.
 *
 * Rules:
 *   - A saved key is honoured when the list is unavailable (offline: trust the
 *     saved selection so it survives reloads) OR when it exists in the list.
 *   - Otherwise fall back to the project default (is_default → 'prod').
 *
 * @param {string|null|undefined} saved
 * @param {Array<{key: string, is_default?: boolean}>|null|undefined} list
 * @returns {string}
 */
export function resolveActiveEnv(saved, list) {
  const savedIsValid = saved && (!Array.isArray(list) || list.some(e => e.key === saved))
  return savedIsValid ? saved : defaultEnvKey(list)
}

/** prod = emerald (live), dev = sky, anything else (custom) = violet. */
export function envDotClass(envKey) {
  if (envKey === 'prod') return 'bg-emerald-500'
  if (envKey === 'dev') return 'bg-sky-500'
  return 'bg-violet-500'
}

/**
 * Build the rows the sidebar env selector renders.
 *
 * When the API list has loaded we use it; before that (or when the API is
 * unavailable) we fall back to the standard prod/dev pair so the control stays
 * usable. The currently-active key is always appended as a non-deletable
 * "ghost" row when it isn't already present (e.g. a legacy localStorage custom
 * env) so the current selection is never invisible.
 *
 * @param {Array<Object>|null} environments  API list, or null when unloaded.
 * @param {string} activeEnv  the currently selected key.
 * @returns {{ apiMode: boolean, rows: Array<Object> }}
 */
export function buildEnvRows(environments, activeEnv) {
  const apiMode = Array.isArray(environments)
  const envs = apiMode
    ? environments
    : ['prod', 'dev'].map(key => ({ id: key, key, is_default: key === 'prod', protected: true }))
  const rows = envs.some(e => e.key === activeEnv)
    ? envs
    : [...envs, { id: activeEnv, key: activeEnv, is_default: false, protected: false, _ghost: true }]
  return { apiMode, rows }
}

/**
 * Whether an env row is a user-created (deletable) custom environment — i.e.
 * the delete affordance should be shown for it.
 *
 * @param {{is_default?: boolean, protected?: boolean, _ghost?: boolean}} env
 * @param {boolean} apiMode  true once the API list has loaded.
 * @returns {boolean}
 */
export function isCustomEnv(env, apiMode) {
  return Boolean(apiMode && env && !env.is_default && !env.protected && !env._ghost)
}

// ---------------------------------------------------------------------------
// Right rail
// ---------------------------------------------------------------------------

/**
 * The rail renders only non-hidden items. Returned separately so the empty
 * case (every item hidden) can be tested + short-circuited by the component.
 *
 * @param {Array<{hidden?: boolean}>} items
 * @returns {Array<Object>}
 */
export function visibleRailItems(items) {
  return (Array.isArray(items) ? items : []).filter(it => it && !it.hidden)
}

/**
 * The accessible label for a rail toggle — describes the action (open/close),
 * the panel, and an optional unread badge count.
 *
 * @param {{active?: boolean, label: string, badge?: number}} item
 * @returns {string}
 */
export function railItemAriaLabel({ active, label, badge }) {
  const verb = active ? 'Close' : 'Open'
  return badge ? `${verb} ${label} (${badge} unread)` : `${verb} ${label}`
}

/** Clamp a badge count to the "99+" display convention; 0/undefined → ''. */
export function formatBadgeCount(n) {
  if (!n || n <= 0) return ''
  return n > 99 ? '99+' : String(n)
}

// ---------------------------------------------------------------------------
// Git sync
// ---------------------------------------------------------------------------

/** First 7 chars of a sha (git short form); '' for nullish. */
export function shortSha(sha) {
  return (sha || '').slice(0, 7)
}

/**
 * Human feedback string for a completed push (POST /environments/{id}/git/push).
 *
 * @param {{committed?: boolean, files?: number, sha?: string|null,
 *   pushed?: boolean, warnings?: string[]}} res
 * @returns {string}
 */
export function formatPushNotice(res) {
  const warn = res?.warnings?.length ? ` (${res.warnings.join('; ')})` : ''
  if (!res?.committed) return `Nothing to commit${warn}`
  const files = res.files ?? 0
  const plural = files === 1 ? '' : 's'
  const pushed = res.pushed ? ', pushed to remote' : ''
  return `Committed ${files} file${plural} @ ${shortSha(res.sha)}${pushed}${warn}`
}

/**
 * Human feedback string for a completed pull (POST /environments/{id}/git/pull).
 *
 * @param {{up_to_date?: boolean, strategy?: string, pulled?: boolean,
 *   sha?: string|null, updated?: Record<string, number>, warning?: string}} res
 * @returns {string}
 */
export function formatPullNotice(res) {
  if (res?.up_to_date) return 'Already up to date.'
  if (res?.strategy === 'take_env') {
    return `Branch overwritten from environment @ ${shortSha(res.sha)}`
  }
  if (res?.pulled) {
    const counts = Object.entries(res.updated ?? {})
      .map(([kind, n]) => `${n} ${kind}${n === 1 ? '' : 's'}`)
      .join(', ')
    return `Pulled ${counts || 'changes'} @ ${shortSha(res.sha)}`
  }
  return res?.warning || 'Nothing to pull.'
}

// ---------------------------------------------------------------------------
// Settings forms
// ---------------------------------------------------------------------------

/**
 * Pragmatic email validity check for the invite form (a backend re-validates).
 * Requires a single @, a non-empty local part, and a dotted domain with no
 * whitespace.
 *
 * @param {string} value
 * @returns {boolean}
 */
export function isValidEmail(value) {
  if (typeof value !== 'string') return false
  const v = value.trim()
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)
}

/**
 * Normalize a free-typed environment key to the allowed charset (lowercase
 * alphanumerics, dash, underscore). Mirrors the sidebar create flow so the
 * accepted key is predictable.
 *
 * @param {string} value
 * @returns {string}
 */
export function normalizeEnvKey(value) {
  return String(value ?? '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '')
}

/**
 * Whether a settings "name" edit is a no-op submit (empty after trim, or
 * unchanged vs the current value) — lets a form disable Save when nothing
 * would change.
 *
 * @param {string} next  the edited value.
 * @param {string} current  the persisted value.
 * @returns {boolean} true when saving would change nothing meaningful.
 */
export function isUnchangedName(next, current) {
  const n = String(next ?? '').trim()
  if (!n) return true
  return n === String(current ?? '').trim()
}
