/**
 * csv.test.js — Unit tests for the RFC-4180 CSV serialisation helper.
 *
 * Tests cover: plain values, commas, embedded double-quotes, newlines,
 * null/undefined, numeric types, unicode, and array-of-objects row form.
 *
 * Run with: npm run test:embed
 */

import { describe, test, expect } from 'vitest'
import { encodeCsvField, toCsv } from '../widgets/csv.js'

const BOM  = '﻿'
const CRLF = '\r\n'

// ---------------------------------------------------------------------------
// encodeCsvField — low-level field encoding
// ---------------------------------------------------------------------------

describe('encodeCsvField', () => {
  test('plain string — no quoting needed', () => {
    expect(encodeCsvField('hello')).toBe('hello')
  })

  test('empty string', () => {
    expect(encodeCsvField('')).toBe('')
  })

  test('null → empty string', () => {
    expect(encodeCsvField(null)).toBe('')
  })

  test('undefined → empty string', () => {
    expect(encodeCsvField(undefined)).toBe('')
  })

  test('number is stringified', () => {
    expect(encodeCsvField(42)).toBe('42')
    expect(encodeCsvField(3.14)).toBe('3.14')
    expect(encodeCsvField(0)).toBe('0')
  })

  test('boolean is stringified', () => {
    expect(encodeCsvField(true)).toBe('true')
    expect(encodeCsvField(false)).toBe('false')
  })

  test('field with comma is quoted', () => {
    expect(encodeCsvField('foo,bar')).toBe('"foo,bar"')
  })

  test('field with double-quote has quote doubled and is quoted', () => {
    expect(encodeCsvField('say "hello"')).toBe('"say ""hello"""')
  })

  test('field with LF newline is quoted', () => {
    expect(encodeCsvField('line1\nline2')).toBe('"line1\nline2"')
  })

  test('field with CR is quoted', () => {
    expect(encodeCsvField('line1\rline2')).toBe('"line1\rline2"')
  })

  test('field with CRLF is quoted', () => {
    expect(encodeCsvField('line1\r\nline2')).toBe('"line1\r\nline2"')
  })

  test('field with only double-quote', () => {
    expect(encodeCsvField('"')).toBe('""""')
  })

  test('unicode text — no quoting for ordinary unicode', () => {
    expect(encodeCsvField('café résumé')).toBe('café résumé')
  })

  test('unicode text with comma — quoted', () => {
    expect(encodeCsvField('café, résumé')).toBe('"café, résumé"')
  })

  test('unicode emoji', () => {
    expect(encodeCsvField('hello 🌍')).toBe('hello 🌍')
  })

  test('field with only whitespace — no quoting needed (RFC-4180 allows it)', () => {
    expect(encodeCsvField('   ')).toBe('   ')
  })
})

// ---------------------------------------------------------------------------
// toCsv — full document serialisation
// ---------------------------------------------------------------------------

describe('toCsv', () => {
  test('produces a BOM at the start', () => {
    const result = toCsv(['a'], [['1']])
    expect(result.startsWith(BOM)).toBe(true)
  })

  test('header row matches column names', () => {
    const result = toCsv(['name', 'score'], [])
    const lines  = result.slice(1).split(CRLF)   // skip BOM
    expect(lines[0]).toBe('name,score')
  })

  test('CRLF line endings between header and rows', () => {
    const result = toCsv(['a'], [['1'], ['2']])
    // All separators must be CRLF
    expect(result).toContain('\r\n')
    // Should NOT contain bare \n not preceded by \r
    const crlfRemoved = result.replace(/\r\n/g, '')
    expect(crlfRemoved).not.toContain('\n')
  })

  test('file ends with a trailing CRLF', () => {
    const result = toCsv(['x'], [['v']])
    expect(result.endsWith(CRLF)).toBe(true)
  })

  test('plain values produce unquoted fields', () => {
    const result = toCsv(['a', 'b'], [['hello', '42']])
    expect(result).toContain('hello,42')
  })

  test('comma in value is quoted', () => {
    const result = toCsv(['name'], [['Smith, John']])
    expect(result).toContain('"Smith, John"')
  })

  test('double-quote in value is escaped (doubled) and quoted', () => {
    const result = toCsv(['q'], [['"quoted"']])
    expect(result).toContain('"""quoted"""')
  })

  test('newline in value is quoted', () => {
    const result = toCsv(['text'], [['line1\nline2']])
    expect(result).toContain('"line1\nline2"')
  })

  test('null value produces empty field', () => {
    const result = toCsv(['a', 'b'], [[null, 'x']])
    // line: ,x  (first field empty)
    const lines = result.split(CRLF)
    const dataLine = lines[1]   // second line = first data row
    expect(dataLine).toBe(',x')
  })

  test('undefined value produces empty field', () => {
    const result = toCsv(['a'], [[undefined]])
    const lines = result.split(CRLF)
    expect(lines[1]).toBe('')
  })

  test('multiple rows in correct order', () => {
    const result = toCsv(['id', 'val'], [['1', 'a'], ['2', 'b'], ['3', 'c']])
    const lines  = result.slice(1).split(CRLF).filter(Boolean)
    expect(lines).toEqual(['id,val', '1,a', '2,b', '3,c'])
  })

  test('empty rows array — only header line', () => {
    const result = toCsv(['x', 'y'], [])
    const lines  = result.slice(1).split(CRLF).filter(Boolean)
    expect(lines).toEqual(['x,y'])
  })

  test('column header containing comma is quoted', () => {
    const result = toCsv(['a,b', 'c'], [])
    const lines = result.slice(1).split(CRLF)
    expect(lines[0]).toBe('"a,b",c')
  })

  test('object rows — values extracted in column order', () => {
    const result = toCsv(
      ['first', 'last'],
      [{ first: 'Jane', last: 'Doe' }, { first: 'John', last: 'Smith' }],
    )
    const lines = result.slice(1).split(CRLF).filter(Boolean)
    expect(lines[1]).toBe('Jane,Doe')
    expect(lines[2]).toBe('John,Smith')
  })

  test('object rows — missing key produces empty field', () => {
    const result = toCsv(['a', 'b'], [{ a: 'hello' }])
    const lines  = result.slice(1).split(CRLF).filter(Boolean)
    expect(lines[1]).toBe('hello,')
  })

  test('numeric values in object rows', () => {
    const result = toCsv(['x'], [{ x: 99 }])
    const lines  = result.slice(1).split(CRLF).filter(Boolean)
    expect(lines[1]).toBe('99')
  })

  test('unicode content round-trips correctly', () => {
    const result = toCsv(['city'], [['Zürich'], ['São Paulo'], ['東京']])
    const lines = result.slice(1).split(CRLF).filter(Boolean)
    expect(lines[1]).toBe('Zürich')
    expect(lines[2]).toBe('São Paulo')
    expect(lines[3]).toBe('東京')
  })

  test('emoji in value', () => {
    const result = toCsv(['label'], [['hello 🌍']])
    const lines = result.slice(1).split(CRLF).filter(Boolean)
    expect(lines[1]).toBe('hello 🌍')
  })
})
