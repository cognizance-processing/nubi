/**
 * validateSpec.js — client-side structural validation for a DashboardSpec.
 *
 * Mirrors the backend validator in `backend/app/dashboards/spec.py`
 * (`validate_spec`) so the Code panel can reject broken specs *before* they
 * are applied to the editor or sent to the server. The server remains the
 * authority (POST /import re-validates), this is the fast inline pass.
 *
 * Rules mirrored from the backend
 * -------------------------------
 *  1. `title` must be a non-empty string.
 *  2. `widgets` must be an array; each widget needs a non-empty unique `id`,
 *     a known `type`, and a `pos` of integers {x,y,w,h} all >= 1.
 *  3. chart widgets need `chart_type` (known variant) and `encoding.x` / `.y`.
 *  4. filter widgets need `subtype` (known variant) and `target_var`.
 *  5. text widgets need `content`.
 *  6. `params` values of shape `{ref: '<var>'}` must reference a declared
 *     variable name.
 *  7. `variables` entries need a `name` and a known `type`.
 *
 * The backend's query-registry check (unknown query_id) is a *soft* warning
 * server-side and needs a network round-trip, so it is intentionally not
 * replicated here.
 *
 * @param {unknown} spec — parsed candidate spec (plain object expected).
 * @returns {string[]} issues — empty when the spec is structurally valid.
 */

const WIDGET_TYPES = new Set([
  'kpi', 'table', 'chart', 'filter', 'text',
  // Extended types rendered by the frontend SpecRenderer:
  'metric', 'pivot', 'section', 'html',
])

const CHART_TYPES = new Set(['line', 'bar', 'scatter', 'area', 'pie'])
const FILTER_SUBTYPES = new Set(['select', 'multiselect', 'daterange', 'text'])
const VARIABLE_TYPES = new Set(['text', 'number', 'date', 'daterange', 'select', 'multiselect'])

function isPlainObject(v) {
  return v != null && typeof v === 'object' && !Array.isArray(v)
}

function isPosInt(v) {
  return Number.isInteger(v) && v >= 1
}

export function validateDashboardSpec(spec) {
  const issues = []

  if (!isPlainObject(spec)) {
    return ['Spec must be an object (got ' + (Array.isArray(spec) ? 'array' : typeof spec) + ').']
  }

  // ── title ────────────────────────────────────────────────────────────────
  if (typeof spec.title !== 'string' || spec.title.trim() === '') {
    issues.push("Field 'title': a non-empty string title is required.")
  }

  // ── variables ────────────────────────────────────────────────────────────
  const declaredVars = new Set()
  if (spec.variables != null) {
    if (!Array.isArray(spec.variables)) {
      issues.push("Field 'variables': must be an array.")
    } else {
      spec.variables.forEach((v, i) => {
        if (!isPlainObject(v)) {
          issues.push(`Variable #${i + 1}: must be an object.`)
          return
        }
        if (typeof v.name !== 'string' || v.name === '') {
          issues.push(`Variable #${i + 1}: 'name' is required.`)
        } else {
          declaredVars.add(v.name)
        }
        if (!VARIABLE_TYPES.has(v.type)) {
          issues.push(
            `Variable '${v.name ?? `#${i + 1}`}': type must be one of ${[...VARIABLE_TYPES].join(' | ')}.`,
          )
        }
      })
    }
  }

  // ── widgets ──────────────────────────────────────────────────────────────
  if (spec.widgets != null && !Array.isArray(spec.widgets)) {
    issues.push("Field 'widgets': must be an array.")
    return issues
  }

  const widgets = Array.isArray(spec.widgets) ? spec.widgets : []
  const seenIds = new Set()

  widgets.forEach((w, i) => {
    const label = `Widget ${typeof w?.id === 'string' && w.id ? `'${w.id}'` : `#${i + 1}`}`

    if (!isPlainObject(w)) {
      issues.push(`Widget #${i + 1}: must be an object.`)
      return
    }

    // id — non-empty, unique
    if (typeof w.id !== 'string' || w.id === '') {
      issues.push(`Widget #${i + 1}: 'id' is required and must be a non-empty string.`)
    } else if (seenIds.has(w.id)) {
      issues.push(`Duplicate widget id '${w.id}' — widget ids must be unique.`)
    } else {
      seenIds.add(w.id)
    }

    // pos {x,y,w,h} — integers >= 1 (required regardless of type)
    if (!isPlainObject(w.pos)) {
      issues.push(`${label}: 'pos' {x,y,w,h} is required.`)
    } else {
      for (const k of ['x', 'y', 'w', 'h']) {
        if (!isPosInt(w.pos[k])) {
          issues.push(`${label}: pos.${k} must be an integer >= 1.`)
        }
      }
    }

    // type
    if (!WIDGET_TYPES.has(w.type)) {
      issues.push(`${label}: unknown type '${w.type}' — expected one of ${[...WIDGET_TYPES].join(' | ')}.`)
      return // further type-specific checks are meaningless
    }

    // chart requirements
    if (w.type === 'chart') {
      if (!CHART_TYPES.has(w.chart_type)) {
        issues.push(`${label} (chart): 'chart_type' is required (${[...CHART_TYPES].join(' | ')}).`)
      }
      const enc = isPlainObject(w.encoding) ? w.encoding : {}
      if (!enc.x) issues.push(`${label} (chart): encoding must include an 'x' column.`)
      if (!enc.y) issues.push(`${label} (chart): encoding must include a 'y' column.`)
    }

    // filter requirements
    if (w.type === 'filter') {
      if (!FILTER_SUBTYPES.has(w.subtype)) {
        issues.push(`${label} (filter): 'subtype' is required (${[...FILTER_SUBTYPES].join(' | ')}).`)
      }
      if (typeof w.target_var !== 'string' || w.target_var === '') {
        issues.push(`${label} (filter): 'target_var' is required.`)
      }
    }

    // text requirements
    if (w.type === 'text' && (typeof w.content !== 'string' || w.content === '')) {
      issues.push(`${label} (text): 'content' is required.`)
    }

    // params {name: {ref:'var'} | literal} — refs must be declared
    if (w.params != null) {
      if (!isPlainObject(w.params)) {
        issues.push(`${label}: 'params' must be an object.`)
      } else {
        for (const [pName, pVal] of Object.entries(w.params)) {
          if (isPlainObject(pVal) && 'ref' in pVal && !declaredVars.has(pVal.ref)) {
            issues.push(
              `${label} param '${pName}': ref '${pVal.ref}' is not declared in spec 'variables'.`,
            )
          }
        }
      }
    }
  })

  return issues
}

export default validateDashboardSpec
