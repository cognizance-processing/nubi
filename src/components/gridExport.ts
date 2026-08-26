/**
 * gridExport.js — Pure, dependency-free CSV + Excel exporters for DataGrid.
 *
 * No DOM, no React, no browser globals in the *builder* functions — those are
 * fully unit-testable in Node. The `download*` helpers are browser-only thin
 * wrappers around the builders.
 *
 * Why a hand-rolled Excel writer?
 * -------------------------------
 * The npm `xlsx` (SheetJS) package carries unfixable prototype-pollution /
 * ReDoS advisories. Rather than pull it in, we emit a SpreadsheetML 2003 XML
 * workbook (`.xls`/`.xml`) which every modern Excel + Google Sheets + LibreOffice
 * opens natively. It supports number/string typing, which is plenty for a grid
 * export and keeps `npm audit` clean.
 *
 * Exports
 * -------
 *   buildCSV(rows, columns)            → CSV string (RFC 4180)
 *   buildExcelXML(rows, columns, name, getValue, settings)
 *                                      → SpreadsheetML 2003 XML string
 *   planExportColumns(columns, settings) → column plan (exclude/reorder/rename)
 *   excelNumberFormat(spec)            → an Excel number-format string, or null
 *   downloadCSV(filename, rows, cols)  → browser download
 *   downloadExcel(filename, rows, cols, sheetName, getValue, settings)
 *                                      → browser download
 *
 * The optional `settings` argument is a widget's `exportSettings` block: sheet
 * name, column exclude/reorder/rename, header + cell styling, per-column number
 * formats, and freeze panes. SpreadsheetML supports all of it natively, so a
 * styled workbook needs no extra dependency.
 *
 * `columns` shape: [{ key, label }]
 * `rows` shape:    [{ [key]: value }]
 * An optional `getValue(row, col)` may be passed to apply display formatting
 * (e.g. columnFormats) to exported cells.
 */

// ---------------------------------------------------------------------------
// CSV
// ---------------------------------------------------------------------------

