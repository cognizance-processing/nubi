/**
 * NodeInspector.jsx — right-hand drawer for editing a selected task node.
 *
 * Editable fields:
 *   - key (text, unique slug)
 *   - kind (select: query | python | agent | noop)
 *   - needs (read-only, derived from edges)
 *   - kind-specific config:
 *       query:  query_id (text) OR sql (textarea)
 *       python: code (@monaco-editor/react)
 *       agent:  prompt (textarea), max_steps (number)
 *       noop:   (nothing)
 *   - retries (number)
 *   - timeout_s (number)
 *   - cache_ttl_s (number)
 *
 * Props:
 *   task      {object}  — the task spec object (from node.data.task)
 *   onChange  {Function(updatedTask)} — called when any field changes
 *   onClose   {Function}             — called to deselect / close drawer
 */

import { useState, useCallback } from 'react'
import { X, ChevronDown } from 'lucide-react'
import Editor from '@monaco-editor/react'

// ---------------------------------------------------------------------------
// Shared styled primitives
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

function FieldLabel({ children }) {
  return <label className="block text-[11px] font-medium text-muted mb-1">{children}</label>
}

function NumberField({ label, value, onChange, min = 0, placeholder = '' }) {
  return (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <input
        type="number"
        min={min}
        className={inputCls}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value === '' ? 0 : parseInt(e.target.value, 10) || 0)}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Kind-specific config panels
// ---------------------------------------------------------------------------

function QueryConfig({ config, onChange }) {
  const useQueryId = config.query_id !== undefined || !config.sql
  return (
    <div className="space-y-3">
      {/* Toggle: query_id vs raw SQL */}
      <div className="flex h-8 rounded-lg border border-border overflow-hidden">
        {['query_id', 'sql'].map(mode => {
          const active = mode === 'query_id' ? useQueryId : !useQueryId
          return (
            <button
              key={mode}
              onClick={() => {
                if (mode === 'query_id') onChange({ query_id: config.query_id ?? '', sql: undefined })
                else onChange({ sql: config.sql ?? '', query_id: undefined })
              }}
              className={[
                'flex-1 text-xs font-medium transition-colors capitalize',
                active ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-primary',
              ].join(' ')}
            >
              {mode === 'query_id' ? 'Query ID' : 'Raw SQL'}
            </button>
          )
        })}
      </div>

      {useQueryId ? (
        <div>
          <FieldLabel>Query ID</FieldLabel>
          <input
            type="text"
            className={inputCls}
            value={config.query_id ?? ''}
            placeholder="e.g. demo_all"
            onChange={e => onChange({ ...config, query_id: e.target.value })}
          />
        </div>
      ) : (
        <div>
          <FieldLabel>SQL</FieldLabel>
          <textarea
            className={textareaCls}
            rows={5}
            value={config.sql ?? ''}
            placeholder="SELECT * FROM ..."
            onChange={e => onChange({ ...config, sql: e.target.value })}
          />
        </div>
      )}
    </div>
  )
}

function PythonConfig({ config, onChange }) {
  return (
    <div>
      <FieldLabel>Python code</FieldLabel>
      <p className="text-[10px] text-muted mb-1.5">
        Variables available: <code className="font-mono bg-surface-2 px-1 rounded">inputs</code>, <code className="font-mono bg-surface-2 px-1 rounded">params</code>. Bind result to <code className="font-mono bg-surface-2 px-1 rounded">result</code>.
      </p>
      <div className="rounded-lg border border-border overflow-hidden" style={{ height: 220 }}>
        <Editor
          defaultLanguage="python"
          value={config.code ?? '# Write your task code here\nresult = {}'}
          onChange={val => onChange({ ...config, code: val ?? '' })}
          theme="vs-dark"
          options={{
            fontSize: 12,
            minimap: { enabled: false },
            lineNumbers: 'on',
            scrollBeyondLastLine: false,
            padding: { top: 8, bottom: 8 },
            wordWrap: 'on',
          }}
        />
      </div>
    </div>
  )
}

function AgentConfig({ config, onChange }) {
  return (
    <div className="space-y-3">
      <div>
        <FieldLabel>Prompt</FieldLabel>
        <textarea
          className={textareaCls}
          rows={5}
          value={config.prompt ?? ''}
          placeholder="Describe what the agent should do…"
          onChange={e => onChange({ ...config, prompt: e.target.value })}
        />
      </div>
      <div>
        <FieldLabel>Max steps</FieldLabel>
        <input
          type="number"
          min={1}
          max={20}
          className={inputCls}
          value={config.max_steps ?? 4}
          onChange={e => onChange({ ...config, max_steps: parseInt(e.target.value, 10) || 4 })}
        />
      </div>
    </div>
  )
}

