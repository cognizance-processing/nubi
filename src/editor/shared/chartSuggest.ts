/**
 * shared/chartSuggest.ts — "which visual fits this result?"
 *
 * Two pure steps, both free of React and of the network so they can be unit
 * tested and reused by any surface that has an Arrow table in hand:
 *
 *   profileTable(table)        — classify each column (measure / temporal /
 *                                category / boolean / id) from its Arrow type
 *                                AND a sample of its values, plus the stats the
 *                                ranker needs (cardinality, sign, monotonicity).
 *
 *   suggestVisuals(profile)    — score every widget type against the profile
 *                                with the usual viz-grammar heuristics and hand
 *                                back a ranked, explained, one-click-applyable
 *                                list. Each entry carries the concrete encoding
 *                                to use, built by encodingFor() below.
 *
 *   encodingFor(type, profile) — profile-aware slot filling for ANY chart
 *                                type (a line wants the date on x, a bar wants
 *                                the category; both were "categorical" to the
 *                                older type-only heuristic).
 *
 * The scores are deliberately coarse (0–100, tens apart) — they only need to
 * order a shortlist, not be probabilities.
 */

import { TYPE_ENCODING } from './chartMeta.jsx'

// ─────────────────────────────────────────────────────────────────────────────
// Column profiling
// ─────────────────────────────────────────────────────────────────────────────

export type ColumnKind = 'measure' | 'temporal' | 'category' | 'boolean' | 'id'

export interface ColumnProfile {
  name: string
  kind: ColumnKind
  /** Arrow type, stringified, for debugging / tests. */
  arrowType: string
  /** Distinct non-null values within the sample. */
  distinct: number
  /** Non-null values within the sample. */
  sampled: number
  nulls: number
  // Measures only
  allPositive?: boolean
  hasNegative?: boolean
  min?: number
  max?: number
  /** Strictly non-increasing across rows (funnel shape). */
  monotonicDown?: boolean
}

export interface ResultProfile {
  rows: number
  columns: ColumnProfile[]
  measures: ColumnProfile[]
  temporals: ColumnProfile[]
  categories: ColumnProfile[]
  ids: ColumnProfile[]
  booleans: ColumnProfile[]
}

/** Rows the profiler looks at — enough for cardinality, cheap on big results. */
const SAMPLE_ROWS = 2000

// Name hints. They only ever tip a decision the values already allow; a column
// named `country` full of floats is still a measure.
const TEMPORAL_NAME = /(^|_)(date|day|week|month|quarter|year|period|time|timestamp|dt)($|_)/i
const ID_NAME = /(^|_)(id|uuid|key|code|sku)$/i
// Strong geo names almost always map to a registered geography; weak ones
// (`region` is also a sales region) get offered, not recommended.
const GEO_STRONG_NAME = /(^|_)(country|nation|province|state|county|iso|geo|continent)($|_)/i
const GEO_WEAK_NAME = /(^|_)(region|territory|district|area|city|suburb)($|_)/i
const RATE_NAME = /(pct|percent|rate|ratio|score|share|utili[sz]ation|completion)/i
const FLOW_SOURCE_NAME = /^(source|from|origin|src)(_|$)/i
const FLOW_TARGET_NAME = /^(target|to|destination|dest|dst)(_|$)/i
// In TYPE_ENCODING slot order, so measureOrder() lands each in its slot.
const OHLC = ['open', 'close', 'low', 'high']
const STAGE_NAME = /(stage|step|phase|funnel|status)/i

// ISO-ish date / datetime / year-month strings, as MySQL/BigQuery hand them
// back once a date axis is CAST to CHAR (see the registered-query gotchas).
const DATE_STRING = /^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$/

export function isNumericType(typeStr: string): boolean {
  return /Int|Float|Decimal/.test(typeStr)
}

function isTemporalType(typeStr: string): boolean {
  return /Date|Timestamp|Time(?!zone)/.test(typeStr)
}

