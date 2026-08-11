/**
 * VariablesPanel.jsx — the org/project persistent VARIABLES manager.
 *
 * Lists the org/project's stored flow variables and lets the author add / edit
 * / delete them (the long-term `{{ vars.* }}` store, written from python cells
 * via `set_var`). Optionally lets the author INSERT a `{{ vars.NAME }}`
 * reference into a target text field (e.g. the selected cell) — pass an
 * `onInsert(name)` handler to enable the per-row Insert button; omit it for the
 * standalone shell panel where there is no active cell.
 *
 * Used in two places:
 *   - FlowsPage RHS sidebar — as a top-level "Variables" panel (no onInsert).
 *   - NodeInspector — under the selected cell, with onInsert wired to append a
 *     `{{ vars.NAME }}` token into the cell's text field.
 *
 * Robust when the variables API is unavailable: `listVariables` returns [] on
 * failure, so the panel renders an empty state and never crashes. Writes
 * surface their error inline.
 *
 * Props:
 *   onInsert  {(name: string) => void=}  optional — enables the Insert button.
 *   readOnly  {boolean=}                 disables all edit affordances.
 */

import { useState, useCallback, useEffect } from 'react'
import { Variable, Trash2, Check } from 'lucide-react'
import {
  listVariables,
  setVariable,
  deleteVariable,
  VARIABLE_TYPES,
} from '../lib/variables.js'

// ---------------------------------------------------------------------------
// Shared styled primitives (mirror NodeInspector's control styling)
// ---------------------------------------------------------------------------

const inputCls = [
  'w-full h-8 text-sm border border-border rounded-lg px-2.5',
  'bg-surface text-fg placeholder:text-muted/50',
  'focus:outline-none focus:ring-2 focus:ring-ring/60 focus:border-ring/40',
  'hover:border-border/80 transition-colors',
].join(' ')

const selectCls = [
  'w-full h-8 text-sm border border-border rounded-lg pl-2.5 pr-8',
  'bg-surface text-fg appearance-none cursor-pointer',
  'focus:outline-none focus:ring-2 focus:ring-ring/60 focus:border-ring/40',
  'hover:border-border/80 transition-colors',
  'bg-[length:14px] bg-[right_0.5rem_center] bg-no-repeat',
  "bg-[url(data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20viewBox=%220%200%2012%2012%22%20fill=%22none%22%20stroke=%22%238895a8%22%20stroke-width=%221.4%22%20stroke-linecap=%22round%22%20stroke-linejoin=%22round%22%3E%3Cpath%20d=%22M3%204.5%206%207.5%209%204.5%22/%3E%3C/svg%3E)]",
].join(' ')

const textareaCls = [
  'w-full text-sm border border-border rounded-lg px-2.5 py-2',
  'bg-surface text-fg placeholder:text-muted/50 font-mono',
  'focus:outline-none focus:ring-2 focus:ring-ring/60 focus:border-ring/40',
  'hover:border-border/80 transition-colors resize-y min-h-[80px]',
].join(' ')

