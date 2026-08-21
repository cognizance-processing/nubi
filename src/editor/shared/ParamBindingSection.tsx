/**
 * shared/ParamBindingSection.tsx — where a widget says what fills its query's
 * parameters.
 *
 * The section is driven by what the query actually declares. The registry
 * already knows every registered query's params, so this lists them by name
 * with their type, and each one asks a single question: is it filled by a
 * filter (a board variable) or by a fixed value? Nobody types a param name —
 * getting it wrong by one character used to leave a widget silently unfiltered.
 *
 * Three things can still be true of a real board, so all three are shown:
 *   - a declared param nothing fills yet — offered a variable to connect to,
 *     or a one-click "add a filter variable" when the board has none by that
 *     name;
 *   - a binding for a param the query no longer declares — kept, flagged, and
 *     removable, never dropped behind the author's back;
 *   - a query whose params we cannot read (unregistered id, registry still
 *     loading) — falls back to naming params by hand, exactly as before.
 *
 * Props:
 *   widget          object    — spec widget
 *   onChange        (w)=>void — full widget update callback
 *   specVariables   array     — spec.variables, for the variable picker
 *   onCreateVariable (name)=>void — declare a board variable (optional; the
 *                                   "connect a filter" shortcut hides without it)
 */

import { Plus, Link2, AlertTriangle } from 'lucide-react'
import { inputCls, selectCls, SectionLabel } from './inspectorPrimitives.jsx'
import { useQueryParams } from './useInspectorData.js'
import { isRef } from '../../dashboards/paramWiring.js'

const NOT_CONNECTED = '__none__'

/** A param row's control: pick a variable, or type a fixed value. */
function BindingControl({ binding, varNames, onPick, onLiteral }) {
  const bound = isRef(binding)
  const mode = bound ? 'variable' : (binding === undefined ? 'unset' : 'literal')

  return (
    <div className="flex items-center gap-1.5">
      <select
        className={`${selectCls} flex-1`}
        value={mode === 'variable' ? (binding.ref || NOT_CONNECTED) : (mode === 'literal' ? '__literal__' : NOT_CONNECTED)}
        onChange={e => {
          const v = e.target.value
          if (v === NOT_CONNECTED) onPick(null)
          else if (v === '__literal__') onLiteral('')
          else onPick(v)
        }}
      >
        <option value={NOT_CONNECTED}>Not connected</option>
        {varNames.length > 0 && (
          <optgroup label="Filter variable">
            {varNames.map(v => <option key={v} value={v}>{v}</option>)}
          </optgroup>
        )}
        {mode === 'variable' && binding.ref && !varNames.includes(binding.ref) && (
          <option value={binding.ref}>{binding.ref} — not on this board</option>
        )}
        <optgroup label="Or">
          <option value="__literal__">Fixed value…</option>
        </optgroup>
      </select>
      {mode === 'literal' && (
        <input
          type="text"
          className={`${inputCls} flex-1 font-mono text-xs`}
          placeholder="value"
          value={typeof binding === 'string' ? binding : JSON.stringify(binding ?? '')}
          onChange={e => {
            const raw = e.target.value
            try { onLiteral(JSON.parse(raw)) } catch { onLiteral(raw) }
          }}
        />
      )}
    </div>
  )
}

