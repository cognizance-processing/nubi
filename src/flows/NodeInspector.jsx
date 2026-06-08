/**
 * NodeInspector.jsx — right-hand drawer for editing a selected task node.
 *
 * Editable fields:
 *   - key (text, unique slug)
 *   - kind (select: query | python | agent | extract | bucket_load | noop | map | branch)
 *   - needs (read-only, derived from edges)
 *   - kind-specific config:
 *       query:       query_id (text) OR sql (textarea)
 *       python:      code (@monaco-editor/react)
 *       agent:       prompt (textarea), max_steps (number)
 *       extract:     source_uri | input, dest_uri, secret (select), format (auto|zip|tar|tar.gz)
 *       bucket_load: uri, secret (select), format, source
 *       noop:        (nothing)
 *       map:         item_expr, item_var, max_concurrency, max_map_size, collect_key, body (JSON)
 *       branch:      conditions list editor (when, next[]), default list
 *   - retries (number)
 *   - timeout_s (number)
 *   - cache_ttl_s (number)
 *
 * Props:
 *   task      {object}  — the task spec object (from node.data.task)
 *   onChange  {Function(updatedTask)} — called when any field changes
 *   onClose   {Function}             — called to deselect / close drawer
 */

import { useState, useCallback, useEffect } from 'react'
import { X, ChevronDown, Plus, Trash2 } from 'lucide-react'
import Editor from '@monaco-editor/react'
import { listSecrets } from '../lib/secrets.js'

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
// SecretSelect — dropdown populated from GET /secrets
// ---------------------------------------------------------------------------

/**
 * A <select> that loads secret names from the backend and lets the user pick
 * one. The chosen value is the secret NAME (string), resolved server-side.
 *
 * @param {{ value: string, onChange: (name: string) => void }} props
 */
