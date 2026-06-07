/**
 * dataTableUtils.js — Pure, side-effect-free helpers for DataTable.
 *
 * No DOM, no React, no browser globals. Fully testable in Node.js.
 *
 * Exports
 * -------
 *   deriveColumns(arrowTable)          → [{key, label, type}]
 *   arrowToRows(arrowTable)            → [{...}]
 *   sortRows(rows, key, dir)           → sorted copy
 *   filterRows(rows, columns, filters) → filtered copy
 *   searchRows(rows, columns, query)   → filtered copy
 *   paginateRows(rows, page, pageSize) → {slice, totalRows, totalPages, startRow, endRow}
 *   sortComparator(a, b, key, dir)     → number
 *   matchesFilter(value, filter, type) → boolean
 *   rowsToCSV(rows, columns)           → CSV string
 */

// ---------------------------------------------------------------------------
// Type detection
// ---------------------------------------------------------------------------

/**
 * Map an Apache Arrow DataType instance to one of our four semantic types.
 * @param {object} dtype – an apache-arrow DataType instance
 * @returns {'number'|'string'|'date'|'bool'}
 */
export function arrowTypeToColumnType(dtype) {
  if (!dtype) return 'string'
  const name = dtype.constructor?.name ?? ''
  // Integers
  if (name.startsWith('Int') || name.startsWith('Uint')) return 'number'
  // Floats
  if (name.startsWith('Float')) return 'number'
  // Boolean
  if (name === 'Bool') return 'bool'
  // Timestamps / Dates / Times
  if (name.startsWith('Timestamp') || name.startsWith('Date') || name.startsWith('Time')) return 'date'
  // Decimal
  if (name.startsWith('Decimal')) return 'number'
  // Duration
  if (name.startsWith('Duration')) return 'number'
  // Everything else (Utf8, LargeUtf8, Dictionary, List, Struct…) → string
  return 'string'
}

// ---------------------------------------------------------------------------
// Arrow → plain JS
// ---------------------------------------------------------------------------

/**
 * Derive a columns descriptor array from an apache-arrow Table.
 *
 * @param {import('apache-arrow').Table} arrowTable
 * @returns {Array<{key: string, label: string, type: 'number'|'string'|'date'|'bool'}>}
 */
export function deriveColumns(arrowTable) {
  if (!arrowTable?.schema?.fields) return []
  return arrowTable.schema.fields.map((field) => ({
    key: field.name,
    label: field.name,
    type: arrowTypeToColumnType(field.type),
  }))
}

/**
 * Convert an apache-arrow Table to an array of plain row objects.
 * Null values are preserved as JS null.
 *
 * @param {import('apache-arrow').Table} arrowTable
 * @returns {Array<Record<string, unknown>>}
 */
export function arrowToRows(arrowTable) {
  if (!arrowTable?.schema?.fields) return []

  const fields = arrowTable.schema.fields
  const numRows = arrowTable.numRows
  const rows = []

  // Pre-fetch column vectors for performance
  const vectors = fields.map((f) => arrowTable.getChild(f.name))

  for (let i = 0; i < numRows; i++) {
    const row = {}
    for (let j = 0; j < fields.length; j++) {
      const v = vectors[j]
      if (v) {
        const val = v.get(i)
        // BigInt → Number for display (Arrow Int64 returns BigInt)
        row[fields[j].name] = typeof val === 'bigint' ? Number(val) : val
      } else {
        row[fields[j].name] = null
      }
    }
    rows.push(row)
  }

  return rows
}

// ---------------------------------------------------------------------------
// Sort
// ---------------------------------------------------------------------------

/**
 * Compare two values for sorting. Handles null (nulls last), numbers, strings, dates.
 *
 * @param {*} a
 * @param {*} b
 * @param {string} key        – row key to compare
 * @param {'asc'|'desc'} dir
 * @returns {number}
 */
export function sortComparator(a, b, key, dir) {
  const va = a[key]
  const vb = b[key]

  // Nulls always last
  if (va == null && vb == null) return 0
  if (va == null) return 1
  if (vb == null) return -1

  let cmp = 0

  if (typeof va === 'number' && typeof vb === 'number') {
    cmp = va - vb
  } else if (va instanceof Date && vb instanceof Date) {
    cmp = va.getTime() - vb.getTime()
  } else if (typeof va === 'boolean' && typeof vb === 'boolean') {
    cmp = (va === vb) ? 0 : va ? -1 : 1
  } else {
    // String comparison
    const sa = String(va).toLowerCase()
    const sb = String(vb).toLowerCase()
    cmp = sa < sb ? -1 : sa > sb ? 1 : 0
  }

  return dir === 'desc' ? -cmp : cmp
}

/**
 * Return a sorted copy of rows.
 *
 * @param {Array<Record<string, unknown>>} rows
 * @param {string|null} key    – column key; null returns rows unchanged
 * @param {'asc'|'desc'|null} dir
 * @returns {Array<Record<string, unknown>>}
 */
export function sortRows(rows, key, dir) {
  if (!key || !dir) return rows
  return [...rows].sort((a, b) => sortComparator(a, b, key, dir))
}

// ---------------------------------------------------------------------------
// Filter
// ---------------------------------------------------------------------------

