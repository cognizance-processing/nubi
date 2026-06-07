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
 *   buildExcelXML(rows, columns, name) → SpreadsheetML 2003 XML string
 *   downloadCSV(filename, rows, cols)  → browser download
 *   downloadExcel(filename, rows, cols, sheetName) → browser download
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
export function buildCSV(rows, columns, getValue) {
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

function xmlCell(value) {
  if (value == null || value === '') {
    return '<Cell><Data ss:Type="String"></Data></Cell>'
  }
  // Numbers (but not things like "007" which Excel would mangle — only emit
  // Number when the value is an actual JS number / finite numeric).
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `<Cell><Data ss:Type="Number">${value}</Data></Cell>`
  }
  if (typeof value === 'bigint') {
    return `<Cell><Data ss:Type="Number">${value.toString()}</Data></Cell>`
  }
  return `<Cell><Data ss:Type="String">${xmlEscape(value)}</Data></Cell>`
}

/**
 * Build a SpreadsheetML 2003 XML workbook string.
 *
 * @param {Array<Record<string, unknown>>} rows
 * @param {Array<{key: string, label?: string}>} columns
 * @param {string} [sheetName='Sheet1']
 * @param {(row: object, col: object) => unknown} [getValue]
 * @returns {string}
 */
export function buildExcelXML(rows, columns, sheetName = 'Sheet1', getValue) {
  const headerCells = columns
    .map((c) => `<Cell><Data ss:Type="String">${xmlEscape(c.label ?? c.key)}</Data></Cell>`)
    .join('')
  const headerRow = `<Row>${headerCells}</Row>`

  const bodyRows = rows
    .map((row) => {
      const cells = columns
        .map((c) => xmlCell(getValue ? getValue(row, c) : row[c.key]))
        .join('')
      return `<Row>${cells}</Row>`
    })
    .join('')

  const safeName = xmlEscape(String(sheetName).slice(0, 31) || 'Sheet1')

  return (
    '<?xml version="1.0"?>\n' +
    '<?mso-application progid="Excel.Sheet"?>\n' +
    '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"\n' +
    ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n' +
    `<Worksheet ss:Name="${safeName}">\n` +
    '<Table>\n' +
    headerRow +
    bodyRows +
    '\n</Table>\n</Worksheet>\n</Workbook>'
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

export function downloadCSV(filename, rows, columns, getValue) {
  const csv = buildCSV(rows, columns, getValue)
  triggerDownload(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' }), filename)
}

export function downloadExcel(filename, rows, columns, sheetName, getValue) {
  const xml = buildExcelXML(rows, columns, sheetName, getValue)
  triggerDownload(
    new Blob([xml], { type: 'application/vnd.ms-excel;charset=utf-8;' }),
    filename,
  )
}
