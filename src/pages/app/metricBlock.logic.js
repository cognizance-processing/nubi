/**
 * metricBlock.logic.js — pure, side-effect-free logic for the query editor's
 * "Expose as metric" panel.
 *
 * Extracted so it can be unit-tested with `node --test` (no React / jsdom). The
 * panel component owns React state + rendering; every function here is
 * deterministic given its inputs.
 *
 * The `config.metric` block is the unification contract (see
 * docs/query-metric-unification.md §1): a query's `config` gains an OPTIONAL
 * `metric` key. Present ⇒ the query is a governed metric. Shape:
 *
 *   {
 *     slug, measure:{ name, agg, expr, type, format },
 *     dimensions:[{ name, expr, type }],
 *     time_dimension:{ column, grains[], default_grain } | null,
 *     default_filters:[], rls_keys:[], owner, description
 *   }
 *
 * The base-grain rule: `config.sql` MUST be authored at base/low grain (select
 * the dimension + raw measure columns, NO GROUP BY); the metric layer owns the
 * aggregation.
 */

// ---------------------------------------------------------------------------
// Vocabularies (mirror app/metrics/models.py + the legacy MetricsPage form)
// ---------------------------------------------------------------------------

export const AGG_FUNCS = ['sum', 'avg', 'min', 'max', 'count', 'count_distinct']
export const MEASURE_TYPES = ['additive', 'semi_additive', 'non_additive']
export const MEASURE_FORMATS = ['number', 'currency', 'percent']
export const DIM_TYPES = ['text', 'number', 'bool', 'date', 'timestamp']
export const ALL_GRAINS = ['hour', 'day', 'week', 'month', 'quarter', 'year']

// ---------------------------------------------------------------------------
// Slug derivation
// ---------------------------------------------------------------------------

/**
 * Derive a stable, URL/identifier-safe slug from a free-text name.
 *   "Total Revenue (ZAR)" → "total_revenue_zar"
 * Lowercase, non-alphanumerics collapsed to single underscores, trimmed of
 * leading/trailing underscores. Returns '' for empty/garbage input.
 *
 * @param {string} name
 * @returns {string}
 */
