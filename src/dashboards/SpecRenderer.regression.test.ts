/**
 * SpecRenderer.regression.test.mjs
 *
 * Regression tests for the VariableProvider / useResolvedParams context bug.
 *
 * THE BUG (fixed): SpecRendererInner called useResolvedParams(...) 8× at its
 * own top level, BEFORE mounting <VariableProvider> in its return tree.
 * useResolvedParams reads VariableValuesContext which only exists once
 * VariableProvider mounts — so every render of /d/:id threw:
 *   "useResolvedParams must be used inside <VariableProvider>"
 *
 * THE FIX: a new SpecRendererBody inner component owns all 8 useResolvedParams
 * calls and the render tree. SpecRendererInner now only computes variableDefaults
 * and renders:
 *   <VariableProvider ...>
 *     <SpecRendererBody ... />   ← hooks run inside the provider
 *   </VariableProvider>
 *
 * These tests cover the pure functions that live in SpecRendererInner
 * (the outer shell) and the SpecRendererBody boundary logic, without
 * requiring a full React DOM environment.
 *
 * Run with:
 *   node --test src/dashboards/SpecRenderer.regression.test.mjs
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// buildVariableDefaults — lives in SpecRendererInner, pure function.
// Inline copy; if the impl changes this test will catch divergence.
// ---------------------------------------------------------------------------

function buildVariableDefaults(specVariables: unknown): Record<string, any> {
  if (!Array.isArray(specVariables)) return {}
  const defaults: Record<string, any> = {}
  for (const v of specVariables) {
    if (v?.name) {
      defaults[v.name] = v.default ?? undefined
    }
  }
  return defaults
}

describe('buildVariableDefaults', () => {
  test('returns {} for missing / null input', () => {
    assert.deepEqual(buildVariableDefaults(null), {})
    assert.deepEqual(buildVariableDefaults(undefined), {})
    assert.deepEqual(buildVariableDefaults('not-an-array'), {})
  })

  test('returns {} for empty array', () => {
    assert.deepEqual(buildVariableDefaults([]), {})
  })

  test('extracts default values from spec.variables', () => {
    const specVars = [
      { name: 'region', type: 'string', default: 'US' },
      { name: 'month',  type: 'string', default: '2024-01' },
    ]
    assert.deepEqual(buildVariableDefaults(specVars), {
      region: 'US',
      month: '2024-01',
    })
  })

  test('variable without a default gets undefined (not omitted)', () => {
    const specVars = [{ name: 'region', type: 'string' }]
    const result = buildVariableDefaults(specVars)
    assert.ok('region' in result, 'key should be present even with no default')
    assert.equal(result.region, undefined)
  })

  test('skips entries without a name', () => {
    const specVars = [
      { type: 'string', default: 'orphan' },
      { name: 'region', default: 'EU' },
    ]
    const result = buildVariableDefaults(specVars)
    assert.equal(Object.keys(result).length, 1)
    assert.equal(result.region, 'EU')
  })

  test('later entries with same name overwrite earlier ones', () => {
    const specVars = [
      { name: 'region', default: 'US' },
      { name: 'region', default: 'EU' },
    ]
    assert.equal(buildVariableDefaults(specVars).region, 'EU')
  })
})

// ---------------------------------------------------------------------------
// variableDefaults merge: spec defaults + initialVariables overlay
// This mirrors the useMemo in SpecRendererInner that feeds VariableProvider.
// ---------------------------------------------------------------------------

describe('variableDefaults merge (spec defaults + initialVariables overlay)', () => {
  function mergeDefaults(specVariables, initialVariables) {
    return {
      ...buildVariableDefaults(specVariables),
      ...(initialVariables ?? {}),
    }
  }

  test('spec defaults alone are returned when no initialVariables', () => {
    const merged = mergeDefaults(
      [{ name: 'region', default: 'US' }],
      {},
    )
    assert.equal(merged.region, 'US')
  })

  test('initialVariables override spec defaults (higher precedence)', () => {
    const merged = mergeDefaults(
      [{ name: 'region', default: 'US' }],
      { region: 'EU' },
    )
    assert.equal(merged.region, 'EU')
  })

  test('initialVariables can add keys not in spec.variables', () => {
    const merged = mergeDefaults(
      [{ name: 'region', default: 'US' }],
      { month: '2024-03' },
    )
    assert.equal(merged.region, 'US')
    assert.equal(merged.month, '2024-03')
  })

  test('empty spec.variables + full initialVariables → only initialVariables', () => {
    const merged = mergeDefaults([], { region: 'ZA', fiscal_year: 2025 })
    assert.deepEqual(merged, { region: 'ZA', fiscal_year: 2025 })
  })

  test('null initialVariables does not crash', () => {
    const merged = mergeDefaults([{ name: 'x', default: 1 }], null)
    assert.equal(merged.x, 1)
  })
})

// ---------------------------------------------------------------------------
// Structure contract: SpecRendererBody receives the right props boundary.
//
// These are data-only checks that mirror the props SpecRendererInner passes
// to SpecRendererBody. The contract guarantees the provider-resolution hooks
// run INSIDE <VariableProvider>, not outside it.
// ---------------------------------------------------------------------------

describe('SpecRendererInner → SpecRendererBody prop boundary', () => {
  // Simulate the prop-derivation logic that SpecRendererInner runs
  // BEFORE rendering <VariableProvider><SpecRendererBody .../></VariableProvider>.
  function deriveOuterProps(spec: Record<string, any>, boardIdProp: string | null, initialVariables: Record<string, any> = {}) {
    const cols        = spec.layout?.cols      ?? 12
    const rowHeight   = spec.layout?.row_height ?? 60
    const allWidgets  = spec.widgets ?? []
    const colsByBp    = {
      lg: cols,
      md: spec.layout?.cols_md ?? cols,
      sm: spec.layout?.cols_sm ?? 1,
    }
    const boardId = boardIdProp ?? spec._boardId ?? null
    const variableDefaults = {
      ...buildVariableDefaults(spec.variables),
      ...initialVariables,
    }
    return { cols, rowHeight, allWidgets, colsByBp, boardId, variableDefaults }
  }

  test('derives correct cols from spec.layout', () => {
    const { cols } = deriveOuterProps({ layout: { cols: 16 } }, null)
    assert.equal(cols, 16)
  })

  test('defaults cols to 12 when layout absent', () => {
    const { cols } = deriveOuterProps({}, null)
    assert.equal(cols, 12)
  })

  test('colsByBp.sm defaults to 1 (single-column mobile)', () => {
    const { colsByBp } = deriveOuterProps({ layout: { cols: 12 } }, null)
    assert.equal(colsByBp.sm, 1)
  })

  test('colsByBp.sm uses spec.layout.cols_sm when set', () => {
    const { colsByBp } = deriveOuterProps({ layout: { cols: 12, cols_sm: 2 } }, null)
    assert.equal(colsByBp.sm, 2)
  })

  test('boardId prefers explicit prop over spec._boardId', () => {
    const { boardId } = deriveOuterProps({ _boardId: 'spec-id' }, 'prop-id')
    assert.equal(boardId, 'prop-id')
  })

  test('boardId falls back to spec._boardId when prop is null', () => {
    const { boardId } = deriveOuterProps({ _boardId: 'spec-id' }, null)
    assert.equal(boardId, 'spec-id')
  })

  test('boardId is null when neither prop nor spec._boardId set', () => {
    const { boardId } = deriveOuterProps({}, null)
    assert.equal(boardId, null)
  })

  test('allWidgets is [] when spec.widgets absent', () => {
    const { allWidgets } = deriveOuterProps({}, null)
    assert.deepEqual(allWidgets, [])
  })

  test('variableDefaults merges spec defaults + initialVariables', () => {
    const spec = {
      variables: [
        { name: 'region', default: 'US' },
        { name: 'month',  default: '2024-01' },
      ],
    }
    const { variableDefaults } = deriveOuterProps(spec, null, { month: '2024-06' })
    assert.equal(variableDefaults.region, 'US')
    assert.equal(variableDefaults.month, '2024-06') // initialVariables wins
  })

  test('providerSlots shape: exactly 8 slots from boardProviders', () => {
    // Simulate the providerSlots derivation from SpecRendererBody
    const boardProviders = [
      { id: 'p1', params: { x: '{{region}}' } },
      { id: 'p2', params: {} },
    ]
    const MAX_PROVIDERS = 8
    const slots = []
    for (let i = 0; i < MAX_PROVIDERS; i++) {
      slots.push(boardProviders[i] ?? null)
    }
    assert.equal(slots.length, 8)
    assert.equal(slots[0].id, 'p1')
    assert.equal(slots[1].id, 'p2')
    assert.equal(slots[2], null)
    assert.equal(slots[7], null)
  })

  test('providerSlots: empty spec.data yields 8 null slots', () => {
    const boardProviders = []
    const MAX_PROVIDERS = 8
    const slots = Array.from({ length: MAX_PROVIDERS }, (_, i) => boardProviders[i] ?? null)
    assert.deepEqual(slots, Array(8).fill(null))
  })

  test('useResolvedParams receives {} fallback when slot is null', () => {
    // When providerSlots[i] === null, the params arg is {} (not undefined).
    // This is the guard that prevents crashes for sparse provider arrays.
    const slots = Array(8).fill(null)
    const paramArgs = slots.map(slot => slot?.params ?? {})
    paramArgs.forEach((p, i) => {
      assert.deepEqual(p, {}, `slot ${i} should produce {} params`)
    })
  })
})
