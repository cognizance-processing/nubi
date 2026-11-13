/**
 * Pure helpers behind useBoardProviderTables (shared by SpecRenderer and the
 * editor canvas): slot padding, widget → provider result mapping, and the
 * column list ImageConfig offers for a provider-bound image.
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import {
  MAX_PROVIDERS,
  providerSlotsFor,
  buildWidgetProviderTableMap,
  providerTableColumns,
} from './providerDataUtils.js'

describe('providerSlotsFor', () => {
  test('pads to exactly MAX_PROVIDERS slots', () => {
    const slots = providerSlotsFor([{ id: 'p1' }, { id: 'p2' }])
    assert.equal(slots.length, MAX_PROVIDERS)
    assert.equal(slots[0].id, 'p1')
    assert.equal(slots[1].id, 'p2')
    assert.equal(slots[2], null)
  })
})

describe('buildWidgetProviderTableMap', () => {
  const logos = { name: 'logos-table' }
  const providers = [{ id: 'p1' }, { id: 'p2' }]
  const slotResults = [
    { tables: { logos } },
    { tables: {} },
    ...Array(MAX_PROVIDERS - 2).fill({ tables: {} }),
  ]

  test('maps provider-bound widgets to their result table', () => {
    const map = buildWidgetProviderTableMap(
      [
        { id: 'img', source: { provider: 'p1', result: 'logos' } },
        { id: 'missing', source: { provider: 'p2', result: 'nope' } },
        { id: 'undeclared', source: { provider: 'p9', result: 'logos' } },
        { id: 'query', query_id: 'q1' },
        { id: 'partial', source: { provider: 'p1' } },
      ],
      providers,
      slotResults,
    )
    assert.deepEqual(map, { img: logos, missing: null })
  })

  test('tolerates missing widgets', () => {
    assert.deepEqual(buildWidgetProviderTableMap(undefined, providers, slotResults), {})
  })
})

describe('providerTableColumns', () => {
  test('reads Arrow schema field names', () => {
    const table = { schema: { fields: [{ name: 'name' }, { name: 'url' }] } }
    assert.deepEqual(providerTableColumns(table), ['name', 'url'])
  })

  test('empty when no table yet', () => {
    assert.deepEqual(providerTableColumns(null), [])
    assert.deepEqual(providerTableColumns(undefined), [])
  })
})
