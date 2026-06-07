/**
 * gridExport.test.mjs — Node:test unit tests for the pure CSV + Excel builders.
 *
 * Run: node --test src/dashboards/gridExport.test.mjs
 *
 * (Lives under src/dashboards/ so it is picked up by the dashboard test glob;
 *  imports the implementation from src/components/gridExport.js.)
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import { buildCSV, buildExcelXML } from '../components/gridExport.js'

const columns = [
  { key: 'name', label: 'Name' },
  { key: 'score', label: 'Score' },
]

describe('buildCSV', () => {
  test('produces header + body with CRLF', () => {
    const rows = [
      { name: 'Alice', score: 95 },
      { name: 'Bob', score: 80 },
    ]
    const csv = buildCSV(rows, columns)
    const lines = csv.split('\r\n')
    assert.equal(lines[0], 'Name,Score')
    assert.equal(lines[1], 'Alice,95')
    assert.equal(lines[2], 'Bob,80')
  })

  test('escapes commas, quotes and newlines', () => {
    const rows = [{ name: 'Smith, "JR"\nX', score: 1 }]
    const csv = buildCSV(rows, columns)
    assert.ok(csv.includes('"Smith, ""JR""\nX"'))
  })

  test('serialises null as empty string', () => {
    const csv = buildCSV([{ name: null, score: 42 }], columns)
    assert.ok(csv.split('\r\n')[1].startsWith(',42'))
  })

  test('header-only when no rows', () => {
    assert.equal(buildCSV([], columns), 'Name,Score')
  })

  test('honours getValue override', () => {
    const csv = buildCSV([{ name: 'x', score: 5 }], columns, (row, col) =>
      col.key === 'score' ? `$${row.score}` : row[col.key],
    )
    assert.ok(csv.includes('x,$5'))
  })

  test('falls back to key when label missing', () => {
    const csv = buildCSV([{ a: 1 }], [{ key: 'a' }])
    assert.equal(csv.split('\r\n')[0], 'a')
  })
})

describe('buildExcelXML', () => {
  test('emits a SpreadsheetML workbook', () => {
    const xml = buildExcelXML([{ name: 'Alice', score: 95 }], columns)
    assert.ok(xml.includes('<?mso-application progid="Excel.Sheet"?>'))
    assert.ok(xml.includes('urn:schemas-microsoft-com:office:spreadsheet'))
    assert.ok(xml.includes('<Worksheet ss:Name="Sheet1">'))
  })

  test('types numbers as Number and strings as String', () => {
    const xml = buildExcelXML([{ name: 'Alice', score: 95 }], columns)
    assert.ok(xml.includes('<Data ss:Type="Number">95</Data>'))
    assert.ok(xml.includes('<Data ss:Type="String">Alice</Data>'))
  })

  test('escapes XML-sensitive characters', () => {
    const xml = buildExcelXML([{ name: 'a<b>&"c', score: 1 }], columns)
    assert.ok(xml.includes('a&lt;b&gt;&amp;&quot;c'))
    assert.ok(!xml.includes('a<b>&"c'))
  })

  test('null cells become empty string cells', () => {
    const xml = buildExcelXML([{ name: null, score: 1 }], columns)
    assert.ok(xml.includes('<Cell><Data ss:Type="String"></Data></Cell>'))
  })

  test('truncates sheet name to 31 chars', () => {
    const long = 'x'.repeat(50)
    const xml = buildExcelXML([], columns, long)
    const m = xml.match(/ss:Name="(x+)"/)
    assert.ok(m)
    assert.equal(m[1].length, 31)
  })

  test('honours getValue override', () => {
    const xml = buildExcelXML([{ name: 'a', score: 5 }], columns, 'S', (row, col) =>
      col.key === 'score' ? `pct-${row.score}` : row[col.key],
    )
    assert.ok(xml.includes('<Data ss:Type="String">pct-5</Data>'))
  })
})