function toNumber(v: unknown): number | null {
  if (v == null) return null
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'bigint') return Number(v)
  // Arrow Decimal values arrive as Uint32Array limbs in some builds; the
  // ranker only needs sign and rough magnitude, so a best-effort Number() is
  // fine and a failure just means "unknown".
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function profileColumn(field: any, vector: any, rows: number): ColumnProfile {
  const name: string = field.name
  const arrowType = String(field.type)
  const limit = Math.min(rows, SAMPLE_ROWS)

  const numeric = isNumericType(arrowType)
  const temporalType = isTemporalType(arrowType)
  const boolType = /Bool/.test(arrowType)

  const seen = new Set<string>()
  let nulls = 0
  let sampled = 0
  let dateLike = 0
  let min = Infinity
  let max = -Infinity
  let hasNegative = false
  let monotonicDown = true
  let prev: number | null = null

  for (let i = 0; i < limit; i++) {
    const v = vector?.get(i)
    if (v == null || v === '') { nulls++; continue }
    sampled++
    if (numeric) {
      const n = toNumber(v)
      if (n != null) {
        if (n < min) min = n
        if (n > max) max = n
        if (n < 0) hasNegative = true
        if (prev != null && n > prev) monotonicDown = false
        prev = n
        seen.add(String(n))
      }
    } else {
      const s = v instanceof Date ? v.toISOString() : String(v)
      seen.add(s)
      if (!temporalType && DATE_STRING.test(s.trim())) dateLike++
    }
  }

  const distinct = seen.size
  let kind: ColumnKind
  if (boolType) {
    kind = 'boolean'
  } else if (temporalType) {
    kind = 'temporal'
  } else if (numeric) {
    // An integer column that is unique per row and named like a key is an
    // identifier, not something to sum. A four-digit integer named year/period
    // is a time axis.
    const integer = /Int/.test(arrowType)
    if (integer && ID_NAME.test(name) && sampled > 1 && distinct === sampled) kind = 'id'
    else if (integer && TEMPORAL_NAME.test(name) && min >= 1900 && max <= 2200) kind = 'temporal'
    else kind = 'measure'
  } else if (sampled > 0 && dateLike / sampled >= 0.9) {
    kind = 'temporal'
  } else if (ID_NAME.test(name) && sampled > 1 && distinct === sampled) {
    kind = 'id'
  } else if (sampled > 0 && distinct <= 2 && [...seen].every(s => /^(true|false|yes|no|0|1)$/i.test(s))) {
    kind = 'boolean'
  } else {
    kind = 'category'
  }

  const profile: ColumnProfile = { name, kind, arrowType, distinct, sampled, nulls }
  if (kind === 'measure' && sampled > 0) {
    profile.min = min
    profile.max = max
    profile.hasNegative = hasNegative
    profile.allPositive = !hasNegative && min >= 0
    profile.monotonicDown = monotonicDown && sampled > 1
  }
  return profile
}

/**
 * Profile an Arrow table. Tolerates `null`/`undefined` (nothing run yet).
 *
 * Reads columns through `getChild(name)` only — that is the whole surface
 * the decimal read-shim in lib/arrowDecimal exposes, and the query runtime
 * hands that shim back (not a bare arrow.Table) whenever a result carries a
 * DECIMAL column.
 */
