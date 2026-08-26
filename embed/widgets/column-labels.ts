/**
 * columnLabels.js — how a result column is titled in a table header.
 *
 * A query returns `gross_margin_pct`; a reader wants "Gross margin %". Until
 * now the grid printed the raw column name, and the only rename that existed
 * anywhere applied to the Excel export — so every board on screen showed its
 * SQL identifiers. That is the difference between a query result and a report.
 *
 * Two layers, in order:
 *
 *   1. `widget.columnLabels` — an explicit `{column: 'Label'}` map. Always wins,
 *      including over the humanised default, and including for a label the
 *      author deliberately wants lowercase or abbreviated.
 *   2. `humaniseColumn()` — a predictable default so a board looks right with no
 *      authoring at all. Snake and camel case both split; a small vocabulary of
 *      abbreviations that are noise when spelled out (`pct`, `qty`, `id`) is
 *      expanded or symbolised; the result is sentence case, not Title Case,
 *      because a column header is a label rather than a heading.
 *
 * Pure — no DOM, no React — so it is testable under the node runner like
 * legacyWidgetText.js and metricBlock.logic.js.
 */

/**
 * Fragments that read worse spelled out, or that have a conventional symbol.
 * Applied per word, after splitting.
 */
const VOCAB: Record<string, string> = {
  pct: '%',
  percent: '%',
  avg: 'average',
  qty: 'quantity',
  amt: 'amount',
  num: 'number',
  id: 'ID',
  ids: 'IDs',
  sku: 'SKU',
  kpi: 'KPI',
  mtd: 'MTD',
  ytd: 'YTD',
  yoy: 'YoY',
  usd: 'USD',
  eur: 'EUR',
  gbp: 'GBP',
  zar: 'ZAR',
}

/** Split `snake_case`, `camelCase` and `kebab-case` into lowercase words. */
export function splitWords(name: string): string[] {
  return String(name ?? '')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[\s_\-.]+/)
    .filter(Boolean)
    .map((w) => w.toLowerCase())
}

/**
 * Turn a column name into a readable header.
 *
 * Sentence case: only the first word is capitalised, so a header reads as a
 * label beside its neighbours rather than shouting. Acronyms keep their case
 * because `VOCAB` supplies them already-cased. A trailing `%` attaches to the
 * previous word ("Gross margin %") rather than standing as its own word.
 */
export function humaniseColumn(name: string): string {
  const words = splitWords(name)
  if (!words.length) return String(name ?? '')

  const mapped = words.map((w) => VOCAB[w] ?? w)

  // Capitalise the first word that carries letters — a leading '%' would
  // otherwise swallow the capital.
  const out: string[] = []
  let capitalised = false
  for (const w of mapped) {
    if (!capitalised && /[a-z]/.test(w)) {
      out.push(w.charAt(0).toUpperCase() + w.slice(1))
      capitalised = true
    } else {
      out.push(w)
    }
  }

  return out
    .join(' ')
    .replace(/\s+%/g, ' %')      // keep one space before a symbol
    .replace(/\s{2,}/g, ' ')
    .trim()
}

/**
 * Resolve the header for one column: an explicit label wins, else humanised.
 *
 * An explicit empty string is honoured — that is how an author hides a header
 * on a column whose meaning is obvious from its values.
 */
export function columnLabel(name: string, labels?: Record<string, string> | null): string {
  if (labels && Object.prototype.hasOwnProperty.call(labels, name)) {
    const v = labels[name]
    if (typeof v === 'string') return v
  }
  return humaniseColumn(name)
}
