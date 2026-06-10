/**
 * VariableStore.jsx — Lightweight React context for dashboard variables.
 *
 * The store holds a flat map of { [varName]: value } for a single dashboard.
 * Filter widgets WRITE to the store; data widgets READ from the store to build
 * their query params.
 *
 * Public API
 * ----------
 * <VariableProvider initialValues={{}} />   Wrap the dashboard render tree.
 * useVariable(name)                          Read a single variable value.
 * useSetVariable()                           Returns a setter: (name, value) => void
 * getResolvedParams(widgetParams, variables) Pure helper — resolve widget params
 *                                            against current variable values.
 * resolveParams(widgetParams, variables)     Same as getResolvedParams but exported
 *                                            as a named function for unit testing.
 *
 * Param resolution rules (resolveParams)
 * --------------------------------------
 * Each entry in widgetParams is either:
 *   { ref: '<varName>' }            → resolved value (see multiselect shape below)
 *   { ref: '<varName>', pick: 'mode' }   → 'all' | 'include' | 'exclude' string
 *   { ref: '<varName>', pick: 'values' } → plain string[] (the selected values)
 *   <literal>                       → passed through as-is
 *
 * If a ref points to an unknown variable name, the resolved value is undefined.
 *
 * Multiselect variable value shapes (Track F — F-P2)
 * ---------------------------------------------------
 * Filter widgets may write any of:
 *   ["a", "b"]                          plain array — legacy include
 *   { mode: "include", values: [...] }
 *   { mode: "exclude", values: [...] }  "all but these"
 *   { mode: "all" }                     no constraint
 *
 * When a ref has NO `pick` key the resolved value is:
 *   - plain array  → passed through unchanged (full backward compat)
 *   - structured   → normalized { mode, values } shape so query bindings can
 *                    inspect it; query authors use the `pick` binding instead of
 *                    a bare ref when they need separate mode / values params.
 *
 * When `pick: "mode"` is set the value resolves to the mode string
 * ('all' | 'include' | 'exclude'), enabling SQL patterns like:
 *   WHERE (:region_mode = 'all'
 *      OR (:region_mode = 'include' AND region IN     (SELECT unnest(:region_values)))
 *      OR (:region_mode = 'exclude' AND region NOT IN (SELECT unnest(:region_values))))
 *
 * When `pick: "values"` is set the value resolves to the raw string array, which
 * the query layer binds as an array parameter for IN / NOT IN.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
// Multiselect value-shape helpers from Track F — imported here so resolveParams
// can call them directly (no re-implementation) and re-exported so callers can
// interrogate raw variable values without importing from the inputs/ layer.
import { isExclude, valuesOf, modeOf, normMulti } from './inputs/index.js'
export { isExclude, valuesOf, modeOf, normMulti }

// ---------------------------------------------------------------------------
// Pure helper (exported for unit tests — no React required)
// ---------------------------------------------------------------------------

/**
 * Resolve a widget's params object against the current variable values map.
 *
 * @param {Record<string, {ref: string, pick?: 'mode'|'values'} | unknown>} widgetParams
 *   The params field from a widget spec.  Each value is either a `{ref}` object
 *   (optionally with a `pick` key) or a literal value.
 * @param {Record<string, unknown>} variables
 *   Current variable store state.
 * @returns {Record<string, unknown>}
 *   A new params object where every `{ref}` has been replaced by its variable
 *   value and every literal has been passed through unchanged.
 *
 *   Multiselect-aware resolution:
 *   - `{ ref, pick: 'mode' }`   → 'all' | 'include' | 'exclude'
 *   - `{ ref, pick: 'values' }` → string[]  (the selected values)
 *   - `{ ref }` (no pick)       → plain array passed through unchanged (backward
 *     compat); structured { mode, values } passed through as normalized { mode,
 *     values } so callers that inspect the shape get a consistent object.
 */