export function ParamBindingSection({ widget, onChange, specVariables, onCreateVariable = undefined }: {
  widget: Record<string, any>
  onChange: (widget: Record<string, any>) => void
  specVariables?: Array<{ name: string }>
  onCreateVariable?: (name: string) => void
}) {
  const params: Record<string, any> = widget.params ?? {}
  const varNames = (specVariables ?? []).map(v => v.name).filter(Boolean)
  const { params: declared, loaded } = useQueryParams(widget.query_id)

  const setParam = (paramName: string, value: any) =>
    onChange({ ...widget, params: { ...params, [paramName]: value } })
  const removeParam = (paramName: string) => {
    const next = { ...params }
    delete next[paramName]
    onChange({ ...widget, params: next })
  }
  const renameParam = (from: string, to: string) => {
    if (!to || to === from) return
    const next: Record<string, any> = {}
    for (const [k, v] of Object.entries(params)) next[k === from ? to : k] = v
    onChange({ ...widget, params: next })
  }

  const declaredNames = Array.isArray(declared) ? declared.map(p => p.name).filter(Boolean) : []
  // Bindings the query no longer declares — kept visible rather than dropped.
  const extraNames = Object.keys(params).filter(n => !declaredNames.includes(n))

  // ── Fallback: params unknown (unregistered query, or still loading) ───────
  if (!Array.isArray(declared)) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 leading-relaxed">
          {!widget.query_id
            ? 'Pick a query first — its parameters appear here.'
            : loaded
              ? "This query isn't in the registry, so its parameters can't be listed. Add them by name below."
              : 'Reading this query’s parameters…'}
        </p>
        {extraNames.map(paramName => (
          <ManualRow key={paramName} paramName={paramName} binding={params[paramName]}
            varNames={varNames} onRename={renameParam} onRemove={removeParam} onSet={setParam} />
        ))}
        {loaded && widget.query_id && (
          <button
            onClick={() => { const n = `param${Object.keys(params).length + 1}`; if (!(n in params)) setParam(n, '') }}
            className="w-full text-[11px] font-medium px-2 h-7 rounded-lg border border-dashed border-border hover:border-primary text-muted hover:text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50">
            <Plus size={11} className="inline -mt-px mr-1" />Add a parameter by name
          </button>
        )}
      </div>
    )
  }

  // ── Normal path: the query told us its params ────────────────────────────
  return (
    <div className="space-y-2" data-testid="param-bindings">
      {declaredNames.length === 0 && extraNames.length === 0 && (
        <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 text-center leading-relaxed">
          This query takes no parameters, so no filter can narrow it.
          <br />
          <span className="text-muted/60">Add a <span className="font-mono">{'{{name}}'}</span> placeholder to its SQL to make it filterable.</span>
        </p>
      )}

      {declared.map(p => {
        const binding = params[p.name]
        const connected = isRef(binding)
        const canOfferVariable = !connected && !varNames.includes(p.name) && !!onCreateVariable
        return (
          <div key={p.name} className="rounded-lg border border-border p-2 space-y-1.5 bg-surface-2"
            data-testid={`param-row-${p.name}`}>
            <div className="flex items-center gap-1.5">
              <span className="flex-1 text-xs font-mono text-fg truncate" title={p.name}>{p.name}</span>
              {p.type && <span className="text-[10px] uppercase tracking-wide text-muted/70">{p.type}</span>}
              {p.required && <span className="text-[10px] uppercase tracking-wide text-warning">required</span>}
            </div>
            <BindingControl
              binding={binding}
              varNames={varNames}
              onPick={v => (v === null ? removeParam(p.name) : setParam(p.name, { ref: v }))}
              onLiteral={v => setParam(p.name, v)}
            />
            {connected && (
              <p className="text-[10px] text-primary/80 flex items-center gap-1">
                <Link2 size={10} /> filled by the <span className="font-mono">{binding.ref}</span> filter
              </p>
            )}
            {canOfferVariable && (
              <button
                onClick={() => { onCreateVariable(p.name); setParam(p.name, { ref: p.name }) }}
                className="text-[10px] text-muted hover:text-primary underline underline-offset-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50 rounded">
                Connect a filter called “{p.name}”
              </button>
            )}
          </div>
        )
      })}

      {extraNames.length > 0 && (
        <div className="space-y-1.5 pt-1">
          <SectionLabel>Not declared by this query</SectionLabel>
          <p className="text-[10px] text-muted/70 flex items-start gap-1 leading-relaxed">
            <AlertTriangle size={11} className="mt-px flex-none text-warning" />
            These are sent anyway and ignored. They usually mean the query was edited — remove them, or rename to match.
          </p>
          {extraNames.map(paramName => (
            <ManualRow key={paramName} paramName={paramName} binding={params[paramName]}
              varNames={varNames} onRename={renameParam} onRemove={removeParam} onSet={setParam} />
          ))}
        </div>
      )}
    </div>
  )
}

/** A row for a param the query doesn't declare — name stays editable. */
function ManualRow({ paramName, binding, varNames, onRename, onRemove, onSet }) {
  return (
    <div className="rounded-lg border border-border p-2 space-y-1.5 bg-surface-2">
      <div className="flex items-center gap-1.5">
        <input type="text" className={`${inputCls} flex-1 font-mono text-xs`} value={paramName}
          onChange={e => onRename(paramName, e.target.value)} placeholder="param name" />
        <button onClick={() => onRemove(paramName)} title="Remove binding"
          className="text-xs px-1.5 py-0.5 rounded border border-transparent hover:border-border text-muted hover:text-fg transition-colors">✕</button>
      </div>
      <BindingControl
        binding={binding}
        varNames={varNames}
        onPick={v => (v === null ? onRemove(paramName) : onSet(paramName, { ref: v }))}
        onLiteral={v => onSet(paramName, v)}
      />
    </div>
  )
}