/**
 * Test whether a cell value matches a filter descriptor.
 *
 * @param {*}      value  – cell value
 * @param {{op: string, value: string}} filter – filter descriptor
 * @param {'number'|'string'|'date'|'bool'} type – column type
 * @returns {boolean}
 */
export function matchesFilter(value, filter, type) {
  if (!filter || filter.value === '' || filter.value == null) return true

  const fv = filter.value
  const op = filter.op ?? 'contains'

  if (value == null) {
    // null matches "eq null" queries, not others
    return op === 'eq' && (fv === '' || fv === 'null' || fv === 'NULL')
  }

  if (type === 'number') {
    const num = typeof value === 'number' ? value : Number(value)
    const fnum = Number(fv)
    if (isNaN(fnum)) return true // don't filter if filter value isn't numeric
    switch (op) {
      case 'eq':  return num === fnum
      case 'ne':  return num !== fnum
      case 'gt':  return num > fnum
      case 'gte': return num >= fnum
      case 'lt':  return num < fnum
      case 'lte': return num <= fnum
      default:    return true
    }
  }

  if (type === 'bool') {
    const boolStr = String(value).toLowerCase()
    const filterStr = String(fv).toLowerCase()
    return op === 'eq' ? boolStr === filterStr : boolStr !== filterStr
  }

  // string / date / fallback — always use contains or eq
  const strVal = String(value).toLowerCase()
  const strFilter = String(fv).toLowerCase()
  switch (op) {
    case 'eq':       return strVal === strFilter
    case 'ne':       return strVal !== strFilter
    case 'contains': return strVal.includes(strFilter)
    default:         return strVal.includes(strFilter)
  }
}

/**
 * Filter rows by per-column filter descriptors.
 *
 * @param {Array<Record<string, unknown>>} rows
 * @param {Array<{key: string, type: string}>} columns
 * @param {Record<string, {op: string, value: string}>} filters – keyed by column key
 * @returns {Array<Record<string, unknown>>}
 */
export function filterRows(rows, columns, filters) {
  if (!filters || Object.keys(filters).length === 0) return rows

  const activeFilters = Object.entries(filters).filter(([, f]) => f && f.value !== '' && f.value != null)
  if (activeFilters.length === 0) return rows

  const colTypeMap = {}
  for (const col of columns) colTypeMap[col.key] = col.type

  return rows.filter((row) => {
    for (const [key, filter] of activeFilters) {
      if (!matchesFilter(row[key], filter, colTypeMap[key] ?? 'string')) {
        return false
      }
    }
    return true
  })
}

/**
 * Filter rows by a global search string — matches any column.
 *
 * @param {Array<Record<string, unknown>>} rows
 * @param {Array<{key: string}>} columns
 * @param {string} query
 * @returns {Array<Record<string, unknown>>}
 */
export function searchRows(rows, columns, query) {
  if (!query || query.trim() === '') return rows
  const q = query.trim().toLowerCase()
  return rows.filter((row) =>
    columns.some((col) => {
      const val = row[col.key]
      if (val == null) return false
      return String(val).toLowerCase().includes(q)
    })
  )
}

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

/**
 * Slice rows for a given page.
 *
 * @param {Array<Record<string, unknown>>} rows
 * @param {number} page      – 0-based page index
 * @param {number} pageSize
 * @returns {{slice: Array, totalRows: number, totalPages: number, startRow: number, endRow: number}}
 */
export function paginateRows(rows, page, pageSize) {
  const totalRows = rows.length
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize))
  const safePage = Math.max(0, Math.min(page, totalPages - 1))
  const start = safePage * pageSize
  const end = Math.min(start + pageSize, totalRows)
  return {
    slice: rows.slice(start, end),
    totalRows,
    totalPages,
    startRow: totalRows === 0 ? 0 : start + 1,
    endRow: end,
  }
}

// ---------------------------------------------------------------------------
// CSV export (pure — no browser APIs)
// ---------------------------------------------------------------------------

/**
 * Convert rows + columns to a CSV string (RFC 4180).
 *
 * @param {Array<Record<string, unknown>>} rows
 * @param {Array<{key: string, label: string}>} columns
 * @returns {string}
 */
export function rowsToCSV(rows, columns) {
  const escape = (v) => {
    if (v == null) return ''
    const s = String(v)
    if (s.includes(',') || s.includes('"') || s.includes('\n')) {
      return '"' + s.replace(/"/g, '""') + '"'
    }
    return s
  }
  const header = columns.map((c) => escape(c.label)).join(',')
  const body = rows
    .map((row) => columns.map((c) => escape(row[c.key])).join(','))
    .join('\n')
  return header + '\n' + body
}

// ---------------------------------------------------------------------------
// Number formatting helper
// ---------------------------------------------------------------------------

/**
 * Format a number with locale-aware thousands separator + up to 6 sig digits.
 * Returns the raw string for non-finite values.
 *
 * @param {number} n
 * @returns {string}
 */
export function formatNumber(n) {
  if (n == null) return ''
  if (!isFinite(n)) return String(n)
  // Integers: plain format; floats: up to 6 decimal places, strip trailing zeros
  if (Number.isInteger(n)) {
    return n.toLocaleString('en-US')
  }
  return n.toLocaleString('en-US', { maximumFractionDigits: 6 })
}