function SecretSelect({ value, onChange }) {
  const [secrets, setSecrets] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    listSecrets().then(data => {
      if (!cancelled) {
        setSecrets(data)
        setLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="relative">
      <select
        className={selectCls}
        value={value ?? ''}
        onChange={e => onChange(e.target.value)}
        disabled={loading}
      >
        <option value="">— none (no credentials) —</option>
        {secrets.map(s => (
          <option key={s.name} value={s.name}>{s.name}</option>
        ))}
        {loading && <option disabled>Loading…</option>}
      </select>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ExtractConfig — source/dest archive extraction task
// ---------------------------------------------------------------------------

const EXTRACT_FORMATS = ['auto', 'zip', 'tar', 'tar.gz']

/**
 * Config panel for the 'extract' task kind.
 *
 * Fields:
 *   source_uri  — cloud/local URI to the archive, OR empty to use an upstream input
 *   input       — upstream input key to use instead of source_uri (mutually exclusive)
 *   dest_uri    — where to write extracted files
 *   secret      — secret name for storage credentials (resolved server-side)
 *   format      — archive format: auto | zip | tar | tar.gz
 */
function ExtractConfig({ config, onChange }) {
  const useInput = config.input !== undefined && config.source_uri === undefined

  return (
    <div className="space-y-3">
      {/* Source toggle: URI vs upstream input */}
      <div className="flex h-8 rounded-lg border border-border overflow-hidden">
        {['source_uri', 'input'].map(mode => {
          const active = mode === 'source_uri' ? !useInput : useInput
          return (
            <button
              key={mode}
              onClick={() => {
                if (mode === 'source_uri') {
                  onChange({ ...config, source_uri: config.source_uri ?? '', input: undefined })
                } else {
                  onChange({ ...config, input: config.input ?? '', source_uri: undefined })
                }
              }}
              className={[
                'flex-1 text-xs font-medium transition-colors capitalize',
                active ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-primary',
              ].join(' ')}
            >
              {mode === 'source_uri' ? 'URI' : 'Input key'}
            </button>
          )
        })}
      </div>

      {/* Source URI or input key */}
      {!useInput ? (
        <div>
          <FieldLabel>Source URI</FieldLabel>
          <input
            type="text"
            className={inputCls}
            value={config.source_uri ?? ''}
            placeholder="s3://bucket/archive.zip"
            onChange={e => onChange({ ...config, source_uri: e.target.value })}
          />
        </div>
      ) : (
        <div>
          <FieldLabel>Input key</FieldLabel>
          <input
            type="text"
            className={inputCls}
            value={config.input ?? ''}
            placeholder="e.g. upstream_task"
            onChange={e => onChange({ ...config, input: e.target.value })}
          />
          <p className="text-[10px] text-muted mt-1">
            Key of the upstream task whose result provides the archive path.
          </p>
        </div>
      )}

      {/* Dest URI */}
      <div>
        <FieldLabel>Destination URI</FieldLabel>
        <input
          type="text"
          className={inputCls}
          value={config.dest_uri ?? ''}
          placeholder="s3://bucket/extracted/"
          onChange={e => onChange({ ...config, dest_uri: e.target.value })}
        />
      </div>

      {/* Secret */}
      <div>
        <FieldLabel>Credentials secret</FieldLabel>
        <SecretSelect
          value={config.secret ?? ''}
          onChange={name => onChange({ ...config, secret: name })}
        />
        <p className="text-[10px] text-muted mt-1">
          Secret holding cloud credentials. Resolved server-side at run time.
        </p>
      </div>

      {/* Format */}
      <div>
        <FieldLabel>Archive format</FieldLabel>
        <div className="relative">
          <select
            className={selectCls}
            value={config.format ?? 'auto'}
            onChange={e => onChange({ ...config, format: e.target.value })}
          >
            {EXTRACT_FORMATS.map(f => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BucketLoadConfig — load a file from cloud storage into a table/dataset
// ---------------------------------------------------------------------------

/**
 * Config panel for the 'bucket_load' task kind.
 *
 * Fields:
 *   uri     — cloud storage URI for the source file
 *   secret  — secret name for storage credentials (resolved server-side)
 *   format  — file format (parquet|csv|json|ndjson|orc|avro)
 *   source  — optional source hint (connector id / table name / etc.)
 */
function BucketLoadConfig({ config, onChange }) {
  const FORMATS = ['parquet', 'csv', 'json', 'ndjson', 'orc', 'avro']

  return (
    <div className="space-y-3">
      {/* URI */}
      <div>
        <FieldLabel>Source URI</FieldLabel>
        <input
          type="text"
          className={inputCls}
          value={config.uri ?? ''}
          placeholder="s3://bucket/data.parquet"
          onChange={e => onChange({ ...config, uri: e.target.value })}
        />
        <p className="text-[10px] text-muted mt-1">
          Supports <code className="font-mono bg-surface-2 px-0.5 rounded">s3://</code>,{' '}
          <code className="font-mono bg-surface-2 px-0.5 rounded">gs://</code>,{' '}
          <code className="font-mono bg-surface-2 px-0.5 rounded">az://</code>, and{' '}
          <code className="font-mono bg-surface-2 px-0.5 rounded">file://</code> schemes.
        </p>
      </div>

      {/* Secret */}
      <div>
        <FieldLabel>Credentials secret</FieldLabel>
        <SecretSelect
          value={config.secret ?? ''}
          onChange={name => onChange({ ...config, secret: name })}
        />
        <p className="text-[10px] text-muted mt-1">
          Secret holding cloud credentials. Resolved server-side at run time.
        </p>
      </div>

      {/* Format */}
      <div>
        <FieldLabel>File format</FieldLabel>
        <div className="relative">
          <select
            className={selectCls}
            value={config.format ?? 'parquet'}
            onChange={e => onChange({ ...config, format: e.target.value })}
          >
            {FORMATS.map(f => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Source */}
      <div>
        <FieldLabel>Source hint (optional)</FieldLabel>
        <input
          type="text"
          className={inputCls}
          value={config.source ?? ''}
          placeholder="e.g. connector_id or table name"
          onChange={e => onChange({ ...config, source: e.target.value })}
        />
        <p className="text-[10px] text-muted mt-1">
          Optional: connector ID, destination table, or other loader hint.
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MapConfig — fan-out map node config panel
// ---------------------------------------------------------------------------

/**
 * Config panel for the 'map' task kind.
 *
 * Fields:
 *   item_expr       — template expression resolving to the iterable at runtime
 *   item_var        — variable name for item fields in body configs (default: "item")
 *   max_concurrency — max simultaneous child executions (0 = unlimited)
 *   max_map_size    — hard cap on item count (default: 1000)
 *   collect_key     — which body task key's result is collected
 *   body            — JSON editor for the nested sub-DAG body tasks
 */
function MapConfig({ config, onChange }) {
  const [bodyError, setBodyError] = useState(null)
  const [bodyText, setBodyText] = useState(
    () => JSON.stringify(config.body ?? [], null, 2)
  )

  // Keep bodyText in sync if config.body changes from outside
  useEffect(() => {
    setBodyText(JSON.stringify(config.body ?? [], null, 2))
  }, [config.body])

  const handleBodyChange = (text) => {
    setBodyText(text)
    try {
      const parsed = JSON.parse(text)
      if (!Array.isArray(parsed)) throw new Error('Body must be a JSON array of task objects.')
      setBodyError(null)
      onChange({ ...config, body: parsed })
    } catch (err) {
      setBodyError(err.message)
    }
  }

  return (
    <div className="space-y-3">
      {/* item_expr */}
      <div>
        <FieldLabel>Item expression</FieldLabel>
        <input
          type="text"
          className={inputCls}
          value={config.item_expr ?? ''}
          placeholder={`{{ inputs.task_key.rows }}`}
          onChange={e => onChange({ ...config, item_expr: e.target.value })}
        />
        <p className="text-[10px] text-muted mt-1">
          Template expression that resolves to a list at runtime.{' '}
          E.g. <code className="font-mono bg-surface-2 px-0.5 rounded">{`{{ inputs.get_regions.rows }}`}</code>
        </p>
      </div>

      {/* item_var */}
      <div>
        <FieldLabel>Item variable name</FieldLabel>
        <input
          type="text"
          className={inputCls}
          value={config.item_var ?? 'item'}
          placeholder="item"
          onChange={e => onChange({ ...config, item_var: e.target.value || 'item' })}
        />
        <p className="text-[10px] text-muted mt-1">
          Bound as <code className="font-mono bg-surface-2 px-0.5 rounded">{'{{ item.<field> }}'}</code> in body task configs.
        </p>
      </div>

      {/* max_concurrency + max_map_size */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel>Max concurrency</FieldLabel>
          <input
            type="number"
            min={0}
            className={inputCls}
            value={config.max_concurrency ?? 0}
            placeholder="0 = unlimited"
            onChange={e => onChange({ ...config, max_concurrency: parseInt(e.target.value, 10) || 0 })}
          />
        </div>
        <div>
          <FieldLabel>Max map size</FieldLabel>
          <input
            type="number"
            min={1}
            className={inputCls}
            value={config.max_map_size ?? 1000}
            placeholder="1000"
            onChange={e => onChange({ ...config, max_map_size: parseInt(e.target.value, 10) || 1000 })}
          />
        </div>
      </div>

      {/* collect_key */}
      <div>
        <FieldLabel>Collect key (body task)</FieldLabel>
        <input
          type="text"
          className={inputCls}
          value={config.collect_key ?? ''}
          placeholder="last body task key (default)"
          onChange={e => onChange({ ...config, collect_key: e.target.value || undefined })}
        />
        <p className="text-[10px] text-muted mt-1">
          Which body task key&apos;s result is collected into the output list.
          Leave blank to use the last task in the body.
        </p>
      </div>

      {/* body JSON editor */}
      <div>
        <FieldLabel>Body tasks (JSON)</FieldLabel>
        <p className="text-[10px] text-muted mb-1.5">
          Array of TaskSpec objects forming the sub-DAG executed per item.{' '}
          Body tasks may reference <code className="font-mono bg-surface-2 px-0.5 rounded">{'{{ item.<field> }}'}</code>.
        </p>
        <div className={['rounded-lg border overflow-hidden', bodyError ? 'border-red-400' : 'border-border'].join(' ')} style={{ height: 200 }}>
          <Editor
            defaultLanguage="json"
            value={bodyText}
            onChange={val => handleBodyChange(val ?? '[]')}
            theme="vs-dark"
            options={{
              fontSize: 11,
              minimap: { enabled: false },
              lineNumbers: 'off',
              scrollBeyondLastLine: false,
              padding: { top: 6, bottom: 6 },
              wordWrap: 'on',
              folding: true,
            }}
          />
        </div>
        {bodyError && (
          <p className="text-[10px] text-red-500 mt-1">{bodyError}</p>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BranchConfig — conditional routing node config panel
// ---------------------------------------------------------------------------

/**
 * Config panel for the 'branch' task kind.
 *
 * Fields:
 *   conditions  — ordered list of { when: string, next: string[] }
 *   default     — list of task keys to activate when no condition matches
 */
function BranchConfig({ config, onChange }) {
  const conditions = config.conditions ?? []
  const defaultNext = config.default ?? []

  const setConditions = (newConds) => {
    onChange({ ...config, conditions: newConds })
  }

  const setDefault = (text) => {
    // Parse comma-separated keys
    const keys = text.split(',').map(s => s.trim()).filter(Boolean)
    onChange({ ...config, default: keys })
  }

  const addCondition = () => {
    setConditions([...conditions, { when: '', next: [] }])
  }

  const removeCondition = (i) => {
    const next = [...conditions]
    next.splice(i, 1)
    setConditions(next)
  }

  const updateCondition = (i, field, value) => {
    const next = conditions.map((c, idx) => idx === i ? { ...c, [field]: value } : c)
    setConditions(next)
  }

  const updateConditionNext = (i, text) => {
    const keys = text.split(',').map(s => s.trim()).filter(Boolean)
    updateCondition(i, 'next', keys)
  }

  return (
    <div className="space-y-4">
      {/* Conditions list */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <FieldLabel>Conditions (ordered — first match wins)</FieldLabel>
          <button
            onClick={addCondition}
            className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded-md border border-dashed border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            title="Add condition"
          >
            <Plus size={10} />
            Add
          </button>
        </div>

        {conditions.length === 0 && (
          <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-3 text-center">
            No conditions yet — click &ldquo;Add&rdquo; to create the first branch.
          </p>
        )}

        <div className="space-y-3">
          {conditions.map((cond, i) => (
            <div
              key={i}
              className="rounded-lg border border-border bg-surface-2/20 p-3 space-y-2"
            >
              {/* Condition header: index + remove */}
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold text-muted/70 uppercase tracking-wider">
                  Condition {i} {i === 0 ? '(then)' : i === 1 ? '(else)' : ''}
                </span>
                <button
                  onClick={() => removeCondition(i)}
                  className="w-5 h-5 flex items-center justify-center rounded text-muted/60 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                  title="Remove condition"
                  aria-label="Remove condition"
                >
                  <Trash2 size={10} />
                </button>
              </div>

              {/* when expression */}
              <div>
                <FieldLabel>When (boolean expression)</FieldLabel>
                <input
                  type="text"
                  className={inputCls}
                  value={cond.when ?? ''}
                  placeholder={`{{ inputs.task.field == 'value' }}`}
                  onChange={e => updateCondition(i, 'when', e.target.value)}
                />
                <p className="text-[10px] text-muted mt-1">
                  Template expression evaluated as a Python boolean after{' '}
                  <code className="font-mono bg-surface-2 px-0.5 rounded">{'{{ }}'}</code> substitution.
                </p>
              </div>

              {/* next task keys */}
              <div>
                <FieldLabel>Activate tasks (comma-separated keys)</FieldLabel>
                <input
                  type="text"
                  className={inputCls}
                  value={(cond.next ?? []).join(', ')}
                  placeholder="task_key_1, task_key_2"
                  onChange={e => updateConditionNext(i, e.target.value)}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Default branch */}
      <div>
        <FieldLabel>Default (no condition matched)</FieldLabel>
        <input
          type="text"
          className={inputCls}
          value={defaultNext.join(', ')}
          placeholder="task_key (optional — leave blank to fail on no match)"
          onChange={e => setDefault(e.target.value)}
        />
        <p className="text-[10px] text-muted mt-1">
          Task keys to activate when no condition matches.
          Leave blank to mark downstream tasks <code className="font-mono bg-surface-2 px-0.5 rounded">upstream_failed</code>.
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// NodeInspector
// ---------------------------------------------------------------------------

const KINDS = ['query', 'python', 'agent', 'extract', 'bucket_load', 'noop', 'map', 'branch']

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
                  query:       { query_id: '' },
                  python:      { code: '# Write your task code here\nresult = {}' },
                  agent:       { prompt: '', max_steps: 4 },
                  extract:     { source_uri: '', dest_uri: '', secret: '', format: 'auto' },
                  bucket_load: { uri: '', secret: '', format: 'parquet', source: '' },
                  noop:        {},
                  map:         { item_expr: '', item_var: 'item', max_concurrency: 0, max_map_size: 1000, collect_key: '', body: [] },
                  branch:      { conditions: [], default: [] },
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
          {task.kind === 'query'       && <QueryConfig      config={config} onChange={setConfig} />}
          {task.kind === 'python'      && <PythonConfig     config={config} onChange={setConfig} />}
          {task.kind === 'agent'       && <AgentConfig      config={config} onChange={setConfig} />}
          {task.kind === 'extract'     && <ExtractConfig    config={config} onChange={setConfig} />}
          {task.kind === 'bucket_load' && <BucketLoadConfig config={config} onChange={setConfig} />}
          {task.kind === 'map'         && <MapConfig        config={config} onChange={setConfig} />}
          {task.kind === 'branch'      && <BranchConfig     config={config} onChange={setConfig} />}
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
