/**
 * failure.ts — turn a raw widget error string into something a viewer can read.
 *
 * A widget's `error` is whatever the engine said: a driver message with host,
 * port and sometimes a stack of qualifiers. That is useful to the person who
 * owns the board and noise (or a leak) to everyone else, so the tile shows a
 * short, human message to every viewer and keeps the raw text behind a
 * "Details" disclosure that only editors get.
 *
 * Two jobs live here, both pure so they can be unit-tested:
 *
 *   classifyFailure(message)  → { cause, label }   which kind of failure it is
 *   groupFailures(entries)    → groups sharing one cause, so a board whose
 *                               warehouse is down can say so ONCE instead of
 *                               drawing six identical error tiles.
 */

export type FailureCause = 'connection' | 'timeout' | 'permission' | 'missing' | 'sql' | 'unknown'

export interface Failure {
  cause: FailureCause
  /** Short sentence about the cause, safe to show to anyone. No hosts or ports. */
  label: string
  /** Stable key two failures share when they have the same root cause. */
  key: string
}

const RULES: Array<{ cause: FailureCause; label: string; test: RegExp }> = [
  {
    cause: 'timeout',
    label: 'The data source took too long to answer.',
    test: /time(d)?[ -]?out|deadline exceeded|statement timeout|took too long|canceling statement/i,
  },
  {
    cause: 'connection',
    label: "The data source couldn't be reached.",
    test: /can'?t connect|cannot connect|could not connect|connection (refused|reset|closed|lost|error|failed)|econn(refused|reset)|unreachable|name or service not known|no route to host|network (error|is)|failed to fetch|\(2003|\(2006|\(2013|08001|08006/i,
  },
  {
    cause: 'permission',
    label: 'Access to this data was refused.',
    test: /permission denied|access denied|forbidden|unauthori[sz]ed|not authori[sz]ed|insufficient privileges|\b40[13]\b|row[- ]level/i,
  },
  {
    cause: 'missing',
    label: 'A query or table this tile depends on is missing.',
    test: /no registered query|no such (table|column)|does not exist|doesn'?t exist|not found|unknown (table|column|database)|catalog error|\b404\b/i,
  },
  {
    cause: 'sql',
    label: 'The query returned an error.',
    test: /syntax|parse error|parser error|binder error|column count|mismatch|invalid (sql|query|column|identifier)|conversion error|division by zero|\bsql\b.*(error|failed)|query failed/i,
  },
]

/** Strip the parts of a message that differ between tiles that share a cause. */
export function normaliseMessage(message: string): string {
  return String(message ?? '')
    .toLowerCase()
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, '#')
    .replace(/\b[0-9a-f]{16,}\b/g, '#')
    .replace(/\d+/g, '#')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 96)
}

export function classifyFailure(message: string | null | undefined): Failure {
  const text = String(message ?? '')
  for (const rule of RULES) {
    if (rule.test.test(text)) {
      return { cause: rule.cause, label: rule.label, key: rule.cause }
    }
  }
  return {
    cause: 'unknown',
    label: 'The query failed.',
    // Unclassified failures only group when the engine said the same thing.
    key: `unknown:${normaliseMessage(text)}`,
  }
}

/** A request id quoted inside an engine message, e.g. "request id: req_8f31c2a9". */
export function extractRequestId(message: string | null | undefined): string | null {
  const text = String(message ?? '')
  const labelled = /request[ _-]?id[\s:=#]+([A-Za-z0-9][A-Za-z0-9_-]{5,})/i.exec(text)
  if (labelled) return labelled[1]
  const bare = /\b(req_[A-Za-z0-9]{4,})\b/.exec(text)
  return bare ? bare[1] : null
}

/** Noun for the viewer message, by widget type. */
export function tileNoun(kind: string | null | undefined): string {
  switch (kind) {
    case 'table': return 'table'
    case 'pivot': return 'pivot'
    case 'kpi':
    case 'metric': return 'number'
    case 'chart': return 'chart'
    default: return 'tile'
  }
}

export function viewerMessage(kind: string | null | undefined): string {
  return `This ${tileNoun(kind)} couldn't load.`
}

export interface FailureEntry {
  id: string
  key: string
  cause: FailureCause
  label: string
  message: string
}

export interface FailureGroup {
  key: string
  cause: FailureCause
  label: string
  count: number
  /** The engine message of the first tile in the group (editors only). */
  sample: string
  ids: string[]
}

/** Minimum tiles sharing a cause before the board gets its own banner. */
export const GROUP_THRESHOLD = 3

/** Groups of ≥ `threshold` entries that share a cause, biggest first. */
export function groupFailures(entries: FailureEntry[], threshold = GROUP_THRESHOLD): FailureGroup[] {
  const byKey = new Map<string, FailureGroup>()
  for (const e of entries) {
    const g = byKey.get(e.key)
    if (g) {
      g.count += 1
      g.ids.push(e.id)
    } else {
      byKey.set(e.key, {
        key: e.key, cause: e.cause, label: e.label, count: 1, sample: e.message, ids: [e.id],
      })
    }
  }
  return [...byKey.values()].filter(g => g.count >= threshold).sort((a, b) => b.count - a.count)
}
