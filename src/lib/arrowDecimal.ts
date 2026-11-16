/**
 * arrowDecimal.js — make Arrow Decimal columns read as real numbers.
 *
 * apache-arrow's JS vectors return a Decimal column's UNSCALED integer from
 * `.get()` / `.toArray()` (78.52 stored at scale 2 comes back as 7852n).
 * MySQL emits DECIMAL for ROUND()/SUM() results, so without this every
 * decimal-typed KPI, chart axis and table cell renders 10^scale too large.
 *
 * `descaleDecimalTable(table)` wraps a decoded Table so `getChild()` on a
 * decimal column yields values divided by 10^scale. Tables without decimal
 * columns are returned unchanged (fast path). The wrapper exposes the full
 * surface widgets use — `numRows`, `schema`, `getChild` — and the wrapped
 * vectors support `.get(i)`, `.toArray()`, `.length` and iteration.
 */
import { Type } from 'apache-arrow'

function isDecimalField(field) {
  const t = field?.type
  if (!t) return false
  if (t.typeId === Type.Decimal) return true
  // Defensive fallback: any type carrying a numeric `scale` and named Decimal*
  return typeof t.scale === 'number' && String(t).toLowerCase().startsWith('decimal')
}

function descaledVector(vec, scale) {
  if (!vec) return vec
  const factor = 10 ** scale
  const get = (i) => {
    const v = vec.get(i)
    if (v == null) return null
    // Decimal comes back as BigInt (or a big-int-like); Number() is safe for
    // the magnitudes BI data holds. NaN falls through as-is.
    return Number(v) / factor
  }
  return {
    length: vec.length,
    get,
    toArray() {
      const out = new Array(vec.length)
      for (let i = 0; i < vec.length; i++) out[i] = get(i)
      return out
    },
    *[Symbol.iterator]() {
      for (let i = 0; i < vec.length; i++) yield get(i)
    },
  }
}

/**
 * @param {import('apache-arrow').Table | null} table
 * @returns the same table, or a read-shim with decimal columns descaled
 */
export function descaleDecimalTable(table) {
  const fields = table?.schema?.fields
  if (!fields || !fields.some(isDecimalField)) return table

  const scaleByName = new Map()
  for (const f of fields) {
    if (isDecimalField(f)) scaleByName.set(f.name, f.type.scale ?? 0)
  }
  const cache = new Map()
  return {
    numRows: table.numRows,
    schema: table.schema,
    getChild(name) {
      if (cache.has(name)) return cache.get(name)
      const child = table.getChild(name)
      const out = scaleByName.has(name)
        ? descaledVector(child, scaleByName.get(name))
        : child
      cache.set(name, out)
      return out
    },
  }
}
