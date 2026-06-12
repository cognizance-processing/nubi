/**
 * editableGridUtils.js — Pure, side-effect-free helpers for the Supabase-style
 * editable table editor (EditableDataGrid + DataBrowser).
 *
 * No DOM, no React, no browser globals — fully testable under `node --test`.
 *
 * Exports
 * -------
 *   normalizeColumnMeta(meta)              → { writable, primaryKey, columns:[{name,type,kind,nullable,editable}] }
 *   classifyType(rawType)                  → 'number'|'bool'|'date'|'json'|'string'
 *   editorKind(type)                       → 'number'|'checkbox'|'date'|'textarea'|'text'
 *   formatCell(value, type)                → { display, isNull, className }
 *   coerceInput(raw, type, nullable)       → { ok, value, error }   (string → typed value)
 *   pkObject(row, primaryKey)              → { col: val, ... } | null
 *   rowMatchesPk(row, pk)                  → boolean
 *   sortComparator(a, b, key, dir)         → number
 *   sortRows(rows, key, dir)               → sorted copy
 *   searchRows(rows, columns, query)       → filtered copy
 *   columnIsEditable(col, writable, hasPk) → boolean
 *   isReadOnly(meta)                       → { readOnly, reason|null }
 */

// ---------------------------------------------------------------------------
// Type classification
// ---------------------------------------------------------------------------

/**
 * Map a backend SQL / DuckDB / Arrow column type string to one of our five
 * semantic kinds. The grid renders + edits based on this kind.
 *
 * @param {string} rawType
 * @returns {'number'|'bool'|'date'|'json'|'string'}
 */
export function classifyType(rawType) {
  const t = String(rawType || '').toLowerCase()
  if (/(bool)/.test(t)) return 'bool'
  if (/(json|jsonb|map|struct|array|\[\])/.test(t)) return 'json'
  // Order matters: check date/time before number so "timestamp" wins.
  if (/(date|time|timestamp)/.test(t)) return 'date'
  if (/(int|serial|decimal|numeric|double|float|real|hugeint|number)/.test(t)) return 'number'
  return 'string'
}

/**
 * Which inline editor widget a given semantic type uses.
 * @param {'number'|'bool'|'date'|'json'|'string'} type
 * @returns {'number'|'checkbox'|'date'|'textarea'|'text'}
 */
export function editorKind(type) {
  switch (type) {
    case 'number': return 'number'
    case 'bool': return 'checkbox'
    case 'date': return 'date'
    case 'json': return 'textarea'
    default: return 'text'
  }
}

// ---------------------------------------------------------------------------
// Column meta normalization
// ---------------------------------------------------------------------------

/**
 * Normalize the columns/meta endpoint payload into a stable shape.
 *
 * Accepts the new write-contract shape:
 *   { writable, primary_key:[...], columns:[{name,type,nullable,editable}] }
 * and degrades gracefully for the older shapes
 *   { columns:[{name,type,nullable,pk}] }  or  [string,...]
 *
 * @param {object|Array} meta
 * @returns {{ writable: boolean, primaryKey: string[], columns: Array<{name:string,type:string,kind:string,nullable:boolean,editable:boolean,pk:boolean}> }}
 */
export function normalizeColumnMeta(meta) {
  const rawCols = Array.isArray(meta)
    ? meta
    : (meta?.columns ?? [])

  const primaryKey = Array.isArray(meta?.primary_key)
    ? meta.primary_key.slice()
    : Array.isArray(meta?.primaryKey)
      ? meta.primaryKey.slice()
      : rawCols.filter((c) => c && (c.pk || c.primary_key)).map((c) => c.name)

  const writable = meta?.writable === true

  const columns = rawCols.map((c) => {
    const col = typeof c === 'string' ? { name: c } : (c ?? {})
    const rawType = col.type ?? col.data_type ?? 'string'
    const isPk = primaryKey.includes(col.name)
    return {
      name: col.name,
      type: String(rawType),
      kind: classifyType(rawType),
      nullable: col.nullable !== false,
      // A column is editable when the backend says so. Default: editable unless
      // it is a primary-key column (PKs are the row identity — never edited
      // inline here) or the backend explicitly marks editable:false.
      editable: col.editable === false ? false : !isPk,
      pk: isPk,
    }
  })

  return { writable, primaryKey, columns }
}

/**
 * Whether a single column may be edited inline, factoring in table-level gates.
 * @param {{editable:boolean, pk:boolean}} col
 * @param {boolean} writable  table-level writable flag
 * @param {boolean} hasPk     table has at least one primary-key column
 * @returns {boolean}
 */