function csvEscape(v) {
  if (v == null) return ''
  const s = String(v)
  if (s.includes(',') || s.includes('"') || s.includes('\n') || s.includes('\r')) {
    return '"' + s.replace(/"/g, '""') + '"'
  }
  return s
}

/**
 * Build an RFC-4180 CSV string from rows + columns.
 *
 * @param {Array<Record<string, unknown>>} rows
 * @param {Array<{key: string, label?: string}>} columns
 * @param {(row: object, col: object) => unknown} [getValue]
 * @returns {string}
 */
export function buildCSV(rows, columns, getValue = undefined) {
  const header = columns.map((c) => csvEscape(c.label ?? c.key)).join(',')
  const body = rows
    .map((row) =>
      columns
        .map((c) => csvEscape(getValue ? getValue(row, c) : row[c.key]))
        .join(','),
    )
    .join('\r\n')
  return body ? header + '\r\n' + body : header
}

// ---------------------------------------------------------------------------
// Excel (SpreadsheetML 2003)
// ---------------------------------------------------------------------------

function xmlEscape(v) {
  return String(v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function xmlCell(value, styleId = undefined) {
  const style = styleId ? ` ss:StyleID="${styleId}"` : ''
  if (value == null || value === '') {
    return `<Cell${style}><Data ss:Type="String"></Data></Cell>`
  }
  // Numbers (but not things like "007" which Excel would mangle — only emit
  // Number when the value is an actual JS number / finite numeric).
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `<Cell${style}><Data ss:Type="Number">${value}</Data></Cell>`
  }
  if (typeof value === 'bigint') {
    return `<Cell${style}><Data ss:Type="Number">${value.toString()}</Data></Cell>`
  }
  return `<Cell${style}><Data ss:Type="String">${xmlEscape(value)}</Data></Cell>`
}

// ---------------------------------------------------------------------------
// Export settings (styled workbooks)
// ---------------------------------------------------------------------------

/**
 * An `exportSettings` object describes how the Excel workbook should LOOK and
 * which columns it should carry — separate from how the grid looks on screen,
 * because a spreadsheet handed to someone else is its own deliverable.
 *
 * Shape (every field optional):
 *
 *   {
 *     sheetName:      string,
 *     columnExcluded: string[],            // column keys to omit
 *     columnOrdering: string[],            // column keys, in the order wanted
 *     columnRenames:  { [key]: string },   // header text overrides
 *     headerStyling:  CellStyle,
 *     cellStyling:    CellStyle,
 *     numberFormat: {
 *       decimalPlaces?: number, thousandSeparator?: boolean, currencySymbol?: string,
 *       columns?: { [key]: { decimalPlaces?: number, currencySymbol?: string } },
 *     },
 *     freezeHeader:      boolean,
 *     freezeFirstColumn: boolean,
 *   }
 *
 * CellStyle = { fontColor?, fontSize?, fontBold?, fontItalic?, fontUnderline?,
 *               backgroundColor?, alignment?: 'left'|'center'|'right' }
 */

/** Normalise a colour to the `#RRGGBB` form SpreadsheetML requires, or null. */
function excelColor(value) {
  if (typeof value !== 'string') return null
  const v = value.trim()
  if (/^#[0-9a-fA-F]{6}$/.test(v)) return v.toUpperCase()
  if (/^#[0-9a-fA-F]{3}$/.test(v)) {
    return ('#' + v[1] + v[1] + v[2] + v[2] + v[3] + v[3]).toUpperCase()
  }
  // Theme tokens (`var(--x)`) and named/rgb() colours have no literal hex to
  // emit; dropping the colour is better than writing something Excel rejects.
  return null
}

const ALIGN_MAP = { left: 'Left', center: 'Center', right: 'Right' }

/** Build one `<Style>` element from a CellStyle descriptor. */
function styleElement(id, style, numberFormat = null) {
  const s = style ?? {}
  const parts = [`<Style ss:ID="${id}">`]

  const align = ALIGN_MAP[s.alignment]
  if (align) parts.push(`<Alignment ss:Horizontal="${align}"/>`)

  const fontAttrs = []
  const color = excelColor(s.fontColor)
  if (color) fontAttrs.push(`ss:Color="${color}"`)
  if (Number.isFinite(s.fontSize)) fontAttrs.push(`ss:Size="${s.fontSize}"`)
  if (s.fontBold) fontAttrs.push('ss:Bold="1"')
  if (s.fontItalic) fontAttrs.push('ss:Italic="1"')
  if (s.fontUnderline) fontAttrs.push('ss:Underline="Single"')
  if (fontAttrs.length) parts.push(`<Font ${fontAttrs.join(' ')}/>`)

  const bg = excelColor(s.backgroundColor)
  // `ss:Pattern="Solid"` is required alongside the colour or Excel ignores it.
  if (bg) parts.push(`<Interior ss:Color="${bg}" ss:Pattern="Solid"/>`)

  if (numberFormat) parts.push(`<NumberFormat ss:Format="${xmlEscape(numberFormat)}"/>`)

  parts.push('</Style>')
  return parts.join('')
}

/**
 * Build an Excel number-format string from a per-column spec.
 * Returns null when nothing was asked for, so the cell keeps Excel's General.
 */
export function excelNumberFormat(spec) {
  if (!spec) return null
  const dp = Number.isFinite(spec.decimalPlaces) ? Math.max(0, Math.min(20, spec.decimalPlaces)) : null
  const sym = typeof spec.currencySymbol === 'string' ? spec.currencySymbol.trim() : ''
  if (dp == null && !sym && !spec.thousandSeparator) return null
  const base = (spec.thousandSeparator ? '#,##0' : '0') + (dp ? '.' + '0'.repeat(dp) : '')
  return sym ? `"${sym}"${base}` : base
}

/**
 * Apply an exportSettings column plan: drop excluded columns, reorder, rename.
 * Columns named in `columnOrdering` come first in that order; anything not
 * named keeps its original relative position afterwards, so adding one column
 * to the ordering list does not silently drop the rest.
 */
export function planExportColumns(columns, settings) {
  const s = settings ?? {}
  const excluded = new Set(Array.isArray(s.columnExcluded) ? s.columnExcluded : [])
  let cols = (columns ?? []).filter((c) => !excluded.has(c.key))

  const ordering = Array.isArray(s.columnOrdering) ? s.columnOrdering : null
  if (ordering && ordering.length) {
    const byKey = new Map(cols.map((c) => [c.key, c]))
    const named = ordering.map((k) => byKey.get(k)).filter(Boolean)
    const namedKeys = new Set(named.map((c) => c.key))
    cols = [...named, ...cols.filter((c) => !namedKeys.has(c.key))]
  }

  const renames = s.columnRenames && typeof s.columnRenames === 'object' ? s.columnRenames : null
  if (renames) {
    cols = cols.map((c) => (renames[c.key] ? { ...c, label: renames[c.key] } : c))
  }
  return cols
}

/**
 * Build a SpreadsheetML 2003 XML workbook string.
 *
 * @param {Array<Record<string, unknown>>} rows
 * @param {Array<{key: string, label?: string}>} columns
 * @param {string} [sheetName='Sheet1']
 * @param {(row: object, col: object) => unknown} [getValue]
 * @param {object} [settings]  exportSettings (see above); omitted = plain workbook
 * @returns {string}
 */
export function buildExcelXML(rows, columns, sheetName = 'Sheet1', getValue = undefined, settings = undefined) {
  const s = settings ?? {}
  const cols = planExportColumns(columns, s)

  // ── Styles ──────────────────────────────────────────────────────────────
  // One header style, one default body style, plus one body style per column
  // that asks for its own number format.
  const styles = []
  const hasHeaderStyle = !!s.headerStyling
  const hasCellStyle = !!s.cellStyling
  if (hasHeaderStyle) styles.push(styleElement('hdr', s.headerStyling))

  const nf = s.numberFormat ?? null
  const perCol = nf && nf.columns && typeof nf.columns === 'object' ? nf.columns : {}
  const defaultFormat = excelNumberFormat(nf)
  if (hasCellStyle || defaultFormat) styles.push(styleElement('cel', s.cellStyling, defaultFormat))

  const colStyleId = {}
  for (const c of cols) {
    const fmt = excelNumberFormat({ ...(nf ?? {}), ...(perCol[c.key] ?? {}) })
    // Only worth its own style when it differs from the shared default.
    if (fmt && fmt !== defaultFormat) {
      const id = `c${styles.length}`
      colStyleId[c.key] = id
      styles.push(styleElement(id, s.cellStyling, fmt))
    } else if (hasCellStyle || defaultFormat) {
      colStyleId[c.key] = 'cel'
    }
  }
  const stylesBlock = styles.length ? `<Styles>${styles.join('')}</Styles>\n` : ''

  // ── Rows ────────────────────────────────────────────────────────────────
  const headerCells = cols
    .map((c) => `<Cell${hasHeaderStyle ? ' ss:StyleID="hdr"' : ''}><Data ss:Type="String">${xmlEscape(c.label ?? c.key)}</Data></Cell>`)
    .join('')
  const headerRow = `<Row>${headerCells}</Row>`

  const bodyRows = (rows ?? [])
    .map((row) => {
      const cells = cols
        .map((c) => xmlCell(getValue ? getValue(row, c) : row[c.key], colStyleId[c.key]))
        .join('')
      return `<Row>${cells}</Row>`
    })
    .join('')

  // ── Freeze panes ────────────────────────────────────────────────────────
  const freezeRow = !!s.freezeHeader
  const freezeCol = !!s.freezeFirstColumn
  let sheetOptions = ''
  if (freezeRow || freezeCol) {
    const opts = ['<FreezePanes/>', '<FrozenNoSplit/>']
    if (freezeCol) opts.push('<SplitVertical>1</SplitVertical>', '<LeftColumnRightPane>1</LeftColumnRightPane>')
    if (freezeRow) opts.push('<SplitHorizontal>1</SplitHorizontal>', '<TopRowBottomPane>1</TopRowBottomPane>')
    // ActivePane 3 = the bottom-right pane, which is where the data is.
    opts.push('<ActivePane>3</ActivePane>')
    sheetOptions =
      '<WorksheetOptions xmlns="urn:schemas-microsoft-com:office:excel">' +
      opts.join('') +
      '</WorksheetOptions>\n'
  }

  const safeName = xmlEscape(String(s.sheetName || sheetName || 'Sheet1').slice(0, 31) || 'Sheet1')

  return (
    '<?xml version="1.0"?>\n' +
    '<?mso-application progid="Excel.Sheet"?>\n' +
    '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"\n' +
    ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"\n' +
    ' xmlns:x="urn:schemas-microsoft-com:office:excel">\n' +
    stylesBlock +
    `<Worksheet ss:Name="${safeName}">\n` +
    '<Table>\n' +
    headerRow +
    bodyRows +
    '\n</Table>\n' +
    sheetOptions +
    '</Worksheet>\n</Workbook>'
  )
}

// ---------------------------------------------------------------------------
// Browser download helpers
// ---------------------------------------------------------------------------

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  setTimeout(() => {
    URL.revokeObjectURL(url)
    a.remove()
  }, 100)
}

export function downloadCSV(filename, rows, columns, getValue = undefined) {
  const csv = buildCSV(rows, columns, getValue)
  triggerDownload(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' }), filename)
}

export function downloadExcel(filename, rows, columns, sheetName = undefined, getValue = undefined, settings = undefined) {
  const xml = buildExcelXML(rows, columns, sheetName, getValue, settings)
  triggerDownload(
    new Blob([xml], { type: 'application/vnd.ms-excel;charset=utf-8;' }),
    filename,
  )
}
