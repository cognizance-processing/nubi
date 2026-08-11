/**
 * titleValue.test.mjs — chart `config.title` in both of the forms the shared
 * chart builder accepts.
 *
 * Boards imported from the legacy platform store the ECharts OBJECT form
 * ({ text, textStyle, left }) — an editor field that assumed a string crashed
 * on `.trim()` the moment such a widget was selected. These tests pin that the
 * object form reads as text and, crucially, that retyping a title keeps the
 * imported styling instead of flattening it to a bare string.
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { titleText, withTitleText } from './titleValue.js'

// ── titleText ──────────────────────────────────────────────────────────────

test('titleText passes a string title through', () => {
  assert.equal(titleText('Revenue'), 'Revenue')
  assert.equal(titleText(''), '')
})

test('titleText reads .text out of an ECharts title object', () => {
  assert.equal(titleText({ text: 'Revenue', textStyle: { fontSize: 14 }, left: 'center' }), 'Revenue')
  assert.equal(titleText({ text: 2024 }), '2024')
})

test('titleText yields empty text for anything without usable text', () => {
  for (const v of [undefined, null, true, 42, [], {}, { textStyle: { fontSize: 14 } }, { text: {} }]) {
    assert.equal(titleText(v), '', `expected '' for ${JSON.stringify(v) ?? String(v)}`)
  }
})

// ── withTitleText ──────────────────────────────────────────────────────────

test('withTitleText keeps the object form and its styling', () => {
  const title = { text: 'Old', textStyle: { color: '#0B2C4D', fontSize: 14 }, left: 'center' }
  assert.deepEqual(withTitleText(title, 'New'), {
    text: 'New', textStyle: { color: '#0B2C4D', fontSize: 14 }, left: 'center',
  })
  // Clearing the text must not drop the styling with it.
  assert.deepEqual(withTitleText(title, ''), {
    text: '', textStyle: { color: '#0B2C4D', fontSize: 14 }, left: 'center',
  })
})

test('withTitleText does not mutate the title it was given', () => {
  const title = { text: 'Old', textStyle: { fontSize: 14 } }
  withTitleText(title, 'New')
  assert.equal(title.text, 'Old')
})

test('withTitleText writes a plain string when there is no object to preserve', () => {
  assert.equal(withTitleText('Old', 'New'), 'New')
  assert.equal(withTitleText(undefined, 'New'), 'New')
  // An emptied string title drops out of the config entirely.
  assert.equal(withTitleText('Old', ''), undefined)
  assert.equal(withTitleText(undefined, ''), undefined)
})