export function columnIsEditable(col, writable, hasPk) {
  if (!writable || !hasPk) return false
  if (!col || col.editable === false) return false
  if (col.pk) return false
  return true
}

/**
 * Resolve whether the whole table is read-only and the human reason why.
 * @param {{writable:boolean, primaryKey:string[]}} meta normalized meta
 * @returns {{ readOnly: boolean, reason: string|null }}
 */
export function isReadOnly(meta) {
  if (!meta) return { readOnly: true, reason: 'No table metadata' }
  if (!meta.writable) {
    return { readOnly: true, reason: 'This source is read-only' }
  }
  if (!meta.primaryKey || meta.primaryKey.length === 0) {
    return { readOnly: true, reason: 'Read-only: table has no primary key' }
  }
  return { readOnly: false, reason: null }
}

// ---------------------------------------------------------------------------
// Cell formatting (display)
// ---------------------------------------------------------------------------

/** Locale number formatter shared with the grid. */
export function formatNumber(n) {
  if (n == null) return ''
  if (typeof n === 'bigint') return n.toLocaleString('en-US')
  if (!Number.isFinite(n)) return String(n)
  if (Number.isInteger(n)) return n.toLocaleString('en-US')
  return n.toLocaleString('en-US', { maximumFractionDigits: 6 })
}

/** Format a date-ish value to a compact ISO-like string. */
export function formatDate(value) {
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return String(value)
    return value.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
  }
  return String(value)
}

/**
 * Produce a display descriptor for a cell value given its semantic type.
 * Returns the *string* to render plus flags the grid uses for styling. The
 * grid still renders booleans/nulls as special widgets, but this gives a
 * single canonical textual form (used by tooltips, copy, export, and tests).
 *
 * @param {*} value
 * @param {'number'|'bool'|'date'|'json'|'string'} type
 * @returns {{ display: string, isNull: boolean, align: 'left'|'right' }}
 */
export function formatCell(value, type) {
  if (value == null) return { display: 'NULL', isNull: true, align: type === 'number' ? 'right' : 'left' }
  if (type === 'number') {
    const n = typeof value === 'bigint' ? value : Number(value)
    return { display: formatNumber(n), isNull: false, align: 'right' }
  }
  if (type === 'bool') {
    return { display: value ? 'true' : 'false', isNull: false, align: 'left' }
  }
  if (type === 'date') {
    return { display: formatDate(value), isNull: false, align: 'left' }
  }
  if (type === 'json') {
    let s
    try { s = typeof value === 'string' ? value : JSON.stringify(value) }
    catch { s = String(value) }
    return { display: s, isNull: false, align: 'left' }
  }
  return { display: String(value), isNull: false, align: 'left' }
}

/**
 * Convert a typed cell value into the string an inline <input> should show so
 * the user edits a faithful representation (not "[object Object]").
 * @param {*} value
 * @param {'number'|'bool'|'date'|'json'|'string'} type
 * @returns {string}
 */
export function toEditString(value, type) {
  if (value == null) return ''
  if (type === 'json') {
    try { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) }
    catch { return String(value) }
  }
  if (type === 'date') return formatDate(value)
  return String(value)
}

// ---------------------------------------------------------------------------
// Input coercion (string → typed value, for PATCH/POST bodies)
// ---------------------------------------------------------------------------

/**
 * Coerce a raw editor value (string from an <input>, or boolean from a toggle)
 * into the JSON value to send to the backend, validating against the column
 * type + nullability.
 *
 * Empty string is treated as NULL when the column is nullable; otherwise it is
 * an error for non-string columns. For string columns empty string stays "".
 *
 * @param {string|boolean|null} raw
 * @param {'number'|'bool'|'date'|'json'|'string'} type
 * @param {boolean} nullable
 * @returns {{ ok: boolean, value: any, error: string|null }}
 */