export function deriveSlug(name) {
  return String(name ?? '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

/** A valid slug is a non-empty lowercase [a-z0-9_] token not starting with a digit. */
export function isValidSlug(slug) {
  return /^[a-z][a-z0-9_]*$/.test(String(slug ?? ''))
}

// ---------------------------------------------------------------------------
// Blank draft
// ---------------------------------------------------------------------------

/**
 * A blank metric-panel draft. `enabled` gates whether a `config.metric` block is
 * written at all (disabled ⇒ plain query, no block).
 */
export function blankMetricDraft() {
  return {
    enabled: false,
    slug: '',
    slugEdited: false, // once true, deriveSlug no longer auto-overwrites it
    description: '',
    measure: { name: '', agg: 'sum', expr: '*', type: 'additive', format: 'number' },
    dimensions: [],
    hasTime: false,
    time: { column: '', grains: [...ALL_GRAINS], default_grain: 'day' },
    rls_keys: [],
    default_filters: [],
  }
}

// ---------------------------------------------------------------------------
// config.metric → draft (parse)
// ---------------------------------------------------------------------------

/**
 * Normalise a persisted `config.metric` block into the panel draft. When the
 * block is absent/empty the draft is blank+disabled (a plain query).
 *
 * @param {object|null|undefined} metric  the `config.metric` block
 * @param {string} [fallbackName]  query name — seeds the slug if the block has none
 * @returns {object} draft
 */
export function metricToDraft(metric, fallbackName = '') {
  if (!metric || typeof metric !== 'object') {
    const base = blankMetricDraft()
    // Pre-seed a slug suggestion from the query name (not yet "edited").
    base.slug = deriveSlug(fallbackName)
    return base
  }
  const td = metric.time_dimension
  const slug = metric.slug ? String(metric.slug) : deriveSlug(metric.measure?.name || fallbackName)
  return {
    enabled: true,
    slug,
    slugEdited: true, // a persisted slug is authoritative — don't auto-rewrite it
    description: metric.description ?? '',
    measure: {
      name: metric.measure?.name ?? '',
      agg: metric.measure?.agg ?? 'sum',
      expr: metric.measure?.expr ?? '*',
      type: metric.measure?.type ?? 'additive',
      format: metric.measure?.format ?? 'number',
    },
    dimensions: Array.isArray(metric.dimensions)
      ? metric.dimensions.map(d => ({
          name: d?.name ?? '',
          expr: d?.expr ?? '',
          type: d?.type ?? 'text',
        }))
      : [],
    hasTime: Boolean(td?.column),
    time: {
      column: td?.column ?? '',
      grains: Array.isArray(td?.grains) && td.grains.length ? td.grains : [...ALL_GRAINS],
      default_grain: td?.default_grain ?? 'day',
    },
    rls_keys: Array.isArray(metric.rls_keys) ? metric.rls_keys.filter(Boolean) : [],
    default_filters: Array.isArray(metric.default_filters) ? metric.default_filters : [],
  }
}

// ---------------------------------------------------------------------------
// draft → config.metric (build)
// ---------------------------------------------------------------------------

/**
 * Build the `config.metric` block from a panel draft, or null when the panel is
 * disabled (⇒ no block written; the query stays a plain query).
 *
 * @param {object} draft
 * @returns {object|null}
 */
export function draftToMetricBlock(draft) {
  if (!draft || !draft.enabled) return null
  const slug = (draft.slugEdited ? draft.slug : draft.slug || deriveSlug(draft.measure?.name)) || ''
  const block = {
    slug: slug.trim(),
    measure: {
      name: (draft.measure?.name ?? '').trim(),
      agg: draft.measure?.agg ?? 'sum',
      expr: (draft.measure?.expr ?? '').trim() || '*',
      type: draft.measure?.type ?? 'additive',
      format: draft.measure?.format ?? 'number',
    },
    dimensions: (draft.dimensions ?? [])
      .filter(d => (d?.name ?? '').trim())
      .map(d => ({
        name: d.name.trim(),
        expr: (d.expr ?? '').trim() || null,
        type: d.type ?? 'text',
      })),
    time_dimension:
      draft.hasTime && (draft.time?.column ?? '').trim()
        ? {
            column: draft.time.column.trim(),
            grains: draft.time.grains?.length ? draft.time.grains : [...ALL_GRAINS],
            default_grain: draft.time.default_grain ?? 'day',
          }
        : null,
    default_filters: Array.isArray(draft.default_filters) ? draft.default_filters : [],
    rls_keys: (draft.rls_keys ?? []).map(k => String(k).trim()).filter(Boolean),
    owner: null,
    description: (draft.description ?? '').trim(),
  }
  return block
}

// ---------------------------------------------------------------------------
// Validation (light, client-side)
// ---------------------------------------------------------------------------

/**
 * Validate a draft. Returns a map of { field: message } — empty ⇒ valid. A
 * disabled panel is always valid (no block is written).
 *
 * @param {object} draft
 * @returns {Record<string,string>}
 */
export function validateMetricDraft(draft) {
  const errors = {}
  if (!draft || !draft.enabled) return errors

  const measureName = (draft.measure?.name ?? '').trim()
  if (!measureName) {
    errors.measureName = 'Measure name is required.'
  }

  const slug = (draft.slugEdited ? draft.slug : draft.slug || deriveSlug(draft.measure?.name)) || ''
  if (!slug.trim()) {
    errors.slug = 'A slug is required (the stable metric id).'
  } else if (!isValidSlug(slug.trim())) {
    errors.slug = 'Slug must be lowercase letters, digits and underscores, starting with a letter.'
  }

  const agg = draft.measure?.agg
  const expr = (draft.measure?.expr ?? '').trim()
  if (agg && agg !== 'count' && !expr) {
    errors.expr = 'Expression (column) is required for this aggregation.'
  }

  return errors
}
