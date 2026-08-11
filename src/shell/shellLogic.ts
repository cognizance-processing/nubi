/**
 * shellLogic.ts — pure, framework-free helpers for the authenticated app
 * shell + admin surfaces (topbar, right rail, environments, git sync, settings).
 *
 * These were previously inlined inside components/contexts where they could not
 * be unit-tested without a DOM harness. Extracting them here keeps the logic
 * importable and covered by `node --test` (see shellLogic.test.ts) while the
 * components stay thin presentation wrappers.
 *
 * Plain ES module (no JSX / React) so it runs directly under node --test.
 */

export interface EnvRow {
  id?: string
  key: string
  is_default?: boolean
  protected?: boolean
  _ghost?: boolean
  imported?: boolean
  warning?: string
  git_branch?: string
}

// ---------------------------------------------------------------------------
// Environments
// ---------------------------------------------------------------------------

/** The project's default env key — the is_default row's key, falling back to 'prod'. */
export function defaultEnvKey(list: EnvRow[] | null | undefined): string {
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
 */
export function resolveActiveEnv(saved: string | null | undefined, list: EnvRow[] | null | undefined): string {
  const savedIsValid = saved && (!Array.isArray(list) || list.some(e => e.key === saved))
  return savedIsValid ? saved : defaultEnvKey(list)
}

/** prod = emerald (live), dev = sky, anything else (custom) = violet. */
export function envDotClass(envKey: string): string {
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
 */
export function buildEnvRows(
  environments: EnvRow[] | null,
  activeEnv: string,
): { apiMode: boolean; rows: EnvRow[] } {
  const apiMode = Array.isArray(environments)
  const envs: EnvRow[] = apiMode
    ? (environments as EnvRow[])
    : ['prod', 'dev'].map(key => ({ id: key, key, is_default: key === 'prod', protected: true }))
  const rows = envs.some(e => e.key === activeEnv)
    ? envs
    : [...envs, { id: activeEnv, key: activeEnv, is_default: false, protected: false, _ghost: true }]
  return { apiMode, rows }
}

/**
 * Whether an env row is a user-created (deletable) custom environment — i.e.
 * the delete affordance should be shown for it.
 */
export function isCustomEnv(env: EnvRow | null | undefined, apiMode: boolean): boolean {
  return Boolean(apiMode && env && !env.is_default && !env.protected && !env._ghost)
}

// ---------------------------------------------------------------------------
// Right rail
// ---------------------------------------------------------------------------

export interface RailItem {
  hidden?: boolean
  active?: boolean
  label: string
  badge?: number
}

/**
 * The rail renders only non-hidden items. Returned separately so the empty
 * case (every item hidden) can be tested + short-circuited by the component.
 */
export function visibleRailItems<T extends { hidden?: boolean }>(items: T[]): T[] {
  return (Array.isArray(items) ? items : []).filter(it => it && !it.hidden)
}

/** The accessible label for a rail toggle — describes the action (open/close), the panel, and an optional unread badge count. */
export function railItemAriaLabel({ active, label, badge }: RailItem): string {
  const verb = active ? 'Close' : 'Open'
  return badge ? `${verb} ${label} (${badge} unread)` : `${verb} ${label}`
}

/** Clamp a badge count to the "99+" display convention; 0/undefined → ''. */
export function formatBadgeCount(n: number | null | undefined): string {
  if (!n || n <= 0) return ''
  return n > 99 ? '99+' : String(n)
}

// ---------------------------------------------------------------------------
// Git sync
// ---------------------------------------------------------------------------

/** First 7 chars of a sha (git short form); '' for nullish. */
export function shortSha(sha: string | null | undefined): string {
  return (sha || '').slice(0, 7)
}

export interface PushResult {
  committed?: boolean
  files?: number
  sha?: string | null
  pushed?: boolean
  warnings?: string[]
}

/** Human feedback string for a completed push (POST /environments/{id}/git/push). */
export function formatPushNotice(res: PushResult | null | undefined): string {
  const warn = res?.warnings?.length ? ` (${res.warnings.join('; ')})` : ''
  if (!res?.committed) return `Nothing to commit${warn}`
  const files = res.files ?? 0
  const plural = files === 1 ? '' : 's'
  const pushed = res.pushed ? ', pushed to remote' : ''
  return `Committed ${files} file${plural} @ ${shortSha(res.sha)}${pushed}${warn}`
}

export interface PullResult {
  up_to_date?: boolean
  strategy?: string
  pulled?: boolean
  sha?: string | null
  updated?: Record<string, number>
  warning?: string
}

/** Human feedback string for a completed pull (POST /environments/{id}/git/pull). */
export function formatPullNotice(res: PullResult | null | undefined): string {
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
 */
export function isValidEmail(value: string): boolean {
  if (typeof value !== 'string') return false
  const v = value.trim()
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)
}

/**
 * Normalize a free-typed environment key to the allowed charset (lowercase
 * alphanumerics, dash, underscore). Mirrors the sidebar create flow so the
 * accepted key is predictable.
 */
export function normalizeEnvKey(value: unknown): string {
  return String(value ?? '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '')
}

/**
 * Whether a settings "name" edit is a no-op submit (empty after trim, or
 * unchanged vs the current value) — lets a form disable Save when nothing
 * would change.
 */
export function isUnchangedName(next: unknown, current: unknown): boolean {
  const n = String(next ?? '').trim()
  if (!n) return true
  return n === String(current ?? '').trim()
}
