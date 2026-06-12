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
 * Map a backend SQL / DuckDB / Arrow column type string to one of our
 * semantic kinds. The grid renders + edits based on this kind.
 *
 * `uuid`, `int` and `float` are *display* refinements of broader kinds:
 * `uuid` and `int`/`float` all behave as their base kind for editing
 * (uuid → string, int/float → number) but get distinct header icons.
 *
 * @param {string} rawType
 * @returns {'number'|'int'|'float'|'bool'|'date'|'json'|'uuid'|'string'}
 */
export function classifyType(rawType) {
  const t = String(rawType || '').toLowerCase()
  if (/(bool)/.test(t)) return 'bool'
  if (/(uuid|guid)/.test(t)) return 'uuid'
  if (/(json|jsonb|map|struct|array|\[\])/.test(t)) return 'json'
  // Order matters: check date/time before number so "timestamp" wins.
  if (/(date|time|timestamp)/.test(t)) return 'date'
  if (/(decimal|numeric|double|float|real)/.test(t)) return 'float'
  if (/(int|serial|hugeint|number)/.test(t)) return 'int'
  return 'string'
}

/**
 * Collapse a (possibly refined) kind to the base behavioural kind used for
 * formatting + editing. `int`/`float` → `number`, `uuid` → `string`.
 *
 * @param {string} kind
 * @returns {'number'|'bool'|'date'|'json'|'string'}
 */
export function baseKind(kind) {
  if (kind === 'int' || kind === 'float' || kind === 'number') return 'number'
  if (kind === 'uuid') return 'string'
  if (kind === 'bool' || kind === 'date' || kind === 'json') return kind
  return 'string'
}

/**
 * Which inline editor widget a given semantic type uses.
 * @param {'number'|'bool'|'date'|'json'|'string'} type
 * @returns {'number'|'checkbox'|'date'|'textarea'|'text'}
 */
