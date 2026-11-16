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

/** Swiss: one status colour for live, the accent for dev, a neutral for everything custom. */
export function envDotClass(envKey: string): string {
  if (envKey === 'prod') return 'bg-success'
  if (envKey === 'dev') return 'bg-accent'
  return 'bg-subtle'
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

/**
 * Clamp a badge count to the "99+" display convention; 0/undefined → ''.
 * `cap` lets a tighter surface (the topbar's square count) clamp earlier: cap=9 → "9+".
 */
export function formatBadgeCount(n: number | null | undefined, cap = 99): string {
  if (!n || n <= 0) return ''
  return n > cap ? `${cap}+` : String(n)
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


// ---------------------------------------------------------------------------
// Breadcrumb
// ---------------------------------------------------------------------------

export interface Crumb {
  label: string
  to: string
}

const SECTION_LABELS: Record<string, string> = {
  home: 'Home',
  connectors: 'Connectors',
  data: 'Data',
  queries: 'Queries',
  dashboards: 'Dashboards',
  flows: 'Flows',
  watches: 'Watches',
  editor: 'Dashboard editor',
  settings: 'Settings',
  admin: 'Admin',
  billing: 'Billing',
  invite: 'Invitation',
}

/** Known second-level segments. Anything else is treated as an id and gets a generic noun. */
const SUB_LABELS: Record<string, Record<string, string>> = {
  settings: {
    profile: 'Profile',
    organization: 'Organization',
    members: 'Members',
    integrations: 'Integrations',
    'ai-providers': 'AI providers',
    bridges: 'Bridges',
    usage: 'Usage',
    project: 'Project',
    security: 'Security',
    mcp: 'MCP',
    connections: 'Connections',
    'access-grants': 'Access grants',
  },
  admin: { users: 'Users', orgs: 'Organisations', audit: 'Audit log' },
  queries: { blend: 'Blend' },
}

/** The noun used for an id segment ("/queries/abc" → "Query"). */
const ID_NOUN: Record<string, string> = {
  queries: 'Query',
  flows: 'Flow',
  connectors: 'Connector',
  orgs: 'Organisation',
  editor: 'Dashboard',
}

const humanize = (s: string) => {
  const t = s.replace(/[-_]+/g, ' ').trim()
  return t.charAt(0).toUpperCase() + t.slice(1)
}

/**
 * The breadcrumb trail for a pathname — **empty below depth 2** (top-level pages never repeat their
 * title in the topbar). `lastLabel` replaces the final segment's label (dynamic routes: a board or
 * query name supplied by the page).
 */
export function crumbTrail(pathname: string, lastLabel?: string | null): Crumb[] {
  const parts = pathname.split('/').filter(Boolean).map(decodeURIComponent)
  if (parts.length < 2 || !SECTION_LABELS[parts[0]]) return []
  const trail: Crumb[] = [{ label: SECTION_LABELS[parts[0]], to: `/${parts[0]}` }]
  let prev = parts[0]
  for (let i = 1; i < parts.length; i++) {
    const seg = parts[i]
    const known = SUB_LABELS[parts[0]]?.[seg]
    const label = known ?? (/^[0-9a-f-]{8,}$/i.test(seg) || /\d/.test(seg) ? ID_NOUN[prev] ?? humanize(seg) : humanize(seg))
    trail.push({ label, to: '/' + parts.slice(0, i + 1).join('/') })
    prev = seg
  }
  if (lastLabel) trail[trail.length - 1] = { ...trail[trail.length - 1], label: lastLabel }
  return trail
}

// ---------------------------------------------------------------------------
// g-chords (go to …)
// ---------------------------------------------------------------------------

/** `g` then the key → route. Single source for the sidebar tooltips, Spotlight kbd column and the shortcut sheet. */
export const NAV_CHORDS: Record<string, { to: string; label: string }> = {
  h: { to: '/home', label: 'Home' },
  c: { to: '/connectors', label: 'Connectors' },
  e: { to: '/data', label: 'Data' },
  q: { to: '/queries', label: 'Queries' },
  d: { to: '/dashboards', label: 'Dashboards' },
  f: { to: '/flows', label: 'Flows' },
  w: { to: '/watches', label: 'Watches' },
  s: { to: '/settings', label: 'Settings' },
}

/** The chord key for a route (`/queries` → `q`), or undefined. */
export function chordKeyFor(to: string): string | undefined {
  return Object.keys(NAV_CHORDS).find(k => NAV_CHORDS[k].to === to)
}

/** True when a key event target is somewhere the user is typing (so global single-key shortcuts must not fire). */
export function isTypingTarget(el: unknown): boolean {
  const t = el as { tagName?: string; isContentEditable?: boolean; closest?: (s: string) => unknown } | null
  if (!t || !t.tagName) return false
  const tag = t.tagName.toUpperCase()
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (t.isContentEditable) return true
  return Boolean(t.closest?.('.monaco-editor, [role="textbox"], [contenteditable="true"]'))
}

// ---------------------------------------------------------------------------
// Keyboard-started navigation is not animated (brief: motion rule 5)
// ---------------------------------------------------------------------------

let keyboardNavAt = 0
/** Call right before navigating from a keyboard shortcut. */
export function markKeyboardNav(): void { keyboardNavAt = Date.now() }
/** Read-and-clear. True only if marked within the last second (a mark that never led to a route change goes stale). */
export function consumeKeyboardNav(): boolean {
  const v = keyboardNavAt > 0 && Date.now() - keyboardNavAt < 1000
  keyboardNavAt = 0
  return v
}

// ---------------------------------------------------------------------------
// Spotlight
// ---------------------------------------------------------------------------

export type SpotlightScope = 'all' | 'dashboards' | 'queries' | 'connectors' | 'actions'

const SCOPE_PREFIX: Record<string, SpotlightScope> = { d: 'dashboards', q: 'queries', c: 'connectors', '>': 'actions' }

/**
 * Split a raw query into a scope and a search term. `d `, `q `, `c ` (letter + space) and `>`
 * narrow the search to one kind of thing; anything else searches everything.
 */
export function parseSpotlightQuery(raw: string): { scope: SpotlightScope; term: string } {
  const m = /^([dqc]) (.*)$/s.exec(raw) ?? /^(>)\s*(.*)$/s.exec(raw)
  if (!m) return { scope: 'all', term: raw }
  return { scope: SCOPE_PREFIX[m[1]], term: m[2] }
}

/** Most-recent-first list with the item moved to the front, de-duplicated, capped. */
export function pushRecent(list: string[], id: string, max = 8): string[] {
  return [id, ...list.filter(x => x !== id)].slice(0, max)
}

/** Rank by: every term present; prefix and word-start matches first. -1 = no match. */
export function scoreText(hay: string, terms: string[]): number {
  const h = hay.toLowerCase()
  let s = 0
  for (const t of terms) {
    const i = h.indexOf(t)
    if (i < 0) return -1
    s += i === 0 ? 3 : /[\s\-_/.]/.test(h[i - 1]) ? 2 : 1
  }
  return s
}

/** "1 result" / "12 results" / "No results" — the Spotlight live region. */
export function resultCountLabel(n: number): string {
  if (n <= 0) return 'No results'
  return n === 1 ? '1 result' : `${n} results`
}
