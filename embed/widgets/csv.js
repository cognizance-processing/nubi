/**
 * csv.js — RFC-4180-compliant CSV serialisation helper for Nubi embed widgets.
 *
 * Rules enforced
 * --------------
 *  - Fields containing a comma, double-quote, or newline are enclosed in double-quotes.
 *  - Embedded double-quote characters are escaped by doubling them ("").
 *  - null / undefined values are written as an empty field (no quotes).
 *  - Rows are separated by CRLF (\r\n) as mandated by RFC 4180.
 *  - The output starts with a UTF-8 BOM (U+FEFF) so Excel opens it without
 *    a character-encoding wizard.
 *
 * Usage
 * -----
 *  import { toCsv } from './csv.js'
 *  const csv = toCsv(['name', 'score'], [['Alice', 42], ['Bob, Jr.', null]])
 *
 * @module csv
 */

const CRLF = '\r\n'
const BOM  = '﻿'

/**
 * Encode a single cell value per RFC-4180 rules.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function encodeCsvField(value) {
  if (value === null || value === undefined) return ''
  const str = String(value)
  // Quote if the field contains comma, double-quote, CR, or LF
  if (str.includes('"') || str.includes(',') || str.includes('\n') || str.includes('\r')) {
    return '"' + str.replace(/"/g, '""') + '"'
  }
  return str
}

/**
 * Serialise tabular data to a RFC-4180 CSV string with UTF-8 BOM.
 *
 * @param {string[]} columns   — ordered column names (used as the header row)
 * @param {unknown[][]} rows   — each element is an array of cell values aligned
 *                               with `columns`; or an array of objects keyed by
 *                               column name (both forms are accepted).
 * @returns {string}           — BOM + header + CRLF-delimited data rows
 */
export function toCsv(columns, rows) {
  const lines = []

  // Header row
  lines.push(columns.map(encodeCsvField).join(','))

  // Data rows — accept both array-of-arrays and array-of-objects
  for (const row of rows) {
    if (Array.isArray(row)) {
      lines.push(row.map(encodeCsvField).join(','))
    } else {
      // Object — extract values in column order
      lines.push(columns.map(col => encodeCsvField(row[col])).join(','))
    }
  }

  return BOM + lines.join(CRLF) + CRLF
}

/**
 * Trigger a client-side file download of the given CSV string.
 *
 * @param {string} csvContent  — full CSV string (including BOM if desired)
 * @param {string} [filename]  — suggested file name (default: 'export.csv')
 */
export function downloadCsv(csvContent, filename = 'export.csv') {
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  // Clean up asynchronously to give the browser time to initiate the download
  setTimeout(() => {
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, 100)
}