export function editorKind(type) {
  switch (baseKind(type)) {
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

  const columns = rawCols
    // Drop backend-hidden system columns (e.g. the synthetic `_row_id` identity
    // — its value stays in the row data for PK edits, but it never renders as a
    // visible column; the row number lives in the gutter instead).
    .filter((c) => !(c && typeof c === 'object' && c.hidden === true))
    .map((c) => {
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
export function formatCell(value, rawType) {
  const type = baseKind(rawType)
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
export function toEditString(value, rawType) {
  const type = baseKind(rawType)
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
export function coerceInput(raw, rawType, nullable) {
  const type = baseKind(rawType)
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

// ---------------------------------------------------------------------------
// Operator-based client-side filters (over the loaded page)
// ---------------------------------------------------------------------------

/**
 * The filter operators the UI offers, keyed by id with a human label and
 * whether they take a value (is-null / not-null don't).
 */
export const FILTER_OPS = [
  { id: 'eq', label: '=', value: true },
  { id: 'neq', label: '≠', value: true },
  { id: 'contains', label: 'contains', value: true },
  { id: 'gt', label: '>', value: true },
  { id: 'gte', label: '≥', value: true },
  { id: 'lt', label: '<', value: true },
  { id: 'lte', label: '≤', value: true },
  { id: 'is_null', label: 'is null', value: false },
  { id: 'not_null', label: 'is not null', value: false },
]

/**
 * Evaluate a single filter against a row value. Numeric comparisons coerce
 * both sides to Number when possible; otherwise fall back to case-insensitive
 * string comparison. Unknown operators pass through (return true).
 *
 * @param {*} cell raw cell value
 * @param {{op:string, value?:string}} filter
 * @returns {boolean}
 */
export function matchFilter(cell, filter) {
  const { op } = filter
  if (op === 'is_null') return cell == null
  if (op === 'not_null') return cell != null
  if (cell == null) return false

  const raw = filter.value ?? ''
  const numA = typeof cell === 'bigint' ? Number(cell) : Number(cell)
  const numB = Number(raw)
  const numeric = raw.trim() !== '' && !Number.isNaN(numA) && !Number.isNaN(numB)
  const sa = String(cell).toLowerCase()
  const sb = raw.toLowerCase()

  switch (op) {
    case 'eq': return numeric ? numA === numB : sa === sb
    case 'neq': return numeric ? numA !== numB : sa !== sb
    case 'contains': return sa.includes(sb)
    case 'gt': return numeric ? numA > numB : sa > sb
    case 'gte': return numeric ? numA >= numB : sa >= sb
    case 'lt': return numeric ? numA < numB : sa < sb
    case 'lte': return numeric ? numA <= numB : sa <= sb
    default: return true
  }
}

/**
 * Apply a list of column filters (AND semantics) to rows. Filters with an
 * empty column are ignored. Value-taking ops with empty value are ignored.
 *
 * @param {Array<Record<string,*>>} rows
 * @param {Array<{column:string, op:string, value?:string}>} filters
 * @returns {Array<Record<string,*>>}
 */
export function filterRows(rows, filters) {
  const active = (filters ?? []).filter((f) => {
    if (!f || !f.column) return false
    const opMeta = FILTER_OPS.find((o) => o.id === f.op)
    if (!opMeta) return false
    if (opMeta.value && (f.value == null || f.value === '')) return false
    return true
  })
  if (active.length === 0) return rows
  return rows.filter((row) => active.every((f) => matchFilter(row[f.column], f)))
}

// ---------------------------------------------------------------------------
// Column width distribution (fill available space, no dead zone)
// ---------------------------------------------------------------------------

/**
 * Suggest a base width (px) for a column from its name + kind. Booleans and
 * numbers are narrow; json/text wider; the name length nudges it up so headers
 * don't truncate immediately.
 *
 * @param {{name:string, kind:string}} col
 * @returns {number}
 */
export function baseColumnWidth(col) {
  const kind = baseKind(col.kind)
  const nameW = 28 + String(col.name || '').length * 7.5
  let typeMin
  switch (kind) {
    case 'bool': typeMin = 90; break
    case 'number': typeMin = 110; break
    case 'date': typeMin = 170; break
    case 'json': typeMin = 240; break
    default: typeMin = col.kind === 'uuid' ? 250 : 170
  }
  return Math.round(Math.min(360, Math.max(typeMin, nameW)))
}

/**
 * Compute the rendered pixel width for every column so the table fills the
 * available content width without a dead zone on the right, while honouring
 * explicit user-set widths and a per-column minimum.
 *
 * Strategy: start from explicit widths (if set) else each column's base width.
 * If the total is narrower than `available`, distribute the slack across the
 * columns that are NOT explicitly sized, proportional to their base width.
 * Columns the user resized keep their exact width.
 *
 * @param {Array<{name:string, kind:string}>} columns
 * @param {Record<string, number>} explicit  user-set widths {name: px}
 * @param {number} available  content width available for data columns (px)
 * @param {number} [minW=80]
 * @returns {Record<string, number>}  {name: px} for every column
 */
export function distributeColumnWidths(columns, explicit, available, minW = 80) {
  const out = {}
  const bases = {}
  let total = 0
  for (const col of columns) {
    const w = Math.max(minW, explicit?.[col.name] ?? baseColumnWidth(col))
    bases[col.name] = w
    out[col.name] = w
    total += w
  }
  const flexible = columns.filter((c) => explicit?.[c.name] == null)
  const slack = (available || 0) - total
  if (slack > 0 && flexible.length > 0) {
    const flexBase = flexible.reduce((s, c) => s + bases[c.name], 0) || 1
    let used = 0
    flexible.forEach((c, i) => {
      const add = i === flexible.length - 1
        ? slack - used
        : Math.floor((bases[c.name] / flexBase) * slack)
      out[c.name] = bases[c.name] + add
      used += add
    })
  }
  return out
}

// ---------------------------------------------------------------------------
// Keyboard navigation math
// ---------------------------------------------------------------------------

/**
 * Compute the next selected cell given a direction, clamped to the grid.
 * Pure — the grid passes current {row, col} indices + bounds and applies the
 * result. Tab wraps to the next/previous row at the horizontal edges.
 *
 * @param {{row:number, col:number}} cur
 * @param {'up'|'down'|'left'|'right'|'tab'|'shiftTab'} dir
 * @param {number} rowCount
 * @param {number} colCount
 * @returns {{row:number, col:number}}
 */
export function moveSelection(cur, dir, rowCount, colCount) {
  if (rowCount <= 0 || colCount <= 0) return cur
  let { row, col } = cur
  const clampRow = (r) => Math.max(0, Math.min(rowCount - 1, r))
  const clampCol = (c) => Math.max(0, Math.min(colCount - 1, c))
  switch (dir) {
    case 'up': return { row: clampRow(row - 1), col }
    case 'down': return { row: clampRow(row + 1), col }
    case 'left': return { row, col: clampCol(col - 1) }
    case 'right': return { row, col: clampCol(col + 1) }
    case 'tab':
      if (col < colCount - 1) return { row, col: col + 1 }
      return { row: clampRow(row + 1), col: 0 }
    case 'shiftTab':
      if (col > 0) return { row, col: col - 1 }
      return { row: clampRow(row - 1), col: colCount - 1 }
    default: return cur
  }
}

/**
 * The plain-text form of a cell value for clipboard copy. Numbers/bools/json
 * become their canonical string; null becomes an empty string (so pasting a
 * column into a sheet yields blanks, not the literal "NULL").
 *
 * @param {*} value
 * @param {string} kind
 * @returns {string}
 */
export function copyCellText(value, kind) {
  if (value == null) return ''
  const base = baseKind(kind)
  if (base === 'json') {
    try { return typeof value === 'string' ? value : JSON.stringify(value) }
    catch { return String(value) }
  }
  if (base === 'date') return formatDate(value)
  return String(value)
}
