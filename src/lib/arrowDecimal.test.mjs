import test from 'node:test'
import assert from 'node:assert/strict'
import { descaleDecimalTable } from './arrowDecimal.js'
import { Type } from 'apache-arrow'

// Duck-typed table double: the util only touches schema.fields, numRows and
// getChild, and vectors only need get/length — no real Arrow buffers needed.
function fakeVec(values) {
  return {
    length: values.length,
    get: (i) => values[i],
    toArray: () => values.slice(),
  }
}

function fakeTable(fields, cols) {
  return {
    numRows: Object.values(cols)[0]?.length ?? 0,
    schema: { fields },
    getChild: (name) => (name in cols ? fakeVec(cols[name]) : null),
  }
}

const decimalField = (name, scale) => ({ name, type: { typeId: Type.Decimal, scale } })
const intField = (name) => ({ name, type: { typeId: Type.Int } })

test('table without decimal columns is returned unchanged', () => {
  const t = fakeTable([intField('a')], { a: [1, 2] })
  assert.equal(descaleDecimalTable(t), t)
})

test('decimal column values are divided by 10^scale', () => {
  const t = fakeTable(
    [decimalField('pct', 2), intField('n')],
    { pct: [7852n, 8149n, null], n: [1, 2, 3] },
  )
  const w = descaleDecimalTable(t)
  assert.notEqual(w, t)
  const pct = w.getChild('pct')
  assert.equal(pct.get(0), 78.52)
  assert.equal(pct.get(1), 81.49)
  assert.equal(pct.get(2), null)
  assert.deepEqual(pct.toArray(), [78.52, 81.49, null])
  assert.deepEqual([...pct], [78.52, 81.49, null])
  assert.equal(pct.length, 3)
})

test('non-decimal columns pass through untouched on a wrapped table', () => {
  const t = fakeTable(
    [decimalField('d', 1), intField('n')],
    { d: [10n], n: [42] },
  )
  const w = descaleDecimalTable(t)
  assert.equal(w.getChild('n').get(0), 42)
  assert.equal(w.numRows, 1)
  assert.equal(w.schema, t.schema)
})

test('scale 0 decimal stays numerically identical', () => {
  const t = fakeTable([decimalField('d', 0)], { d: [4n] })
  assert.equal(descaleDecimalTable(t).getChild('d').get(0), 4)
})

test('null/empty table passes through', () => {
  assert.equal(descaleDecimalTable(null), null)
})
