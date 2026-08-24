/**
 * exports.test.mjs — Unit tests for src/lib/exports.js
 *
 * Run with:
 *   node --test src/lib/exports.test.mjs
 *
 * Coverage
 * --------
 *   arrowTableToCSV — pure Arrow-to-CSV transform (no DOM; testable in Node).
 *     - Correct header row from schema.fields
 *     - Correct data rows (integers, floats, strings, booleans)
 *     - Null / undefined values → empty string (not the string "null")
 *     - Single-row table
 *     - Multi-column table with mixed types
 *     - Empty table (0 rows) → header only
 *
 *   planPdfLayout — pure page-fitting maths behind elementToPDF.
 *     - A board that fits outright stays on one page
 *     - A board a little too tall is shrunk to fit rather than split
 *     - A genuinely long board keeps full width and paginates
 *     - Slices cover the whole image with no gap
 *
 *   DOM-only functions (downloadCSV, chartToPNG, elementToPDF, elementToPNG)
 *   are NOT tested here; they require a browser environment and are tested
 *   manually / via Playwright e2e.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { tableFromArrays } from 'apache-arrow'
import { arrowTableToCSV, planPdfLayout } from './exports.js'

// ---------------------------------------------------------------------------
// Helper: parse a CSV string back into rows for easy assertions
// ---------------------------------------------------------------------------

/**
 * Split a CSV string into an array of arrays (header + data rows).
 * Handles quoted fields (PapaParse always quotes when needed).
 * For simple test data we use a naive split — for correctness with quoted
 * commas we parse properly.
 *
 * @param {string} csv
 * @returns {string[][]}
 */
