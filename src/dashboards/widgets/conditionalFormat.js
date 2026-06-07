/**
 * conditionalFormat.js — Pure conditional-formatting + value-formatting logic.
 *
 * No DOM, no React, no side effects. Fully unit-testable in Node.
 *
 * ── Rule shape ───────────────────────────────────────────────────────────────
 * {
 *   column   : string          // column name the rule tests
 *   op       : 'eq'|'ne'|'gt'|'gte'|'lt'|'lte'|'between'|'contains'
 *   value    : any             // primary comparison value
 *   value2?  : any             // upper bound for 'between'
 *   style    : {               // CSS-in-JS style fragment to apply
 *     backgroundColor? : string
 *     color?           : string
 *     fontWeight?      : string|number
 *   }
 *   scope    : 'cell'|'row'    // 'cell' → apply to that cell only;
 *                              // 'row'  → apply to the entire row
 * }
 *
 * ── Format shape ─────────────────────────────────────────────────────────────
 * {
 *   type     : 'number'|'date'|'currency'|'percent'
 *   // additional Intl options forwarded directly
 *   locale?  : string          // default 'en-US'
 *   currency? : string         // for type:'currency', default 'USD'
 *   decimals? : number         // explicit fraction digits
 *   dateStyle?: string         // 'short'|'medium'|'long'|'full' (date)
 *   timeStyle?: string         // optional time portion
 * }
 *
 * ── Exports ──────────────────────────────────────────────────────────────────
 * evalRules(rules, row, columns) → { cellStyles: {col: style}, rowStyle: style|null }
 * formatValue(value, fmt)        → string
 */

// ---------------------------------------------------------------------------
// Operator evaluation
// ---------------------------------------------------------------------------

/**
 * Cast a value for numeric comparison. Returns the value coerced to a number,
 * or NaN if not representable.
 */
function toNum(v) {
  if (v == null) return NaN
  const n = Number(v)
  return n
}

/**
 * Evaluate a single rule against a cell value.
 * Returns true if the rule condition matches.
 *
 * @param {*} cellValue  – raw value from the row object
 * @param {string} op    – operator
 * @param {*} value      – primary operand
 * @param {*} value2     – secondary operand (between upper bound)
 * @returns {boolean}
 */
function matchesOp(cellValue, op, value, value2) {
  switch (op) {
    case 'eq':
      // loose equality so '42' == 42 when appropriate; use == intentionally
      // eslint-disable-next-line eqeqeq
      return cellValue == value

    case 'ne':
      // eslint-disable-next-line eqeqeq
      return cellValue != value

    case 'gt':
      return toNum(cellValue) > toNum(value)

    case 'gte':
      return toNum(cellValue) >= toNum(value)

    case 'lt':
      return toNum(cellValue) < toNum(value)

    case 'lte':
      return toNum(cellValue) <= toNum(value)

    case 'between': {
      const n = toNum(cellValue)
      return n >= toNum(value) && n <= toNum(value2)
    }

    case 'contains': {
      if (cellValue == null) return false
      return String(cellValue).toLowerCase().includes(String(value).toLowerCase())
    }

    default:
      return false
  }
}

// ---------------------------------------------------------------------------
// Rule evaluator
// ---------------------------------------------------------------------------

/**
 * Evaluate all formatting rules against a single row and return the computed
 * cell styles and an optional row style.
 *
 * Rules are applied in order; later rules win (last-writer-wins merge per cell).
 * Row-scope rules merge into rowStyle (same last-writer-wins).
 *
 * @param {Array}  rules   – array of rule objects (see file header)
 * @param {object} row     – plain key→value row object
 * @param {string[]} columns – columns present in the display (unused by eval
 *                            but kept for future column-existence guards)
 * @returns {{ cellStyles: Record<string, object>, rowStyle: object|null }}
 */
export function evalRules(rules, row, columns) {
  if (!rules || rules.length === 0) {
    return { cellStyles: {}, rowStyle: null }
  }

  const cellStyles = {}
  let rowStyle = null

  for (const rule of rules) {
    const { column, op, value, value2, style, scope } = rule
    if (!column || !op || !style) continue

    const cellValue = row[column]
    if (!matchesOp(cellValue, op, value, value2)) continue

    if (scope === 'row') {
      rowStyle = rowStyle ? { ...rowStyle, ...style } : { ...style }
    } else {
      // default scope is 'cell'
      cellStyles[column] = cellStyles[column]
        ? { ...cellStyles[column], ...style }
        : { ...style }
    }
  }

  return { cellStyles, rowStyle }
}

// ---------------------------------------------------------------------------
// Value formatter
// ---------------------------------------------------------------------------

/**
 * Format a raw value according to a format descriptor.
 *
 * Supported format types:
 *   'number'   – Intl.NumberFormat with optional decimals
 *   'currency' – Intl.NumberFormat style:'currency', currency:'USD' (or fmt.currency)
 *   'percent'  – Intl.NumberFormat style:'percent', value is already a ratio (0–1)
 *   'date'     – Intl.DateTimeFormat; value may be a Date, ISO string, or epoch ms
 *
 * If value is null/undefined, returns '—'.
 * If fmt is null/undefined, returns String(value).
 *
 * @param {*}      value – raw cell value
 * @param {object} fmt   – format descriptor { type, locale?, ...opts }
 * @returns {string}
 */
export function formatValue(value, fmt) {
  if (value == null) return '—'
  if (!fmt || !fmt.type) return String(value)

  const locale = fmt.locale ?? 'en-US'

  try {
    switch (fmt.type) {
      case 'number': {
        const opts = {}
        if (fmt.decimals != null) {
          opts.minimumFractionDigits = fmt.decimals
          opts.maximumFractionDigits = fmt.decimals
        }
        return new Intl.NumberFormat(locale, opts).format(Number(value))
      }

      case 'currency': {
        const opts = {
          style: 'currency',
          currency: fmt.currency ?? 'USD',
        }
        if (fmt.decimals != null) {
          opts.minimumFractionDigits = fmt.decimals
          opts.maximumFractionDigits = fmt.decimals
        }
        return new Intl.NumberFormat(locale, opts).format(Number(value))
      }

      case 'percent': {
        const opts = {
          style: 'percent',
        }
        if (fmt.decimals != null) {
          opts.minimumFractionDigits = fmt.decimals
          opts.maximumFractionDigits = fmt.decimals
        }
        return new Intl.NumberFormat(locale, opts).format(Number(value))
      }

      case 'date': {
        const dateObj =
          value instanceof Date
            ? value
            : typeof value === 'number'
            ? new Date(value)
            : new Date(String(value))

        const opts = {}
        if (fmt.dateStyle) opts.dateStyle = fmt.dateStyle
        if (fmt.timeStyle) opts.timeStyle = fmt.timeStyle
        // default: short date if nothing specified
        if (!opts.dateStyle && !opts.timeStyle) opts.dateStyle = 'short'

        return new Intl.DateTimeFormat(locale, opts).format(dateObj)
      }

      default:
        return String(value)
    }
  } catch {
    // If formatting fails (invalid date, etc.), fall back to raw string
    return String(value)
  }
}
