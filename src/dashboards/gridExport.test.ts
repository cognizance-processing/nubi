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

import {
  buildCSV, buildExcelXML, planExportColumns, excelNumberFormat,
} from '../components/gridExport.js'

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

// ---------------------------------------------------------------------------
// exportSettings — styled workbooks (legacy parity)
// ---------------------------------------------------------------------------

describe('planExportColumns', () => {
  const cols = [
    { key: 'a', label: 'A' },
    { key: 'b', label: 'B' },
    { key: 'c', label: 'C' },
  ]

  test('no settings leaves the columns untouched', () => {
    assert.deepEqual(planExportColumns(cols, undefined), cols)
  })

  test('excluded columns are dropped', () => {
    const out = planExportColumns(cols, { columnExcluded: ['b'] })
    assert.deepEqual(out.map(c => c.key), ['a', 'c'])
  })

  test('named columns lead, and unnamed ones keep their order behind them', () => {
    // Partial orderings must not silently drop the rest — that would quietly
    // shrink the export when someone reorders one column.
    const out = planExportColumns(cols, { columnOrdering: ['c'] })
    assert.deepEqual(out.map(c => c.key), ['c', 'a', 'b'])
  })

  test('an ordering entry for an excluded column is ignored', () => {
    const out = planExportColumns(cols, { columnExcluded: ['c'], columnOrdering: ['c', 'b'] })
    assert.deepEqual(out.map(c => c.key), ['b', 'a'])
  })

  test('renames replace the header label only', () => {
    const out = planExportColumns(cols, { columnRenames: { a: 'Alpha' } })
    assert.equal(out[0].label, 'Alpha')
    assert.equal(out[0].key, 'a', 'the data key must not change')
    assert.equal(out[1].label, 'B')
  })
})

describe('excelNumberFormat', () => {
  test('returns null when nothing was asked for', () => {
    assert.equal(excelNumberFormat(null), null)
    assert.equal(excelNumberFormat({}), null)
  })

  test('decimals, separators and a currency symbol compose', () => {
    assert.equal(excelNumberFormat({ decimalPlaces: 2 }), '0.00')
    assert.equal(excelNumberFormat({ thousandSeparator: true }), '#,##0')
    assert.equal(excelNumberFormat({ decimalPlaces: 2, thousandSeparator: true }), '#,##0.00')
    assert.equal(excelNumberFormat({ decimalPlaces: 2, currencySymbol: 'R' }), '"R"0.00')
  })
})

describe('buildExcelXML with exportSettings', () => {
  const rows = [{ name: 'a', score: 1 }, { name: 'b', score: 2 }]

  test('an omitted settings object produces the same plain workbook as before', () => {
    const xml = buildExcelXML(rows, columns, 'Sheet1')
    assert.ok(!xml.includes('<Styles>'), 'no styles should be emitted')
    assert.ok(!xml.includes('FreezePanes'))
    assert.ok(xml.includes('ss:Name="Sheet1"'))
  })

  test('sheetName in settings wins over the positional argument', () => {
    const xml = buildExcelXML(rows, columns, 'Positional', undefined, { sheetName: 'Revenue' })
    assert.ok(xml.includes('ss:Name="Revenue"'))
  })

  test('header styling emits a style and tags the header cells with it', () => {
    const xml = buildExcelXML(rows, columns, 'S', undefined, {
      headerStyling: { fontBold: true, fontColor: '#ffffff', backgroundColor: '#123456', alignment: 'center' },
    })
    assert.ok(xml.includes('<Style ss:ID="hdr">'))
    assert.ok(xml.includes('ss:Bold="1"'))
    assert.ok(xml.includes('ss:Color="#FFFFFF"'))
    // Excel ignores a fill colour without an explicit pattern.
    assert.ok(xml.includes('<Interior ss:Color="#123456" ss:Pattern="Solid"/>'))
    assert.ok(xml.includes('ss:Horizontal="Center"'))
    assert.ok(xml.includes('<Cell ss:StyleID="hdr">'))
  })

  test('a theme token is dropped rather than written as an invalid colour', () => {
    // `var(--surface)` means nothing inside a workbook; emitting it verbatim
    // would make Excel reject the file.
    const xml = buildExcelXML(rows, columns, 'S', undefined, {
      headerStyling: { backgroundColor: 'var(--surface)', fontBold: true },
    })
    assert.ok(!xml.includes('var(--surface)'))
    assert.ok(xml.includes('ss:Bold="1"'), 'the rest of the style still applies')
  })

  test('a per-column number format gets its own style', () => {
    const xml = buildExcelXML(rows, columns, 'S', undefined, {
      numberFormat: { decimalPlaces: 0, columns: { score: { decimalPlaces: 2, currencySymbol: '$' } } },
    })
    assert.ok(xml.includes('ss:Format="&quot;$&quot;0.00"'), xml)
  })

  test('freeze panes are emitted for header and first column', () => {
    const xml = buildExcelXML(rows, columns, 'S', undefined, {
      freezeHeader: true, freezeFirstColumn: true,
    })
    assert.ok(xml.includes('<FreezePanes/>'))
    assert.ok(xml.includes('<SplitHorizontal>1</SplitHorizontal>'))
    assert.ok(xml.includes('<SplitVertical>1</SplitVertical>'))
  })

  test('excluded columns really do not reach the workbook', () => {
    const xml = buildExcelXML(rows, columns, 'S', undefined, { columnExcluded: ['score'] })
    assert.ok(xml.includes('>Name<'))
    assert.ok(!xml.includes('>Score<'))
  })
})
