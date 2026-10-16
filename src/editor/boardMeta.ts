/**
 * boardMeta.ts — small pure helpers behind the draft bar and the promote summary.
 *
 *   summarizeSpecChanges(from, to)  what differs between two board specs, as a
 *                                   count and a short list of named changes.
 *   describeChanges(summary)        "3 unpushed changes" style copy.
 *   relativeTime(date, now)         "2h ago" for the mono "updated" stamp.
 *
 * Kept free of React and of lib/api so `node --test` can import it.
 */

export type ChangeKind = 'added' | 'removed' | 'edited' | 'moved' | 'board'

export interface SpecChange {
  kind: ChangeKind
  /** Widget id, or the spec key for a board-level change. */
  id: string
  /** Human label: the widget's own label, else its type; or the spec key. */
  label: string
}

export interface ChangeSummary {
  added: number
  removed: number
  edited: number
  moved: number
  /** Board-level differences (title, layout, background, tabs, variables…). */
  board: number
  total: number
  changes: SpecChange[]
}

type Json = Record<string, any>

/** JSON with object keys sorted, so key order never reads as a change. */
export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`
  const keys = Object.keys(value as Json).sort()
  return `{${keys
    .filter(k => (value as Json)[k] !== undefined)
    .map(k => `${JSON.stringify(k)}:${stableStringify((value as Json)[k])}`)
    .join(',')}}`
}

function widgetLabel(w: Json): string {
  const t = w?.title
  const titleText = typeof t === 'string' ? t : typeof t?.text === 'string' ? t.text : ''
  return String(w?.props?.label || titleText || w?.type || 'widget')
}

// Spec keys that describe the board itself. `widgets` is diffed per widget.
const IGNORED_BOARD_KEYS = new Set(['widgets', 'version'])

export function summarizeSpecChanges(from: Json | null | undefined, to: Json | null | undefined): ChangeSummary {
  const a = from ?? {}
  const b = to ?? {}
  const changes: SpecChange[] = []

  const aw = new Map<string, Json>((Array.isArray(a.widgets) ? a.widgets : []).map((w: Json) => [w.id, w]))
  const bw = new Map<string, Json>((Array.isArray(b.widgets) ? b.widgets : []).map((w: Json) => [w.id, w]))

  for (const [id, w] of bw) {
    const before = aw.get(id)
    if (!before) {
      changes.push({ kind: 'added', id, label: widgetLabel(w) })
      continue
    }
    if (stableStringify(before) === stableStringify(w)) continue
    const { pos: beforePos, ...beforeRest } = before
    const { pos: afterPos, ...afterRest } = w
    const onlyMoved =
      stableStringify(beforeRest) === stableStringify(afterRest) &&
      stableStringify(beforePos) !== stableStringify(afterPos)
    changes.push({ kind: onlyMoved ? 'moved' : 'edited', id, label: widgetLabel(w) })
  }
  for (const [id, w] of aw) {
    if (!bw.has(id)) changes.push({ kind: 'removed', id, label: widgetLabel(w) })
  }

  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  for (const key of [...keys].sort()) {
    if (IGNORED_BOARD_KEYS.has(key)) continue
    if (stableStringify(a[key]) !== stableStringify(b[key])) {
      changes.push({ kind: 'board', id: key, label: key })
    }
  }

  const count = (k: ChangeKind) => changes.filter(c => c.kind === k).length
  return {
    added: count('added'),
    removed: count('removed'),
    edited: count('edited'),
    moved: count('moved'),
    board: count('board'),
    total: changes.length,
    changes,
  }
}

/** "3 unpushed changes", "1 unpushed change", "No unpushed changes". */
export function describeChanges(total: number, noun = 'unpushed'): string {
  if (total <= 0) return `No ${noun} changes`
  return `${total} ${noun} ${total === 1 ? 'change' : 'changes'}`
}

/** One line per kind, only the ones that happened: "2 added · 1 edited · 1 moved". */
export function describeBreakdown(s: ChangeSummary): string {
  const parts: string[] = []
  if (s.added) parts.push(`${s.added} added`)
  if (s.edited) parts.push(`${s.edited} edited`)
  if (s.moved) parts.push(`${s.moved} moved`)
  if (s.removed) parts.push(`${s.removed} removed`)
  if (s.board) parts.push(`${s.board} board ${s.board === 1 ? 'setting' : 'settings'}`)
  return parts.join(' · ')
}

/** Short relative stamp for mono meta: "just now", "5m ago", "2h ago", "3d ago", else a date. */
export function relativeTime(input: string | number | Date | null | undefined, now: number = Date.now()): string {
  if (input == null || input === '') return ''
  const t = input instanceof Date ? input.getTime() : new Date(input).getTime()
  if (!Number.isFinite(t)) return ''
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 45) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.round(h / 24)
  if (d < 30) return `${d}d ago`
  return new Date(t).toISOString().slice(0, 10)
}