export function resolveParams(widgetParams, variables) {
  if (!widgetParams || typeof widgetParams !== 'object' || Array.isArray(widgetParams)) {
    return {}
  }
  if (!variables || typeof variables !== 'object') {
    variables = {}
  }

  const resolved = {}
  for (const [paramName, paramValue] of Object.entries(widgetParams)) {
    if (
      paramValue !== null &&
      typeof paramValue === 'object' &&
      !Array.isArray(paramValue) &&
      Object.prototype.hasOwnProperty.call(paramValue, 'ref')
    ) {
      // {ref: '<varName>', pick?: 'mode'|'values'} — look up in variables
      const rawValue = variables[paramValue.ref]
      const pick = paramValue.pick

      if (pick === 'mode') {
        // Resolve to the mode string: 'all' | 'include' | 'exclude'
        resolved[paramName] = rawValue === undefined ? undefined : modeOf(rawValue)
      } else if (pick === 'values') {
        // Resolve to the plain string array regardless of include/exclude
        resolved[paramName] = rawValue === undefined ? undefined : valuesOf(rawValue)
      } else if (Array.isArray(rawValue)) {
        // Plain array — pass through as-is (full backward compatibility)
        resolved[paramName] = rawValue
      } else if (
        rawValue !== null &&
        rawValue !== undefined &&
        typeof rawValue === 'object' &&
        ('mode' in rawValue || 'values' in rawValue)
      ) {
        // Structured multiselect value — normalize so callers get a consistent shape:
        // { mode: 'all'|'include'|'exclude', values: string[] }
        resolved[paramName] = normMulti(rawValue)
      } else {
        // Scalar, undefined, or any other type — pass through unchanged
        resolved[paramName] = rawValue
      }
    } else {
      // Literal — pass through as-is
      resolved[paramName] = paramValue
    }
  }
  return resolved
}

/**
 * Alias with the "get" prefix — kept for symmetry with the naming in TASKS.md.
 * Both names are exported; prefer resolveParams in tests.
 */
export const getResolvedParams = resolveParams

// ---------------------------------------------------------------------------
// React context
// ---------------------------------------------------------------------------

const VariableContext = createContext(null)

/**
 * Provider that wraps a dashboard render tree.
 *
 * @param {{
 *   initialValues?: Record<string, unknown>,
 *   onVariableChange?: (name: string, value: unknown) => void,
 *   children: React.ReactNode
 * }} props
 *
 * onVariableChange — optional callback fired whenever a variable is set.
 *   Used by DashboardViewPage to write changes back to the URL search params.
 *   The callback receives the variable name and new value.  Callers should
 *   use useCallback / a stable ref to avoid infinite re-renders.
 */
export function VariableProvider({ initialValues = {}, onVariableChange, children }) {
  const [variables, setVariables] = useState(() => ({ ...initialValues }))

  // Keep a ref to the latest onVariableChange so the stable setVariable
  // callback can always call the most recent version without being re-created.
  const onChangeRef = useRef(onVariableChange)
  useEffect(() => { onChangeRef.current = onVariableChange }, [onVariableChange])

  // Stable setter — does NOT re-create on every render
  const setVariable = useCallback((name, value) => {
    setVariables(prev => {
      if (prev[name] === value) return prev   // bail out early — no re-render
      const next = { ...prev, [name]: value }
      // Fire the external callback (e.g. URL write-back) after state is queued.
      // We call it here (inside the updater) so we always have the real new value.
      setTimeout(() => { onChangeRef.current?.(name, value) }, 0)
      return next
    })
  }, [])

  // Memoize the context value so consumers only re-render when variables change
  const ctx = useMemo(() => ({ variables, setVariable }), [variables, setVariable])

  return (
    <VariableContext.Provider value={ctx}>
      {children}
    </VariableContext.Provider>
  )
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/**
 * Read the current value of a single variable.
 *
 * @param {string} name
 * @returns {unknown}
 */
export function useVariable(name) {
  const ctx = useContext(VariableContext)
  if (!ctx) throw new Error('useVariable must be used inside <VariableProvider>')
  return ctx.variables[name]
}

/**
 * Returns the stable `(name, value) => void` setter.
 * Components that only SET variables won't re-render when variable values change.
 *
 * @returns {(name: string, value: unknown) => void}
 */
export function useSetVariable() {
  const ctx = useContext(VariableContext)
  if (!ctx) throw new Error('useSetVariable must be used inside <VariableProvider>')
  return ctx.setVariable
}

/**
 * Convenience hook: returns the fully-resolved params for a widget given its
 * spec params.  Re-renders when any referenced variable changes.
 *
 * @param {Record<string, {ref: string} | unknown>} widgetParams
 * @returns {Record<string, unknown>}
 */
export function useResolvedParams(widgetParams) {
  const ctx = useContext(VariableContext)
  if (!ctx) throw new Error('useResolvedParams must be used inside <VariableProvider>')
  return useMemo(
    () => resolveParams(widgetParams, ctx.variables),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(widgetParams), ctx.variables]
  )
}
