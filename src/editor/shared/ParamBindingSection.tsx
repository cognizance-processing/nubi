/**
 * shared/ParamBindingSection.jsx — Widget parameter binding editor.
 * Edits widget.params: maps param names to either literal values or { ref: varName }.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Props:
 *   widget          object    — spec widget
 *   onChange        (w)=>void — full widget update callback
 *   specVariables   array     — spec.variables array for the variable-ref picker
 */

import { inputCls, selectCls, SectionLabel } from './inspectorPrimitives.jsx'

export function ParamBindingSection({ widget, onChange, specVariables }: {
  widget: Record<string, any>
  onChange: (widget: Record<string, any>) => void
  specVariables?: Array<{ name: string }>
}) {
  const params: Record<string, any> = widget.params ?? {}
  const varNames = (specVariables ?? []).map(v => v.name)

  const setParam = (paramName: string, value: any) =>
    onChange({ ...widget, params: { ...params, [paramName]: value } })
  const removeParam = (paramName: string) => {
    const next = { ...params }
    delete next[paramName]
    onChange({ ...widget, params: next })
  }
  const addParam = () => {
    const name = `param${Object.keys(params).length + 1}`
    if (!(name in params)) setParam(name, '')
  }
  const entries = Object.entries(params)

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <SectionLabel>Param bindings</SectionLabel>
        <button onClick={addParam}
          className="text-[11px] font-medium px-2 h-6 rounded-lg border border-dashed border-border hover:border-primary text-muted hover:text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50">
          + Add
        </button>
      </div>
      {entries.length === 0 && (
        <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 text-center">
          No params bound.
        </p>
      )}
      {entries.map(([paramName, binding]) => {
        const isRef = binding !== null && typeof binding === 'object' && 'ref' in binding
        return (
          <div key={paramName} className="rounded-lg border border-border p-2 space-y-1.5 bg-surface-2">
            <div className="flex items-center gap-1.5">
              <input type="text" className={`${inputCls} flex-1 font-mono text-xs`} value={paramName}
                onChange={e => {
                  const newName = e.target.value
                  if (!newName || newName === paramName) return
                  const next = {}
                  for (const [k, v] of Object.entries(params)) next[k === paramName ? newName : k] = v
                  onChange({ ...widget, params: next })
                }} placeholder="param name" />
              <button onClick={() => removeParam(paramName)}
                className="text-xs px-1.5 py-0.5 rounded border border-transparent hover:border-border text-muted hover:text-fg transition-colors" title="Remove binding">✕</button>
            </div>
            <div className="flex gap-1">
              <button onClick={() => setParam(paramName, isRef ? '' : { ref: varNames[0] ?? '' })}
                className={`flex-1 text-xs py-0.5 rounded border transition-colors ${isRef ? 'border-primary text-primary bg-surface' : 'border-border text-muted hover:border-primary hover:text-primary'}`}>
                {isRef ? '↔ Variable' : 'Variable'}
              </button>
              <button onClick={() => setParam(paramName, isRef ? '' : binding)}
                className={`flex-1 text-xs py-0.5 rounded border transition-colors ${!isRef ? 'border-primary text-primary bg-surface' : 'border-border text-muted hover:border-primary hover:text-primary'}`}>
                {!isRef ? '↔ Literal' : 'Literal'}
              </button>
            </div>
            {isRef ? (
              <select className={selectCls} value={binding.ref ?? ''} onChange={e => setParam(paramName, { ref: e.target.value })}>
                {varNames.length === 0 && <option value="">— no variables defined —</option>}
                {varNames.map(v => <option key={v} value={v}>{v}</option>)}
                {binding.ref && !varNames.includes(binding.ref) && (
                  <option value={binding.ref}>{binding.ref} (not found)</option>
                )}
              </select>
            ) : (
              <input type="text" className={`${inputCls} font-mono text-xs`} placeholder="literal value"
                value={typeof binding === 'string' ? binding : JSON.stringify(binding)}
                onChange={e => {
                  const raw = e.target.value
                  try { setParam(paramName, JSON.parse(raw)) } catch { setParam(paramName, raw) }
                }} />
            )}
          </div>
        )
      })}
    </div>
  )
}