function NoopConfig() {
  return (
    <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-3 text-center">
      No config required — noop tasks pass through upstream results.
    </p>
  )
}

// ---------------------------------------------------------------------------
// NodeInspector
// ---------------------------------------------------------------------------

const KINDS = ['query', 'python', 'agent', 'noop']

export default function NodeInspector({ task, onChange, onClose }) {
  // Validate key locally (only on blur to avoid stuttering)
  const [keyError, setKeyError] = useState(null)

  const setField = useCallback((field, value) => {
    onChange({ ...task, [field]: value })
  }, [task, onChange])

  const setConfig = useCallback((newConfig) => {
    onChange({ ...task, config: newConfig })
  }, [task, onChange])

  if (!task) return null

  const needs = task.needs ?? []
  const config = task.config ?? {}

  const validateKey = (val) => {
    if (!val) { setKeyError('Key is required'); return }
    if (!/^[a-z][a-z0-9_]*$/.test(val)) {
      setKeyError('Key must be lowercase alphanumeric + underscores, starting with a letter')
    } else {
      setKeyError(null)
    }
  }

  return (
    <aside className="flex flex-col h-full border-l border-border bg-surface overflow-hidden">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-border">
        <div>
          <h3 className="text-sm font-semibold text-fg">Task inspector</h3>
          <p className="text-[11px] text-muted font-mono mt-0.5">{task.key}</p>
        </div>
        <button
          onClick={onClose}
          className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          title="Close inspector"
        >
          <X size={15} />
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">

        {/* ── Identity ─────────────────────────────────────────── */}
        <section className="space-y-3">
          <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-widest">Identity</p>

          <div>
            <FieldLabel>Key</FieldLabel>
            <input
              type="text"
              className={[inputCls, keyError ? 'border-red-400 focus:ring-red-300/60' : ''].join(' ')}
              value={task.key ?? ''}
              onChange={e => setField('key', e.target.value)}
              onBlur={e => validateKey(e.target.value)}
              placeholder="e.g. pull_data"
            />
            {keyError && <p className="text-[10px] text-red-500 mt-1">{keyError}</p>}
          </div>

          <div>
            <FieldLabel>Kind</FieldLabel>
            <select
              className={selectCls}
              value={task.kind ?? 'noop'}
              onChange={e => {
                const kind = e.target.value
                // Reset config to a sensible default when switching kinds
                const defaultConfigs = {
                  query: { query_id: '' },
                  python: { code: '# Write your task code here\nresult = {}' },
                  agent: { prompt: '', max_steps: 4 },
                  noop: {},
                }
                onChange({ ...task, kind, config: defaultConfigs[kind] ?? {} })
              }}
            >
              {KINDS.map(k => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>

          {needs.length > 0 && (
            <div>
              <FieldLabel>Needs (upstream tasks)</FieldLabel>
              <div className="flex flex-wrap gap-1.5 mt-1">
                {needs.map(n => (
                  <span
                    key={n}
                    className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-mono bg-surface-2 border border-border text-fg"
                  >
                    {n}
                  </span>
                ))}
              </div>
              <p className="text-[10px] text-muted/60 mt-1">Connect / disconnect edges on the canvas to change dependencies.</p>
            </div>
          )}
        </section>

        {/* ── Kind-specific config ─────────────────────────────── */}
        <section className="space-y-3">
          <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-widest">Config</p>
          {task.kind === 'query'  && <QueryConfig  config={config} onChange={setConfig} />}
          {task.kind === 'python' && <PythonConfig config={config} onChange={setConfig} />}
          {task.kind === 'agent'  && <AgentConfig  config={config} onChange={setConfig} />}
          {(task.kind === 'noop' || !task.kind) && <NoopConfig />}
        </section>

        {/* ── Execution settings ───────────────────────────────── */}
        <section className="space-y-3">
          <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-widest">Execution</p>

          <div className="grid grid-cols-2 gap-3">
            <NumberField
              label="Retries"
              value={task.retries}
              onChange={v => setField('retries', v)}
              min={0}
              placeholder="0"
            />
            <NumberField
              label="Backoff (s)"
              value={task.retry_backoff_s}
              onChange={v => setField('retry_backoff_s', v)}
              min={0}
              placeholder="30"
            />
            <NumberField
              label="Timeout (s)"
              value={task.timeout_s}
              onChange={v => setField('timeout_s', v)}
              min={1}
              placeholder="60"
            />
            <NumberField
              label="Cache TTL (s)"
              value={task.cache_ttl_s}
              onChange={v => setField('cache_ttl_s', v)}
              min={0}
              placeholder="0 = off"
            />
          </div>
        </section>

      </div>
    </aside>
  )
}
