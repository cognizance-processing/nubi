// Chart options that came from a board spec are data. ECharts renders some of
// them as HTML or CSS in the viewer's page (tooltips, the toolbox data view,
// title links), so those values are removed before an option is drawn.
//
// One implementation, used by the browser option builder
// (embed/widgets/chart-options.ts) and by the server-side renderer in this
// directory. backend/app/dashboards/spec.py applies the same rules to stored
// specs — keep the two in step.
import { safeColor } from './colors.mjs'

/** Objects nested deeper than this are dropped rather than left unchecked. */
export const MAX_OPTION_DEPTH = 64

const TOOLTIP_HTML_SINKS = [
  'extraCssText', 'className', 'renderMode', 'appendTo', 'appendToBody', 'enterable', 'alwaysShowContent',
]
const TOOLTIP_POSITIONS = /^(inside|top|left|right|bottom)$/

function isPlainObject(v) {
  return v != null && typeof v === 'object' && !Array.isArray(v)
}

/** A tooltip keeps its look-and-feel values only. Functions are ours (a spec is JSON). */
function scrubTooltip(tip) {
  if (typeof tip.formatter !== 'function') delete tip.formatter
  if (typeof tip.content !== 'function') delete tip.content
  if (typeof tip.position === 'string' && !TOOLTIP_POSITIONS.test(tip.position)) delete tip.position
  for (const sink of TOOLTIP_HTML_SINKS) delete tip[sink]
  for (const c of ['backgroundColor', 'borderColor']) {
    if (typeof tip[c] === 'string' && !safeColor(tip[c])) delete tip[c]
  }
  if (isPlainObject(tip.textStyle)) {
    delete tip.textStyle.rich
    delete tip.textStyle.formatter
    if (typeof tip.textStyle.color === 'string' && !safeColor(tip.textStyle.color)) delete tip.textStyle.color
  }
}

/** Toolbox features: the data view is an HTML panel; `lang` strings are written as markup. */
function scrubToolboxFeatures(features) {
  delete features.dataView
  for (const feature of Object.values(features)) {
    if (isPlainObject(feature)) delete feature.lang
  }
}

/**
 * Remove, in place, everything in *node* that would be rendered as HTML or CSS.
 * Applies at every depth and to list-form components; anything nested past
 * MAX_OPTION_DEPTH is removed instead of being passed through unchecked.
 */
export function scrubRenderedHtml(node, depth = 0) {
  if (node == null || typeof node !== 'object') return node
  const tooDeep = (v) => v != null && typeof v === 'object' && depth + 1 > MAX_OPTION_DEPTH
  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i++) {
      if (tooDeep(node[i])) node[i] = null
      else scrubRenderedHtml(node[i], depth + 1)
    }
    return node
  }
  for (const key of Object.keys(node)) {
    const value = node[key]
    if (tooDeep(value) || key === 'dataView') {
      delete node[key]
      continue
    }
    if (key === 'tooltip') {
      for (const tip of Array.isArray(value) ? value : [value]) {
        if (isPlainObject(tip)) scrubTooltip(tip)
      }
    } else if (key === 'feature' && isPlainObject(value)) {
      scrubToolboxFeatures(value)
    } else if ((key === 'link' || key === 'sublink') && typeof value === 'string' && !/^https?:\/\//i.test(value.trim())) {
      delete node[key]
      continue
    }
    scrubRenderedHtml(value, depth + 1)
  }
  return node
}