export default function VariablesPanel({ onInsert = null, readOnly = false }) {
  const [vars, setVars] = useState(null) // null = not loaded yet
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  // Draft for the add/edit form.
  const [draftName, setDraftName] = useState('')
  const [draftValue, setDraftValue] = useState('')
  const [draftType, setDraftType] = useState<'string' | 'number' | 'boolean' | 'json'>('string')

  const refresh = useCallback(() => {
    listVariables().then(rows => setVars(rows ?? []))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const startEdit = (v) => {
    setError(null)
    setDraftName(v.name)
    setDraftType(v.type ?? 'string')
    setDraftValue(
      v.type === 'json'
        ? JSON.stringify(v.value ?? {}, null, 2)
        : v.value == null ? '' : String(v.value),
    )
  }

  const resetDraft = () => {
    setDraftName('')
    setDraftValue('')
    setDraftType('string')
  }

  const save = () => {
    const name = draftName.trim()
    if (!name) { setError('Name is required.'); return }
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name)) {
      setError('Name must start with a letter; letters, numbers and _ only.')
      return
    }
    setBusy(true)
    setError(null)
    setVariable({ name, value: draftValue, type: draftType })
      .then(() => { resetDraft(); refresh() })
      .catch(err => setError(err?.message ?? 'Failed to save variable.'))
      .finally(() => setBusy(false))
  }

  const remove = (name) => {
    setBusy(true)
    setError(null)
    deleteVariable(name)
      .then(() => refresh())
      .catch(err => setError(err?.message ?? 'Failed to delete variable.'))
      .finally(() => setBusy(false))
  }

  const fmtValue = (v) =>
    v.type === 'json'
      ? JSON.stringify(v.value)
      : v.value == null ? '' : String(v.value)

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-muted/70">
        Persistent org/project values referenced in cells as{' '}
        <code className="font-mono bg-surface-2 px-0.5 rounded">{'{{ vars.NAME }}'}</code>{' '}
        and writable from python cells via{' '}
        <code className="font-mono bg-surface-2 px-0.5 rounded">set_var</code>.
      </p>

      {/* Existing variables */}
      {vars === null ? (
        <p className="text-xs text-muted">Loading…</p>
      ) : vars.length === 0 ? (
        <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-3 text-center">
          No variables yet — add one below to reference it from your cells.
        </p>
      ) : (
        <div className="space-y-1.5">
          {vars.map(v => (
            <div
              key={v.name}
              className="flex items-center gap-2 rounded-lg border border-border bg-surface-2/20 px-2.5 py-1.5"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-xs text-fg truncate">{v.name}</span>
                  <span className="shrink-0 text-[9px] uppercase tracking-wide text-muted/60 border border-border rounded px-1 py-px">
                    {v.type}
                  </span>
                </div>
                <p className="font-mono text-[10px] text-muted/70 truncate">{fmtValue(v)}</p>
              </div>
              {onInsert && (
                <button
                  type="button"
                  onClick={() => !readOnly && onInsert(v.name)}
                  disabled={readOnly}
                  title={`Insert {{ vars.${v.name} }} into this cell`}
                  className="shrink-0 px-1.5 py-1 text-[10px] font-medium rounded border border-border bg-surface hover:bg-surface-2 text-muted hover:text-fg transition-colors disabled:opacity-50"
                >
                  Insert
                </button>
              )}
              <button
                type="button"
                onClick={() => startEdit(v)}
                disabled={readOnly}
                title="Edit variable"
                className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-muted/60 hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
                aria-label="Edit variable"
              >
                <Variable size={11} />
              </button>
              <button
                type="button"
                onClick={() => remove(v.name)}
                disabled={readOnly || busy}
                title="Delete variable"
                className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-muted/60 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors disabled:opacity-50"
                aria-label="Delete variable"
              >
                <Trash2 size={11} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Add / edit form */}
      <div className="rounded-lg border border-border bg-surface-2/20 p-3 space-y-2">
        <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-wider">
          {draftName && vars?.some(v => v.name === draftName) ? 'Edit / overwrite' : 'Add variable'}
        </p>
        <div className="grid grid-cols-[1fr_auto] gap-2">
          <input
            type="text"
            className={inputCls}
            value={draftName}
            placeholder="NAME"
            onChange={e => setDraftName(e.target.value)}
            disabled={readOnly}
          />
          <select
            className={[selectCls, 'w-[110px]'].join(' ')}
            value={draftType}
            onChange={e => setDraftType(e.target.value as 'string' | 'number' | 'boolean' | 'json')}
            disabled={readOnly}
          >
            {VARIABLE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        {draftType === 'json' ? (
          <textarea
            className={textareaCls}
            rows={3}
            value={draftValue}
            placeholder={'{ "key": "value" }'}
            onChange={e => setDraftValue(e.target.value)}
            disabled={readOnly}
          />
        ) : draftType === 'boolean' ? (
          <select
            className={selectCls}
            value={String(draftValue) === 'true' ? 'true' : 'false'}
            onChange={e => setDraftValue(e.target.value)}
            disabled={readOnly}
          >
            <option value="false">false</option>
            <option value="true">true</option>
          </select>
        ) : (
          <input
            type={draftType === 'number' ? 'number' : 'text'}
            className={inputCls}
            value={draftValue}
            placeholder={draftType === 'number' ? '0' : 'value'}
            onChange={e => setDraftValue(e.target.value)}
            disabled={readOnly}
          />
        )}
        {error && <p className="text-[10px] text-red-500">{error}</p>}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={save}
            disabled={readOnly || busy || !draftName.trim()}
            className="flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            <Check size={11} />
            Save
          </button>
          {draftName && (
            <button
              type="button"
              onClick={resetDraft}
              disabled={busy}
              className="px-2.5 py-1.5 text-[11px] font-medium rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
            >
              Clear
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