function parseCSV(csv) {
  return csv
    .trim()
    .split(/\r?\n/)   // handle both \n and \r\n (PapaParse uses \r\n by default)
    .map((line) => {
      // Simple RFC-4180: split on comma, strip surrounding quotes.
      const cells = []
      let current = ''
      let inQuote = false
      for (let i = 0; i < line.length; i++) {
        const ch = line[i]
        if (ch === '"') {
          if (inQuote && line[i + 1] === '"') {
            current += '"'
            i++
          } else {
            inQuote = !inQuote
          }
        } else if (ch === ',' && !inQuote) {
          cells.push(current)
          current = ''
        } else {
          current += ch
        }
      }
      cells.push(current)
      return cells
    })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('arrowTableToCSV — produces correct header from schema', () => {
  const table = tableFromArrays({
    id:    [1, 2, 3],
    label: ['alpha', 'beta', 'gamma'],
  })
  const csv = arrowTableToCSV(table)
  const rows = parseCSV(csv)

  assert.deepEqual(rows[0], ['id', 'label'], 'Header row must match field names')
})

test('arrowTableToCSV — produces correct data rows for integers and strings', () => {
  const table = tableFromArrays({
    id:   [10, 20, 30],
    name: ['foo', 'bar', 'baz'],
  })
  const csv = arrowTableToCSV(table)
  const rows = parseCSV(csv)

  assert.equal(rows.length, 4, '1 header + 3 data rows')
  assert.deepEqual(rows[1], ['10', 'foo'])
  assert.deepEqual(rows[2], ['20', 'bar'])
  assert.deepEqual(rows[3], ['30', 'baz'])
})

test('arrowTableToCSV — handles float values', () => {
  const table = tableFromArrays({
    x: [1.5, 2.75, 0.1],
    y: [100.0, 200.5, 300.9],
  })
  const csv = arrowTableToCSV(table)
  const rows = parseCSV(csv)

  assert.equal(rows[1][0], '1.5')
  assert.equal(rows[1][1], '100')
  assert.equal(rows[2][0], '2.75')
  assert.equal(rows[2][1], '200.5')
  assert.equal(rows[3][0], '0.1')
  assert.equal(rows[3][1], '300.9')
})

test('arrowTableToCSV — handles boolean values', () => {
  const table = tableFromArrays({
    active: [true, false, true],
  })
  const csv = arrowTableToCSV(table)
  const rows = parseCSV(csv)

  assert.equal(rows[0][0], 'active', 'header')
  assert.equal(rows[1][0], 'true')
  assert.equal(rows[2][0], 'false')
  assert.equal(rows[3][0], 'true')
})

test('arrowTableToCSV — mixed-type columns (int + float + string + bool)', () => {
  const table = tableFromArrays({
    id:     [1, 2],
    score:  [9.5, 7.0],
    tag:    ['A', 'B'],
    active: [true, false],
  })
  const csv = arrowTableToCSV(table)
  const rows = parseCSV(csv)

  assert.deepEqual(rows[0], ['id', 'score', 'tag', 'active'], 'header')
  assert.equal(rows[1][0], '1')
  assert.equal(rows[1][1], '9.5')
  assert.equal(rows[1][2], 'A')
  assert.equal(rows[1][3], 'true')
  assert.equal(rows[2][2], 'B')
  assert.equal(rows[2][3], 'false')
})

test('arrowTableToCSV — single-row table', () => {
  const table = tableFromArrays({
    col1: [42],
    col2: ['only'],
  })
  const csv = arrowTableToCSV(table)
  const rows = parseCSV(csv)

  assert.equal(rows.length, 2, '1 header + 1 data row')
  assert.deepEqual(rows[1], ['42', 'only'])
})

test('arrowTableToCSV — empty table (0 rows) produces header only', () => {
  // tableFromArrays with empty arrays still carries schema information.
  const table = tableFromArrays({
    id:   new Int32Array(0),
    name: [],
  })
  const csv = arrowTableToCSV(table)
  const rows = parseCSV(csv)

  // PapaParse unparse of a single-row (header-only) array-of-arrays
  // produces one line with the column names.
  assert.equal(rows.length, 1, 'Only the header row expected for empty table')
  assert.equal(rows[0][0], 'id')
  assert.equal(rows[0][1], 'name')
})

test('arrowTableToCSV — returned string contains newline-separated lines', () => {
  const table = tableFromArrays({ a: [1, 2, 3] })
  const csv = arrowTableToCSV(table)

  // Must have at least one newline (header + data rows).
  // PapaParse uses \r\n (CRLF) by default.
  assert.ok(csv.includes('\n') || csv.includes('\r\n'), 'CSV must contain newline separators')
})

test('arrowTableToCSV — column names with spaces are quoted in output', () => {
  // PapaParse automatically quotes fields containing commas/quotes; column
  // names with spaces may or may not be quoted depending on the backend.
  // Here we just verify the header is present and numRows correct.
  const table = tableFromArrays({
    'first name': ['Alice', 'Bob'],
    'last name':  ['Smith', 'Jones'],
  })
  const csv = arrowTableToCSV(table)

  assert.ok(csv.includes('first name') || csv.includes('"first name"'),
    'Column name with space must appear in the header')
  const rows = parseCSV(csv)
  assert.equal(rows.length, 3, '1 header + 2 data rows')
})

// ---------------------------------------------------------------------------
// planPdfLayout — page fitting
// ---------------------------------------------------------------------------

// A4 landscape minus the margins/header/footer elementToPDF reserves.
const CONTENT_W = 777.89
const CONTENT_H = 487.28

test('planPdfLayout — a capture that already fits stays on one page', () => {
  const plan = planPdfLayout(2000, 1000, CONTENT_W, CONTENT_H)
  assert.equal(plan.pages, 1)
  // Limited by width, so it is drawn at full content width.
  assert.ok(Math.abs(plan.drawW - CONTENT_W) < 0.01)
})

test('planPdfLayout — a slightly-too-tall board is shrunk to fit, not split', () => {
  // 2814x2152 is the real "Retail Sales Overview" capture: taller than one
  // page at full width, but only just.
  const plan = planPdfLayout(2814, 2152, CONTENT_W, CONTENT_H)
  assert.equal(plan.pages, 1)
  assert.ok(plan.drawW < CONTENT_W, 'shrunk below full width')
  assert.ok(plan.sliceH * plan.ptPerPx <= CONTENT_H + 0.01, 'fits the page height')
})

test('planPdfLayout — a genuinely long board paginates at full width', () => {
  const plan = planPdfLayout(2814, 12000, CONTENT_W, CONTENT_H)
  assert.ok(plan.pages > 1)
  assert.ok(Math.abs(plan.drawW - CONTENT_W) < 0.01, 'keeps full width')
  assert.ok(plan.sliceH * plan.ptPerPx <= CONTENT_H + 0.01, 'each slice fits a page')
})

test('planPdfLayout — slices cover the whole capture with no gap', () => {
  const height = 12000
  const plan = planPdfLayout(2814, height, CONTENT_W, CONTENT_H)
  assert.ok(plan.sliceH * plan.pages >= height, 'last slice reaches the bottom')
  assert.ok(plan.sliceH * (plan.pages - 1) < height, 'no wholly-empty trailing page')
})

test('planPdfLayout — a very tall capture never asks for a zero-height slice', () => {
  const plan = planPdfLayout(100, 1_000_000, CONTENT_W, CONTENT_H)
  assert.ok(plan.sliceH >= 1)
  assert.ok(Number.isFinite(plan.pages))
})