export function coerceInput(raw, type, nullable) {
  // Explicit NULL sentinel (null passed directly) or empty for non-string types.
  if (raw === null) {
    if (nullable) return { ok: true, value: null, error: null }
    return { ok: false, value: undefined, error: 'Value is required (NOT NULL)' }
  }

  if (type === 'bool') {
    if (typeof raw === 'boolean') return { ok: true, value: raw, error: null }
    const s = String(raw).trim().toLowerCase()
    if (s === '' && nullable) return { ok: true, value: null, error: null }
    if (['true', 't', '1', 'yes', 'y'].includes(s)) return { ok: true, value: true, error: null }
    if (['false', 'f', '0', 'no', 'n'].includes(s)) return { ok: true, value: false, error: null }
    return { ok: false, value: undefined, error: 'Enter true or false' }
  }

  const s = typeof raw === 'string' ? raw : String(raw)

  if (type === 'number') {
    if (s.trim() === '') {
      if (nullable) return { ok: true, value: null, error: null }
      return { ok: false, value: undefined, error: 'Value is required (NOT NULL)' }
    }
    const n = Number(s)
    if (Number.isNaN(n)) return { ok: false, value: undefined, error: 'Not a valid number' }
    return { ok: true, value: n, error: null }
  }

  if (type === 'json') {
    if (s.trim() === '') {
      if (nullable) return { ok: true, value: null, error: null }
      return { ok: false, value: undefined, error: 'Value is required (NOT NULL)' }
    }
    try {
      return { ok: true, value: JSON.parse(s), error: null }
    } catch {
      return { ok: false, value: undefined, error: 'Invalid JSON' }
    }
  }

  // string / date — send the raw string; empty becomes NULL only if nullable
  if (s === '' && nullable) return { ok: true, value: null, error: null }
  return { ok: true, value: s, error: null }
}

// ---------------------------------------------------------------------------
// Primary-key helpers
// ---------------------------------------------------------------------------

/**
 * Build the `pk` object for a row given the primary-key column list.
 * Returns null if any PK column value is missing (cannot safely identify row).
 *
 * @param {Record<string,*>} row
 * @param {string[]} primaryKey
 * @returns {Record<string,*>|null}
 */
export function pkObject(row, primaryKey) {
  if (!row || !Array.isArray(primaryKey) || primaryKey.length === 0) return null
  const pk = {}
  for (const col of primaryKey) {
    if (!(col in row)) return null
    pk[col] = row[col]
  }
  return pk
}

/**
 * Whether a row matches a pk descriptor (all pk columns equal).
 * @param {Record<string,*>} row
 * @param {Record<string,*>} pk
 * @returns {boolean}
 */
export function rowMatchesPk(row, pk) {
  if (!row || !pk) return false
  for (const k of Object.keys(pk)) {
    if (row[k] !== pk[k]) return false
  }
  return true
}

// ---------------------------------------------------------------------------
// Sort + search (client-side, over the loaded page)
// ---------------------------------------------------------------------------

/**
 * Compare two row values for sorting. Nulls last; numbers/dates/bools/strings.
 * @param {Record<string,*>} a
 * @param {Record<string,*>} b
 * @param {string} key
 * @param {'asc'|'desc'} dir
 * @returns {number}
 */
export function sortComparator(a, b, key, dir) {
  const va = a[key]
  const vb = b[key]
  if (va == null && vb == null) return 0
  if (va == null) return 1
  if (vb == null) return -1

  let cmp
  if (typeof va === 'number' && typeof vb === 'number') {
    cmp = va - vb
  } else if (typeof va === 'bigint' && typeof vb === 'bigint') {
    cmp = va < vb ? -1 : va > vb ? 1 : 0
  } else if (va instanceof Date && vb instanceof Date) {
    cmp = va.getTime() - vb.getTime()
  } else if (typeof va === 'boolean' && typeof vb === 'boolean') {
    cmp = va === vb ? 0 : va ? 1 : -1
  } else {
    const sa = String(va).toLowerCase()
    const sb = String(vb).toLowerCase()
    cmp = sa < sb ? -1 : sa > sb ? 1 : 0
  }
  return dir === 'desc' ? -cmp : cmp
}

/**
 * Return a sorted copy of rows. `key`/`dir` null → unchanged copy.
 * @param {Array<Record<string,*>>} rows
 * @param {string|null} key
 * @param {'asc'|'desc'|null} dir
 * @returns {Array<Record<string,*>>}
 */
export function sortRows(rows, key, dir) {
  if (!key || !dir) return rows
  return [...rows].sort((a, b) => sortComparator(a, b, key, dir))
}

/**
 * Global substring search across all columns.
 * @param {Array<Record<string,*>>} rows
 * @param {Array<{name:string}>} columns
 * @param {string} query
 * @returns {Array<Record<string,*>>}
 */
export function searchRows(rows, columns, query) {
  if (!query || query.trim() === '') return rows
  const q = query.trim().toLowerCase()
  return rows.filter((row) =>
    columns.some((col) => {
      const v = row[col.name]
      if (v == null) return false
      return String(v).toLowerCase().includes(q)
    }),
  )
}