export function profileTable(table: any): ResultProfile {
  const rows: number = table?.numRows ?? 0
  const fields: any[] = table?.schema?.fields ?? []
  const columns = fields.map(f => profileColumn(f, table.getChild(f.name), rows))
  return {
    rows,
    columns,
    measures:   columns.filter(c => c.kind === 'measure'),
    temporals:  columns.filter(c => c.kind === 'temporal'),
    categories: columns.filter(c => c.kind === 'category'),
    ids:        columns.filter(c => c.kind === 'id'),
    booleans:   columns.filter(c => c.kind === 'boolean'),
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Encoding
// ─────────────────────────────────────────────────────────────────────────────

/** Encoding slots that want a NUMERIC column; everything else takes a dimension. */
export const NUMERIC_ENCODING_KEYS = new Set([
  'y', 'y2', 'value', 'size', 'open', 'close', 'low', 'high', 'lower', 'upper',
])

// Chart types whose x axis is naturally time — prefer a temporal column there.
const TIME_FIRST_TYPES = new Set(['line', 'area', 'candlestick', 'fan', 'combo'])

export interface EncodingResult {
  encoding: Record<string, any>
  /** Required slot labels no column could satisfy. */
  missing: string[]
}

/**
 * Dimension columns in the order a chart type should consume them: time
 * first for time-series types, categories first elsewhere; ids and booleans
 * last as a fallback so a narrow result still draws something.
 */
function dimensionOrder(type: string, p: ResultProfile): string[] {
  const cats = p.categories.map(c => c.name)
  const time = p.temporals.map(c => c.name)
  const rest = [...p.booleans, ...p.ids].map(c => c.name)
  return TIME_FIRST_TYPES.has(type) ? [...time, ...cats, ...rest] : [...cats, ...time, ...rest]
}

function measureOrder(type: string, p: ResultProfile): string[] {
  const names = p.measures.map(c => c.name)
  if (type !== 'candlestick') return names
  // OHLC by name when present, so open/close/low/high land in the right slots.
  const byName = (n: string) => names.find(x => x.toLowerCase() === n || x.toLowerCase().endsWith('_' + n))
  const ohlc = OHLC.map(byName).filter(Boolean) as string[]
  return ohlc.length === 4 ? ohlc : names
}

/**
 * Fill a chart type's encoding slots from a profile.
 *
 * Required slots always get a column (reusing the last candidate when the
 * result is narrower than the chart wants, so a two-column result still draws
 * a bar chart). Optional slots are only filled from a column no other slot has
 * claimed — reusing one there would produce a "grouped by itself" series.
 */
export function encodingFor(type: string, p: ResultProfile): EncodingResult {
  const all = p.columns.map(c => c.name)
  const dims = dimensionOrder(type, p)
  const nums = measureOrder(type, p)

  // Combo has no TYPE_ENCODING entry — it reads column-name arrays instead.
  if (type === 'combo') {
    return {
      encoding: {
        x: dims[0] ?? all[0],
        bars: nums.slice(0, 1),
        lines: nums.length > 1 ? nums.slice(1, 2) : nums.slice(0, 1),
      },
      missing: nums.length ? [] : ['a numeric column'],
    }
  }

  const slots = TYPE_ENCODING[type] ?? [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }]
  const encoding: Record<string, any> = {}
  const missing: string[] = []
  const used = new Set<string>()
  let ni = 0
  let di = 0

  for (const slot of slots) {
    const wantsNumber = NUMERIC_ENCODING_KEYS.has(slot.key)
    const pool = wantsNumber ? nums : dims
    let pick = pool[wantsNumber ? ni : di]

    // Sankey's source/target read better by name when the columns say so.
    if (type === 'sankey' && !wantsNumber) {
      const named = dims.find(d => (slot.key === 'source' ? FLOW_SOURCE_NAME : FLOW_TARGET_NAME).test(d) && !used.has(d))
      if (named) pick = named
    }
    // A colour/group slot on a chart that already put time on x should take a
    // low-cardinality category, never a second date column.
    if (slot.key === 'color' && slot.optional) {
      pick = p.categories.map(c => c.name).find(n => !used.has(n) && distinctOf(p, n) <= 12)
    }

    if (pick !== undefined && !used.has(pick)) {
      if (wantsNumber) ni++
      else di++
    } else if (slot.optional) {
      continue
    } else {
      pick = pool[pool.length - 1] ?? all[wantsNumber ? all.length - 1 : 0]
      // Reusing a NUMERIC column is harmless (a flat candlestick still draws).
      // Reusing a DIMENSION is not: sankey's source/target become a self-loop
      // ECharts rejects outright, and heatmap collapses to one row.
      if (!wantsNumber && (pick === undefined || used.has(pick))) {
        missing.push(slot.label ?? slot.key)
        continue
      }
    }
    if (pick === undefined) {
      missing.push(slot.label ?? slot.key)
      continue
    }
    encoding[slot.key] = pick
    used.add(pick)
  }
  return { encoding, missing }
}

function distinctOf(p: ResultProfile, name: string): number {
  return p.columns.find(c => c.name === name)?.distinct ?? Infinity
}

// ─────────────────────────────────────────────────────────────────────────────
// Ranking
// ─────────────────────────────────────────────────────────────────────────────

export interface Suggestion {
  /** chart_type for charts, or 'kpi' | 'table' | 'pivot'. */
  type: string
  /** Widget type the board would store: 'chart' | 'kpi' | 'table' | 'pivot'. */
  widgetType: 'chart' | 'kpi' | 'table' | 'pivot'
  score: number
  /** One sentence a reader can act on — why this shape fits this data. */
  reason: string
  encoding: Record<string, any>
  /** Widget props to set alongside (KPI label, pivot agg…). */
  props?: Record<string, any>
  /** Chart config to set alongside (orientation…). */
  config?: Record<string, any>
}

const q = (s: string) => `‘${s}’`
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

/**
 * Rank every visual against a profile. Returns best first; anything scoring
 * zero, or whose encoding could not be filled, is dropped.
 *
 * `limit` caps the list; pass `Infinity` to see the whole ranking.
 */
export function suggestVisuals(p: ResultProfile, { limit = 3 }: { limit?: number } = {}): Suggestion[] {
  const out: Suggestion[] = []
  const { rows, measures: M, temporals: T, categories: C } = p
  if (rows === 0 || p.columns.length === 0) return out

  const m0 = M[0]
  const t0 = T[0]
  const c0 = C[0]
  const smallCat = C.filter(c => c.distinct <= 12)
  const geoStrong = C.find(c => GEO_STRONG_NAME.test(c.name) && c.distinct >= 2)
  const geoCat = geoStrong ?? C.find(c => GEO_WEAK_NAME.test(c.name) && c.distinct >= 3)
  const chart = (type: string, score: number, reason: string, extra: Partial<Suggestion> = {}) => {
    if (score <= 0) return
    const { encoding, missing } = encodingFor(type, p)
    if (missing.length) return
    out.push({ type, widgetType: 'chart', score, reason, encoding, ...extra })
  }

  // ── Headline figures ────────────────────────────────────────────────────
  if (rows === 1 && m0) {
    const rateLike = M.find(c => RATE_NAME.test(c.name))
    out.push({
      type: 'kpi', widgetType: 'kpi', score: 95,
      reason: `A single row with ${q(m0.name)} reads best as a headline figure.`,
      encoding: { value: m0.name, spark: M[1]?.name ?? m0.name },
      props: { label: m0.name },
    })
    if (rateLike && rateLike.min != null && rateLike.min >= 0 && (rateLike.max ?? 0) <= 100) {
      chart('gauge', 80, `${q(rateLike.name)} is a single bounded rate — a gauge shows it against its range.`)
    }
  }

  // ── Time series ─────────────────────────────────────────────────────────
  if (t0 && m0 && rows > 1) {
    const series = smallCat[0]
    let lineScore = M.length > 4 ? 70 : 90
    const comboable = M.length >= 2 && scaleRatio(M[0], M[1]) >= 10
    if (comboable) lineScore -= 10
    chart('line', lineScore,
      series
        ? `${q(t0.name)} is a time axis — a line per ${q(series.name)} shows how each trends.`
        : `${q(t0.name)} is a time axis — a line shows ${M.length > 1 ? 'how the measures' : `how ${q(m0.name)}`} trend over time.`)
    if (M.length === 1 && m0.allPositive) {
      chart('area', 65, `Positive ${q(m0.name)} over time — an area chart emphasises volume.`)
    }
    if (comboable) {
      chart('combo', 85, `${q(M[0].name)} and ${q(M[1].name)} are on very different scales — bars and a line on a second axis keep both readable.`)
    }
    if (t0.distinct <= 24 && M.length <= 2 && C.length === 0) {
      chart('bar', 60, `Only ${plural(t0.distinct, 'period')} — bars make period-to-period comparison easy.`)
    }
    if (M.length >= 3 && M.some(c => /lower|low|min|p10|p5/i.test(c.name)) && M.some(c => /upper|high|max|p90|p95/i.test(c.name))) {
      chart('fan', 80, `A midline with lower and upper bounds over time — a fan chart draws the band.`)
    }
    const ohlc = OHLC.every(n => M.some(c => c.name.toLowerCase() === n || c.name.toLowerCase().endsWith('_' + n)))
    if (ohlc) chart('candlestick', 95, `Open / high / low / close per ${q(t0.name)} — a candlestick is the native shape.`)
    if (smallCat[0] && t0.distinct <= 40) {
      chart('heatmap', 55, `${q(smallCat[0].name)} by ${q(t0.name)} — a heatmap shows the matrix at a glance.`)
    }
  }

  // ── One category, one or more measures ──────────────────────────────────
  if (c0 && m0 && rows > 1) {
    const n = c0.distinct
    const rowsPerCat = rows / Math.max(n, 1)
    const repeated = rowsPerCat >= 5
    const group = smallCat.find(c => c !== c0)

    if (!repeated) {
      if (n <= 12) {
        chart('bar', T.length ? 70 : (group ? 82 : 88), group
          ? `Compare ${q(m0.name)} across ${q(c0.name)}, grouped by ${q(group.name)}.`
          : `Compare ${q(m0.name)} across ${plural(n, q(c0.name) + ' value')}.`)
      } else if (n <= 40) {
        chart('bar', 78, `${n} ${q(c0.name)} values — horizontal bars keep every label legible.`, { config: { orientation: 'horizontal' } })
      } else {
        chart('bar', 45, `${n} ${q(c0.name)} values is a lot for bars — consider filtering or a table.`, { config: { orientation: 'horizontal' } })
      }
      if (M.length === 1 && m0.allPositive && !T.length) {
        if (n >= 2 && n <= 6) {
          chart('donut', 72, `${n} parts of a whole — a donut shows the share of each ${q(c0.name)}.`)
          chart('pie', 60, `${n} parts of a whole — a pie shows the share of each ${q(c0.name)}.`)
        }
        if (n > 6) {
          chart('treemap', n > 12 ? 68 : 55, `${n} ${q(c0.name)} values sized by ${q(m0.name)} — a treemap packs them into one tile.`)
        }
        // Monotonic alone is weak evidence (ORDER BY value DESC looks the
        // same); a stage-like name is what makes it a funnel.
        if (n >= 3 && n <= 8 && m0.monotonicDown) {
          const staged = STAGE_NAME.test(c0.name)
          chart('funnel', staged ? 92 : 60, `${q(m0.name)} falls at every ${q(c0.name)} step — a funnel shows the drop-off.`)
        }
      }
      if (M.length === 1 && m0.hasNegative && m0.min != null && m0.max != null && m0.max > 0 && n <= 20) {
        chart('waterfall', 58, `${q(m0.name)} mixes gains and losses — a waterfall shows how they add up.`)
      }
      if (n >= 3 && n <= 10 && (M.length >= 3 || group)) {
        chart('radar', 40, `${n} indicators${group ? ` per ${q(group.name)}` : ''} — a radar compares their profile.`)
      }
      if (geoCat) {
        chart('map', geoStrong ? 84 : 50, `${q(geoCat.name)} looks like a place — colour it on a map by ${q(m0.name)}.`)
      }
    } else {
      chart('boxplot', 75, `About ${Math.round(rowsPerCat)} rows per ${q(c0.name)} — a box plot shows the spread of ${q(m0.name)}.`)
      chart('bar', 40, `Bars will aggregate the ${Math.round(rowsPerCat)} rows per ${q(c0.name)}.`)
    }
  }

  // ── Two dimensions and a measure ────────────────────────────────────────
  const dims2 = [...C, ...T].filter(c => c.distinct <= 30)
  if (dims2.length >= 2 && m0 && rows > 1) {
    const [a, b] = dims2
    if (!(T.length && C.length === 0)) {
      // A handful of cells reads fine as grouped bars; a real matrix doesn't.
      const cells = a.distinct * b.distinct
      chart('heatmap', cells >= 12 ? 86 : 68, `${q(a.name)} × ${q(b.name)} with ${q(m0.name)} — a heatmap shows the whole ${a.distinct}×${b.distinct} matrix.`)
    }
    if (C.length >= 2) {
      const src = C.find(c => FLOW_SOURCE_NAME.test(c.name))
      const dst = C.find(c => FLOW_TARGET_NAME.test(c.name))
      const flow = src && dst ? 92 : 30
      chart('sankey', flow, src && dst
        ? `${q(src.name)} → ${q(dst.name)} with a value — a sankey draws the flow.`
        : `Two categories and a value could be drawn as a flow.`)
      out.push({
        type: 'pivot', widgetType: 'pivot', score: 62,
        reason: `${q(C[0].name)} down, ${q(C[1].name)} across, ${q(m0.name)} summed in the cells.`,
        encoding: { rows: C[0].name, cols: C[1].name, value: m0.name },
        props: { agg: 'sum' },
      })
    }
  }

  // ── Measure vs measure ──────────────────────────────────────────────────
  if (M.length >= 2 && !t0 && rows >= 8) {
    const group = smallCat[0]
    const base = C.length === 0 ? 86 : 70
    chart('scatter', base, `${q(M[0].name)} against ${q(M[1].name)} — a scatter shows whether they move together${group ? `, coloured by ${q(group.name)}` : ''}.`)
    if (M.length >= 3) {
      chart('bubble', base - 8, `A third measure, ${q(M[2].name)}, can size each point.`)
    }
  }

  // ── The honest fallback ─────────────────────────────────────────────────
  const wide = C.length + T.length >= 3 || M.length >= 4
  out.push({
    type: 'table', widgetType: 'table',
    score: wide ? 74 : (m0 ? 35 : 60),
    reason: wide
      ? `${plural(p.columns.length, 'column')} — a table keeps every field readable.`
      : m0 ? `Every value, exactly as returned.` : `No numeric column to plot — a table shows the rows as they are.`,
    encoding: {},
  })

  // Dedupe (a type can be nominated twice, e.g. bar), keep the higher score.
  const best = new Map<string, Suggestion>()
  for (const s of out) {
    const prev = best.get(s.type)
    if (!prev || s.score > prev.score) best.set(s.type, s)
  }
  return [...best.values()]
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
}

/** Ratio between two measures' magnitudes (≥1). Unknown → 1. */
function scaleRatio(a: ColumnProfile, b: ColumnProfile): number {
  const ma = Math.max(Math.abs(a.min ?? 0), Math.abs(a.max ?? 0))
  const mb = Math.max(Math.abs(b.min ?? 0), Math.abs(b.max ?? 0))
  if (!ma || !mb) return 1
  return ma > mb ? ma / mb : mb / ma
}
