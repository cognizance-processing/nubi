/**
 * CanvasEditor.jsx — Three-pane IDE for editing CanvasDoc documents.
 *
 * Layout (three panes):
 *   LEFT/CENTER (tabbed):
 *     (a) CODE tab — HTML editor (CodeMirror via Monaco html mode), debounced
 *         → sanitized preview. Click-to-select an element outlines it and
 *         focuses the right inspector.
 *     (b) PREVIEW tab — CanvasRenderer live preview; click-to-select an element
 *         with a data-el-id outlines it and opens its inspector.
 *   Selection state is shared between code + preview tabs.
 *
 *   RIGHT INSPECTOR:
 *     - Element header (el id)
 *     - Binding-kind switch: None / Query / Metric / API
 *     - Query sub-panel: QueryPicker + useColumnIntrospection → field/format/params
 *     - Metric sub-panel
 *     - API connector picker + path + JSONPath select + test button
 *     - Chart encoding (nubi-chart elements)
 *     - Conditional-format / signal rules (via TableConfig primitives)
 *     - Insert-element palette: Add KPI / Table / Chart / Metric / Filter / Text
 *
 *   TOP BAR:
 *     - Title input
 *     - Save (PUT /canvases/{id})
 *     - Undo / Redo (history.js)
 *     - Variable manager
 *     - Ask-AI panel (POST /ai/canvas/edit → swap doc+bindings)
 *     - Schedule dialog (POST /canvases/{id}/schedule)
 *     - Share button
 *
 * Route: /canvas/:id (registered in App.jsx)
 *
 * INVARIANTS:
 *   - All preview HTML passes through CanvasRenderer (never bypassed).
 *   - Bindings live in doc.bindings[el_id]; HTML is NOT rewritten when a binding
 *     changes. Only the bindings map is mutated.
 *   - RLS: data flows through runArrowQueryById inside CanvasRenderer.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Editor from '@monaco-editor/react'
import {
  Save, Undo2, Redo2, Eye, Code2, Plus, X, ChevronDown, ChevronRight,
  Loader2, Check, AlertCircle, Settings2, Share2, Calendar, Sparkles,
  Hash, Table2, BarChart3, TrendingUp, Filter as FilterIcon, Type,
  Database, Zap, Link2, TestTube2, Variable, Trash2,
} from 'lucide-react'

import { get, post, put } from '../lib/api.js'
import CanvasRenderer from '../dashboards/CanvasRenderer.jsx'
import { useTheme } from '../contexts/ThemeContext.jsx'
import {
  createHistory,
  push as historyPush,
  undo as historyUndo,
  redo as historyRedo,
  canUndo,
  canRedo,
} from './history.js'
import {
  inputCls, selectCls,
  FieldLabel, SectionLabel, Section, ToggleRow,
  useColumnIntrospection, useMetricsList,
  QueryPicker,
  ParamBindingSection,
} from './shared/index.js'
import Spinner from '../components/ui/Spinner.jsx'
import { toast } from '../components/ui/Toast.jsx'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BINDING_KINDS = ['none', 'query', 'metric', 'api']

const ELEMENT_STUBS = {
  kpi: (id) => `<nubi-kpi data-el-id="${id}" label="KPI" format="number"></nubi-kpi>`,
  table: (id) => `<nubi-table data-el-id="${id}" limit="25"></nubi-table>`,
  chart: (id) => `<nubi-chart data-el-id="${id}" type="bar"></nubi-chart>`,
  metric: (id) => `<nubi-kpi data-el-id="${id}" label="Metric" format="number"></nubi-kpi>`,
  filter: (id) => `<nubi-filter data-el-id="${id}"></nubi-filter>`,
  text: (id) => `<p data-el-id="${id}">Text block</p>`,
}

// Generate a short unique element id (el_xxxxxxxx)
function genElId(prefix = 'el') {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`
}

// ---------------------------------------------------------------------------
// Empty/default canvas doc
// ---------------------------------------------------------------------------

const EMPTY_DOC = {
  version: 1,
  title: 'Untitled Canvas',
  html: `<section style="padding:2rem;font-family:sans-serif;">
  <h1 style="font-size:1.5rem;font-weight:700;margin:0 0 1rem;">New Canvas</h1>
  <p style="color:#6b7280;">Add elements using the palette on the right, or edit the HTML directly.</p>
</section>`,
  bindings: {},
  variables: [],
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extract all data-el-id values from an HTML string. */
function extractElIds(html) {
  const re = /data-el-id="([^"]+)"/g
  const ids = []
  let m
  while ((m = re.exec(html)) !== null) ids.push(m[1])
  return ids
}

/** Insert a stub at the end of doc.html and return the new html string. */
function insertStub(html, stubHtml) {
  return html.trimEnd() + '\n' + stubHtml + '\n'
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Top bar action button (compact, consistent). */
function TopBtn({ onClick, disabled, title, children, active = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={[
        'inline-flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-lg border',
        'transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
        'whitespace-nowrap shrink-0',
        active
          ? 'bg-primary text-primary-fg border-primary shadow-sm'
          : 'bg-surface text-muted border-border hover:bg-surface-2 hover:text-fg hover:border-border/80',
        disabled ? 'opacity-40 cursor-not-allowed pointer-events-none' : '',
      ].join(' ')}
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Variable Manager dialog
// ---------------------------------------------------------------------------

function VariableManager({ variables, onChange, onClose }) {
  const add = () => onChange([...variables, { name: '', type: 'text', default: '' }])
  const update = (i, patch) => onChange(variables.map((v, idx) => idx === i ? { ...v, ...patch } : v))
  const remove = (i) => onChange(variables.filter((_, idx) => idx !== i))

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        className="bg-surface rounded-2xl border border-border shadow-2xl w-full max-w-lg mx-4 overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border bg-surface-2/20">
          <h2 className="text-sm font-semibold text-fg flex items-center gap-2">
            <Variable size={15} className="text-primary" />
            Variable Manager
          </h2>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            aria-label="Close"
          >
            <X size={15} />
          </button>
        </div>

        {/* Body */}
        <div className="p-5 space-y-2 max-h-80 overflow-y-auto">
          {variables.length === 0 && (
            <div className="text-xs text-muted/70 text-center py-6 border border-dashed border-border/80 rounded-xl bg-surface-2/20">
              No variables defined.
            </div>
          )}
          {variables.map((v, i) => (
            <div key={i} className="flex items-center gap-2 rounded-xl border border-border bg-surface-2/30 px-2.5 py-2">
              <input
                type="text"
                placeholder="name"
                className={`${inputCls} flex-1 font-mono text-xs`}
                value={v.name}
                onChange={e => update(i, { name: e.target.value })}
              />
              <select
                className={`${selectCls} w-28`}
                value={v.type ?? 'text'}
                onChange={e => update(i, { type: e.target.value })}
              >
                {['text', 'number', 'date', 'daterange', 'select', 'multiselect'].map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <input
                type="text"
                placeholder="default"
                className={`${inputCls} w-28`}
                value={v.default ?? ''}
                onChange={e => update(i, { default: e.target.value })}
              />
              <button
                onClick={() => remove(i)}
                className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors shrink-0"
                aria-label="Remove variable"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-border bg-surface-2/10">
          <button
            onClick={add}
            className="inline-flex items-center gap-1.5 text-xs font-medium px-3 h-8 rounded-lg border border-dashed border-border text-muted hover:border-primary hover:text-primary transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <Plus size={12} /> Add variable
          </button>
          <button
            onClick={onClose}
            className="inline-flex items-center gap-1.5 text-xs font-medium px-4 h-8 rounded-lg bg-primary text-primary-fg hover:opacity-90 transition-opacity active:scale-[0.97] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Ask-AI panel (Canvas variant — POST /ai/canvas/edit)
// ---------------------------------------------------------------------------

function CanvasAskAIPanel({ doc, onApply, onClose }) {
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const handleGenerate = useCallback(async () => {
    const p = prompt.trim()
    if (!p) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await post('/ai/canvas/edit', {
        question: p,
        doc,
      })
      setResult(res)
    } catch (err) {
      // Fallback: try /ai/canvas (generate from scratch)
      try {
        const res2 = await post('/ai/canvas', { question: p })
        setResult(res2)
      } catch (err2) {
        setError(err2.message ?? err.message ?? 'AI generation failed.')
      }
    } finally {
      setLoading(false)
    }
  }, [prompt, doc])

  const handleApply = () => {
    if (!result) return
    // result may contain { doc } or { html, bindings }
    const newDoc = result.doc ?? {
      ...doc,
      html: result.html ?? doc.html,
      bindings: result.bindings ?? doc.bindings,
    }
    onApply(newDoc)
  }

  return (
    <div
      className="absolute right-0 top-full mt-2 z-50 w-96 rounded-2xl border border-border bg-surface shadow-2xl"
      onClick={e => e.stopPropagation()}
    >
      <div className="flex items-center justify-between px-4 pt-4 pb-3 border-b border-border bg-surface-2/20 rounded-t-2xl">
        <h3 className="text-sm font-semibold text-fg flex items-center gap-1.5">
          <Sparkles size={14} className="text-primary" />
          Ask AI
        </h3>
        <button
          onClick={onClose}
          className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          aria-label="Close"
        >
          <X size={13} />
        </button>
      </div>
      <div className="p-4 space-y-3">
        <textarea
          rows={3}
          placeholder="Describe what you want to add or change…"
          className={[
            'w-full text-sm border border-border rounded-xl px-3 py-2.5 resize-none',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
            'bg-surface text-fg placeholder:text-muted/60',
            'transition-all duration-150',
          ].join(' ')}
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') handleGenerate() }}
          disabled={loading}
        />
        <button
          onClick={handleGenerate}
          disabled={loading || !prompt.trim()}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium bg-primary text-primary-fg rounded-xl hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all active:scale-[0.97] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          {loading
            ? <><Loader2 size={14} className="animate-spin" /> Generating…</>
            : <><Sparkles size={14} /> Generate</>
          }
        </button>
        {error && (
          <div
            className="text-xs rounded-xl px-3 py-2 border"
            style={{ background: 'color-mix(in srgb,#ef4444 8%,transparent)', color: '#ef4444', borderColor: 'color-mix(in srgb,#ef4444 25%,transparent)' }}
          >
            {error}
          </div>
        )}
        {result && (
          <div className="space-y-2 bg-surface-2/50 rounded-xl border border-border p-3">
            <p className="text-[11px] text-muted/70 font-medium uppercase tracking-wider">AI result ready</p>
            <button
              onClick={handleApply}
              className="w-full px-3 py-2 text-xs font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity active:scale-[0.97] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            >
              Apply changes
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Schedule dialog
// ---------------------------------------------------------------------------

function ScheduleDialog({ canvasId, onClose }) {
  const [cron, setCron] = useState('0 9 * * 1')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      await post(`/canvases/${canvasId}/schedule`, { cron })
      setSaved(true)
      setTimeout(() => { setSaved(false); onClose() }, 1200)
    } catch (err) {
      setError(err.message ?? 'Schedule failed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        className="bg-surface rounded-2xl border border-border shadow-2xl w-full max-w-sm mx-4 overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-border bg-surface-2/20">
          <h2 className="text-sm font-semibold text-fg flex items-center gap-2">
            <Calendar size={15} className="text-primary" /> Schedule
          </h2>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <X size={15} />
          </button>
        </div>
        <div className="p-5 space-y-3">
          <div>
            <FieldLabel>Cron expression</FieldLabel>
            <input
              type="text"
              className={inputCls}
              value={cron}
              onChange={e => setCron(e.target.value)}
              placeholder="0 9 * * 1 (Mon 9am)"
            />
            <p className="text-[10px] text-muted/70 mt-1 leading-relaxed">Standard 5-field cron (UTC).</p>
          </div>
          {error && (
            <p className="text-xs text-red-500 rounded-lg px-3 py-2 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800/50">
              {error}
            </p>
          )}
          <div className="flex gap-2 pt-1">
            <button
              onClick={onClose}
              className="flex-1 px-3 py-2 text-xs font-medium bg-surface text-muted border border-border rounded-xl hover:bg-surface-2 hover:text-fg transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving || saved}
              className="flex-1 px-3 py-2 text-xs font-medium bg-primary text-primary-fg rounded-xl hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-1.5 transition-all active:scale-[0.97] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            >
              {saved
                ? <><Check size={13} /> Saved</>
                : saving
                ? <><Loader2 size={13} className="animate-spin" /> Saving…</>
                : 'Save schedule'
              }
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Inspector: binding editor for one element
// ---------------------------------------------------------------------------

function BindingInspector({ elId, binding, onChange, variables }) {
  const kind = binding?.kind ?? 'none'
  const [apiTestResult, setApiTestResult] = useState(null)
  const [apiTesting, setApiTesting] = useState(false)
  const metrics = useMetricsList()

  const { columns, introspecting } = useColumnIntrospection(
    kind === 'query' ? (binding?.query_id ?? '') : ''
  )

  const setKind = (k) => {
    if (k === 'none') { onChange(null); return }
    onChange({ kind: k })
  }

  const patch = (p) => onChange({ ...binding, ...p })

  // API connector test
  const testApiBinding = async () => {
    if (!binding?.connector_id) return
    setApiTesting(true)
    setApiTestResult(null)
    try {
      const { get: apiGet } = await import('../lib/api.js')
      const data = await apiGet(
        `/connectors/${binding.connector_id}/fetch?path=${encodeURIComponent(binding.path ?? '')}`
      )
      setApiTestResult({ ok: true, preview: JSON.stringify(data).slice(0, 200) })
    } catch (err) {
      setApiTestResult({ ok: false, message: err.message })
    } finally {
      setApiTesting(false)
    }
  }

  return (
    <div className="space-y-3" data-testid={`binding-inspector-${elId}`}>
      {/* Element ID header */}
      <div className="px-3 py-2 bg-surface-2/50 rounded-lg border border-border">
        <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-0.5">Element</p>
        <p className="text-xs font-mono text-fg truncate" title={elId}>{elId}</p>
      </div>

      {/* Binding-kind switch */}
      <div>
        <SectionLabel>Binding kind</SectionLabel>
        <div className="flex rounded-xl border border-border bg-surface-2/50 p-0.5 mt-1.5 gap-0.5">
          {BINDING_KINDS.map(k => (
            <button
              key={k}
              onClick={() => setKind(k)}
              data-testid={`binding-kind-${k}`}
              className={[
                'flex-1 h-7 text-[11px] font-medium rounded-lg capitalize transition-all duration-150',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                kind === k
                  ? 'bg-surface text-fg shadow-sm border border-border/60'
                  : 'text-muted hover:text-fg',
              ].join(' ')}
            >
              {k}
            </button>
          ))}
        </div>
      </div>

      {/* Query sub-panel */}
      {kind === 'query' && (
        <Section title="Query binding" icon={Database}>
          <div>
            <FieldLabel>Query ID</FieldLabel>
            <QueryPicker value={binding?.query_id ?? ''} onChange={v => patch({ query_id: v })} />
          </div>
          {introspecting && <p className="text-xs text-muted animate-pulse">Introspecting columns…</p>}
          {columns.length > 0 && (
            <div>
              <FieldLabel>Field (first-row value)</FieldLabel>
              <select className={selectCls} value={binding?.field ?? ''}
                onChange={e => patch({ field: e.target.value })}>
                <option value="">— none —</option>
                {columns.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          )}
          <div>
            <FieldLabel>Format</FieldLabel>
            <select className={selectCls} value={binding?.format ?? 'number'}
              onChange={e => patch({ format: e.target.value })}>
              {['number', 'integer', 'currency', 'percent', 'text'].map(f => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
          </div>
          <ParamBindingSection
            widget={{ params: binding?.params ?? {} }}
            onChange={w => patch({ params: w.params })}
            specVariables={variables}
          />
        </Section>
      )}

      {/* Metric sub-panel */}
      {kind === 'metric' && (
        <Section title="Metric binding" icon={TrendingUp}>
          <div>
            <FieldLabel>Metric</FieldLabel>
            <select className={selectCls} value={binding?.metric?.metric_id ?? ''}
              onChange={e => patch({ metric: { metric_id: e.target.value } })}>
              <option value="">Select a metric…</option>
              {metrics.map(m => (
                <option key={m.metric_id ?? m.id} value={m.metric_id ?? m.id}>
                  {m.name ?? m.metric_id ?? m.id}
                </option>
              ))}
            </select>
          </div>
        </Section>
      )}

      {/* API connector sub-panel */}
      {kind === 'api' && (
        <Section title="API connector binding" icon={Link2}>
          <div>
            <FieldLabel>Connector ID</FieldLabel>
            <input type="text" className={inputCls} placeholder="connector_id"
              value={binding?.connector_id ?? ''} onChange={e => patch({ connector_id: e.target.value })} />
          </div>
          <div>
            <FieldLabel>Path</FieldLabel>
            <input type="text" className={inputCls} placeholder="/endpoint"
              value={binding?.path ?? ''} onChange={e => patch({ path: e.target.value })} />
          </div>
          <div>
            <FieldLabel>JSONPath select</FieldLabel>
            <input type="text" className={inputCls} placeholder="$.data.value"
              value={binding?.select ?? ''} onChange={e => patch({ select: e.target.value })} />
          </div>
          <div className="flex gap-2">
            <button onClick={testApiBinding} disabled={apiTesting || !binding?.connector_id}
              className="inline-flex items-center gap-1.5 text-xs font-medium px-3 h-7 rounded-lg border border-border text-muted hover:text-primary hover:border-primary disabled:opacity-40 transition-colors">
              {apiTesting ? <Loader2 size={12} className="animate-spin" /> : <TestTube2 size={12} />}
              Test
            </button>
          </div>
          {apiTestResult && (
            <div className={`text-xs rounded-lg p-2 border ${apiTestResult.ok ? 'border-green-300 text-green-700 bg-green-50' : 'border-red-300 text-red-600 bg-red-50'}`}>
              {apiTestResult.ok ? `OK: ${apiTestResult.preview}` : `Error: ${apiTestResult.message}`}
            </div>
          )}
        </Section>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Insert-element palette
// ---------------------------------------------------------------------------

const PALETTE_ITEMS = [
  { type: 'kpi', label: 'KPI', icon: Hash },
  { type: 'table', label: 'Table', icon: Table2 },
  { type: 'chart', label: 'Chart', icon: BarChart3 },
  { type: 'metric', label: 'Metric', icon: TrendingUp },
  { type: 'filter', label: 'Filter', icon: FilterIcon },
  { type: 'text', label: 'Text', icon: Type },
]

function InsertPalette({ onInsert }) {
  return (
    <div>
      <SectionLabel>Insert element</SectionLabel>
      <div className="grid grid-cols-3 gap-1.5 mt-2">
        {PALETTE_ITEMS.map(({ type, label, icon: Icon }) => (
          <button
            key={type}
            onClick={() => onInsert(type)}
            data-testid={`insert-palette-${type}`}
            className={[
              'flex flex-col items-center justify-center gap-1.5 h-14 px-1',
              'text-[11px] font-medium rounded-xl border border-border bg-surface',
              'text-muted hover:border-primary/60 hover:text-primary hover:bg-surface-2/60',
              'transition-all duration-150 group',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
              'active:scale-[0.96]',
            ].join(' ')}
          >
            <span className="w-7 h-7 rounded-lg bg-surface-2 flex items-center justify-center group-hover:bg-primary/10 transition-colors duration-150">
              <Icon size={14} />
            </span>
            {label}
          </button>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// CanvasEditor (main export)
// ---------------------------------------------------------------------------

/**
 * Route: /canvas/:id
 *
 * Loads GET /canvases/{id}, renders a three-pane IDE:
 *   left/center — HTML editor + live preview (CanvasRenderer)
 *   right       — element inspector + insert palette
 *   top         — title / save / undo-redo / AI / schedule / share
 */
export default function CanvasEditor() {
  const { id } = useParams()
  const navigate = useNavigate()

  // ── Theme for Monaco ──────────────────────────────────────────────────────
  // CanvasEditor always mounts inside AppShell → ThemeProvider, so useTheme
  // is safe to call unconditionally here.
  const { theme } = useTheme()
  const monacoTheme = theme === 'dark' ? 'vs-dark' : 'light'

  // ── Load / save state ─────────────────────────────────────────────────────
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState(null) // 'saved' | 'error' | null

  // ── Document state + undo/redo ────────────────────────────────────────────
  const [historyState, setHistoryState] = useState(null) // createHistory(doc)
  const doc = historyState?.present ?? EMPTY_DOC
  const dirty = useRef(false)

  const handleUndo = useCallback(() => {
    setHistoryState(prev => prev ? historyUndo(prev) : prev)
    dirty.current = true
  }, [])

  const handleRedo = useCallback(() => {
    setHistoryState(prev => prev ? historyRedo(prev) : prev)
    dirty.current = true
  }, [])

  // ── Left-pane tab: 'code' | 'preview' ────────────────────────────────────
  const [activeTab, setActiveTab] = useState('code')

  // ── Selected element ──────────────────────────────────────────────────────
  const [selectedElId, setSelectedElId] = useState(null)

  // ── Right-pane panels ─────────────────────────────────────────────────────
  const [showVariables, setShowVariables] = useState(false)
  const [showAI, setShowAI] = useState(false)
  const [showSchedule, setShowSchedule] = useState(false)

  // ── Debounce timer for HTML preview ──────────────────────────────────────
  const debounceTimer = useRef(null)

  // ── Monaco editor ref ─────────────────────────────────────────────────────
  const editorRef = useRef(null)

  // ── Load canvas ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (!id) return
    let cancelled = false
    async function load() {
      setLoading(true)
      setLoadError(null)
      try {
        const canvas = await get(`/canvases/${id}`)
        if (cancelled) return
        const canvasDoc = canvas?.config?.doc ?? canvas?.doc ?? EMPTY_DOC
        setHistoryState(createHistory({
          ...EMPTY_DOC,
          ...canvasDoc,
          title: canvasDoc?.title ?? canvas?.name ?? 'Untitled Canvas',
        }))
      } catch (err) {
        if (cancelled) return
        setLoadError(err.message)
        setHistoryState(createHistory(EMPTY_DOC))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [id])

  // ── Save ──────────────────────────────────────────────────────────────────
  const handleSave = useCallback(async () => {
    if (!id) return
    setSaving(true)
    setSaveStatus(null)
    try {
      await put(`/canvases/${id}`, { doc })
      dirty.current = false
      setSaveStatus('saved')
      toast.success('Canvas saved')
      setTimeout(() => setSaveStatus(null), 2000)
    } catch {
      setSaveStatus('error')
      toast.error('Save failed')
      setTimeout(() => setSaveStatus(null), 3000)
    } finally {
      setSaving(false)
    }
  }, [id, doc])

  // Ctrl/Cmd+S
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        handleSave()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [handleSave])

  // ── HTML change (from Monaco, debounced) ─────────────────────────────────
  const handleHtmlChangeDirect = useCallback((val) => {
    const html = val ?? ''
    if (debounceTimer.current) clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => {
      setHistoryState(prev => {
        if (!prev) return null
        dirty.current = true
        const newDoc = { ...prev.present, html }
        return historyPush(prev, newDoc)
      })
    }, 400)
  }, [])

  // ── Title change ──────────────────────────────────────────────────────────
  const handleTitleChange = useCallback((title) => {
    setHistoryState(prev => {
      if (!prev) return null
      dirty.current = true
      return historyPush(prev, { ...prev.present, title })
    })
  }, [])

  // ── Binding mutation ──────────────────────────────────────────────────────
  const handleBindingChange = useCallback((elId, newBinding) => {
    setHistoryState(prev => {
      if (!prev) return null
      dirty.current = true
      const present = prev.present
      const bindings = { ...present.bindings }
      if (newBinding === null) {
        delete bindings[elId]
      } else {
        bindings[elId] = newBinding
      }
      return historyPush(prev, { ...present, bindings })
    })
  }, [])

  // ── Variable change ───────────────────────────────────────────────────────
  const handleVariablesChange = useCallback((variables) => {
    setHistoryState(prev => {
      if (!prev) return null
      dirty.current = true
      return historyPush(prev, { ...prev.present, variables })
    })
  }, [])

  // ── Insert element from palette ───────────────────────────────────────────
  const handleInsert = useCallback((type) => {
    const prefix = type === 'kpi' ? 'kpi' : type === 'table' ? 'tbl' : type === 'chart' ? 'chart' : type === 'metric' ? 'metric' : type === 'filter' ? 'filter' : 'txt'
    const elId = genElId(prefix)
    const stubFn = ELEMENT_STUBS[type] ?? ELEMENT_STUBS.text
    const stub = stubFn(elId)

    setHistoryState(prev => {
      if (!prev) return null
      dirty.current = true
      const newHtml = insertStub(prev.present.html, stub)
      return historyPush(prev, { ...prev.present, html: newHtml })
    })

    // Also update the Monaco editor content if it's mounted
    if (editorRef.current) {
      const model = editorRef.current.getModel()
      if (model) {
        const current = model.getValue()
        const next = insertStub(current, stub)
        editorRef.current.setValue(next)
      }
    }

    setSelectedElId(elId)
    setActiveTab('preview')
  }, [])

  // ── AI panel apply ────────────────────────────────────────────────────────
  const handleAIApply = useCallback((newDoc) => {
    setHistoryState(prev => {
      if (!prev) return createHistory(newDoc)
      dirty.current = true
      return historyPush(prev, { ...prev.present, ...newDoc })
    })
    setShowAI(false)
  }, [])

  // ── Preview click-to-select ───────────────────────────────────────────────
  // We attach a click listener to the preview container via a ref callback.
  const previewContainerRef = useRef(null)
  const handlePreviewContainerRef = useCallback((node) => {
    if (previewContainerRef.current && previewContainerRef.current !== node) {
      // cleanup old
    }
    previewContainerRef.current = node
    if (!node) return
    const handleClick = (e) => {
      let el = e.target
      while (el && el !== node) {
        const eid = el.getAttribute?.('data-el-id')
        if (eid) {
          setSelectedElId(eid)
          e.stopPropagation()
          return
        }
        el = el.parentElement
      }
      setSelectedElId(null)
    }
    node.addEventListener('click', handleClick)
  }, [])

  // ── Derive known el ids from HTML ─────────────────────────────────────────
  const knownElIds = useMemo(() => extractElIds(doc.html ?? ''), [doc.html])

  // ── canUndo / canRedo shortcuts ───────────────────────────────────────────
  const undoAvailable = historyState ? canUndo(historyState) : false
  const redoAvailable = historyState ? canRedo(historyState) : false

  // ── Monaco mount ──────────────────────────────────────────────────────────
  const handleEditorMount = useCallback((editor, monaco) => {
    editorRef.current = editor
    // Ctrl/Cmd+S → save
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      handleSave()
    })
  }, [handleSave])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-bg gap-3">
        <Spinner size="lg" label="Loading canvas" />
        <p className="text-sm text-muted">Loading canvas…</p>
      </div>
    )
  }

  const selectedBinding = selectedElId ? (doc.bindings?.[selectedElId] ?? null) : null

  return (
    <div className="flex flex-col h-screen bg-bg overflow-hidden" data-testid="canvas-editor">
      {/* ── TOP BAR ───────────────────────────────────────────────────────── */}
      <div className="h-12 shrink-0 flex items-center gap-1.5 px-3 border-b border-border bg-surface z-20">
        {/* Back */}
        <button
          onClick={() => navigate('/canvases')}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          title="Back to canvases"
          aria-label="Back to canvases"
        >
          <ChevronRight size={15} className="rotate-180" />
        </button>

        <div className="w-px h-4 bg-border/60 shrink-0" />

        {/* Title */}
        <input
          type="text"
          value={doc.title ?? ''}
          onChange={e => handleTitleChange(e.target.value)}
          className={[
            'h-8 text-sm font-semibold bg-transparent border border-transparent rounded-lg px-2',
            'text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:border-border focus-visible:bg-surface',
            'min-w-0 max-w-[180px] sm:max-w-xs transition-all duration-150',
            'placeholder:text-muted/50',
          ].join(' ')}
          placeholder="Canvas title…"
          aria-label="Canvas title"
        />

        <div className="flex-1" />

        {/* Undo / redo */}
        <div className="flex items-center gap-0.5">
          <button
            onClick={handleUndo}
            disabled={!undoAvailable}
            title="Undo (Ctrl+Z)"
            aria-label="Undo"
            className="w-8 h-8 flex items-center justify-center rounded-lg border border-transparent text-muted hover:border-border hover:bg-surface-2 hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <Undo2 size={13} />
          </button>
          <button
            onClick={handleRedo}
            disabled={!redoAvailable}
            title="Redo (Ctrl+Y)"
            aria-label="Redo"
            className="w-8 h-8 flex items-center justify-center rounded-lg border border-transparent text-muted hover:border-border hover:bg-surface-2 hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <Redo2 size={13} />
          </button>
        </div>

        <div className="w-px h-4 bg-border/60 shrink-0" />

        {/* Variables */}
        <TopBtn onClick={() => setShowVariables(true)} title="Manage variables">
          <Variable size={13} />
          <span className="hidden sm:inline">Variables</span>
        </TopBtn>

        {/* Ask AI */}
        <div className="relative">
          <TopBtn onClick={() => setShowAI(o => !o)} title="Ask AI to edit canvas" active={showAI}>
            <Sparkles size={13} />
            <span className="hidden sm:inline">Ask AI</span>
          </TopBtn>
          {showAI && (
            <CanvasAskAIPanel
              doc={doc}
              onApply={handleAIApply}
              onClose={() => setShowAI(false)}
            />
          )}
        </div>

        {/* Schedule */}
        <TopBtn onClick={() => setShowSchedule(true)} title="Schedule canvas refresh">
          <Calendar size={13} />
          <span className="hidden md:inline">Schedule</span>
        </TopBtn>

        {/* Share */}
        <TopBtn
          onClick={() => {
            const url = `${window.location.origin}/c/${id}`
            navigator.clipboard?.writeText(url).catch(() => {})
            toast.success('Share link copied')
          }}
          title="Copy share link"
        >
          <Share2 size={13} />
          <span className="hidden md:inline">Share</span>
        </TopBtn>

        <div className="w-px h-4 bg-border/60 shrink-0" />

        {/* Save */}
        <button
          onClick={handleSave}
          disabled={saving}
          title="Save (Ctrl+S)"
          className={[
            'inline-flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-lg border',
            'transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
            'whitespace-nowrap active:scale-[0.97]',
            saveStatus === 'saved'
              ? 'bg-emerald-500 text-white border-emerald-500'
              : saveStatus === 'error'
              ? 'bg-red-500 text-white border-red-500'
              : 'bg-primary text-primary-fg border-primary hover:opacity-90',
            saving ? 'opacity-70 cursor-not-allowed' : '',
          ].join(' ')}
        >
          {saving
            ? <><Loader2 size={13} className="animate-spin" /> Saving…</>
            : saveStatus === 'saved'
            ? <><Check size={13} /> Saved</>
            : saveStatus === 'error'
            ? <><AlertCircle size={13} /> Error</>
            : <><Save size={13} /> Save</>
          }
        </button>
      </div>

      {/* ── MAIN AREA ─────────────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0">
        {/* ── LEFT / CENTER ──────────────────────────────────────────────── */}
        <div className="flex flex-col flex-1 min-w-0">
          {/* Tab bar */}
          <div className="flex items-center gap-0 border-b border-border bg-surface shrink-0">
            {[
              { id: 'code', label: 'HTML', icon: Code2 },
              { id: 'preview', label: 'Preview', icon: Eye },
            ].map(({ id: tid, label, icon: Icon }) => (
              <button
                key={tid}
                onClick={() => setActiveTab(tid)}
                role="tab"
                aria-selected={activeTab === tid}
                className={[
                  'inline-flex items-center gap-1.5 h-9 px-4 text-xs font-medium border-b-2',
                  'transition-all duration-150',
                  'focus:outline-none focus-visible:ring-inset focus-visible:ring-2 focus-visible:ring-ring/60',
                  activeTab === tid
                    ? 'border-primary text-primary bg-primary/5'
                    : 'border-transparent text-muted hover:text-fg hover:bg-surface-2',
                ].join(' ')}
              >
                <Icon size={13} />
                {label}
              </button>
            ))}
            {loadError && (
              <span className="ml-auto mr-3 text-xs flex items-center gap-1 px-2 py-0.5 rounded-md"
                style={{ color: '#d97706', background: 'color-mix(in srgb, #f59e0b 8%, transparent)' }}>
                <AlertCircle size={11} /> {loadError}
              </span>
            )}
          </div>

          {/* CODE tab */}
          {activeTab === 'code' && (
            <div className="flex-1 min-h-0" data-testid="canvas-html-editor">
              <Editor
                height="100%"
                language="html"
                theme={monacoTheme}
                defaultValue={doc.html}
                onChange={handleHtmlChangeDirect}
                onMount={handleEditorMount}
                options={{
                  minimap: { enabled: false },
                  scrollBeyondLastLine: false,
                  fontSize: 13,
                  lineNumbers: 'on',
                  wordWrap: 'on',
                  tabSize: 2,
                  automaticLayout: true,
                  padding: { top: 8, bottom: 8 },
                  scrollbar: { vertical: 'auto', horizontal: 'auto' },
                }}
                loading={
                  <div className="flex items-center justify-center h-full text-xs text-muted">
                    Loading editor…
                  </div>
                }
              />
            </div>
          )}

          {/* PREVIEW tab */}
          {activeTab === 'preview' && (
            <div className="flex-1 min-h-0 overflow-auto p-4 bg-bg" data-testid="canvas-preview">
              <div
                ref={handlePreviewContainerRef}
                className="relative canvas-editor-preview-container"
                style={{ cursor: 'default' }}
              >
                {/* Selection outline overlay (via CSS data attribute) */}
                <style>{`
                  .canvas-editor-preview-container [data-el-id="${selectedElId ?? '__none__'}"] {
                    outline: 2px solid hsl(var(--color-primary));
                    outline-offset: 2px;
                    border-radius: 4px;
                  }
                `}</style>
                <CanvasRenderer doc={doc} />
              </div>
            </div>
          )}
        </div>

        {/* ── RIGHT INSPECTOR ────────────────────────────────────────────── */}
        <div
          className="w-72 shrink-0 border-l border-border bg-surface flex flex-col overflow-hidden"
          data-testid="canvas-inspector"
        >
          {/* Inspector header */}
          <div className="px-3 h-10 border-b border-border shrink-0 flex items-center bg-surface-2/30">
            <p className="text-[11px] font-semibold text-muted/80 uppercase tracking-wider flex items-center gap-1.5">
              <Settings2 size={12} />
              Inspector
            </p>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-4">
            {/* Element binding editor */}
            {selectedElId ? (
              <BindingInspector
                key={selectedElId}
                elId={selectedElId}
                binding={selectedBinding}
                onChange={(binding) => handleBindingChange(selectedElId, binding)}
                variables={doc.variables ?? []}
              />
            ) : (
              <div className="rounded-xl border border-dashed border-border/80 bg-surface-2/20 px-3 py-5 text-center space-y-1.5">
                <div className="w-8 h-8 rounded-lg bg-surface-2 flex items-center justify-center mx-auto">
                  <Settings2 size={15} className="text-muted/40" />
                </div>
                <p className="text-xs font-medium text-fg/70">No element selected</p>
                <p className="text-[11px] text-muted/60 leading-relaxed">
                  Click an element in Preview to inspect it.
                </p>
              </div>
            )}

            {/* Known element IDs quick-pick */}
            {knownElIds.length > 0 && (
              <div>
                <SectionLabel>Elements in HTML</SectionLabel>
                <div className="mt-1.5 space-y-0.5 max-h-36 overflow-y-auto rounded-lg border border-border bg-bg p-1">
                  {knownElIds.map(eid => (
                    <button
                      key={eid}
                      onClick={() => { setSelectedElId(eid); setActiveTab('preview') }}
                      className={[
                        'w-full text-left px-2 py-1 text-xs rounded-md font-mono transition-all duration-150',
                        'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                        selectedElId === eid
                          ? 'bg-primary/10 text-primary'
                          : 'text-muted hover:text-fg hover:bg-surface-2',
                      ].join(' ')}
                    >
                      {eid}
                      {doc.bindings?.[eid] && (
                        <span className="ml-1.5 text-[9px] font-semibold text-primary/70 uppercase tracking-wide">
                          {doc.bindings[eid].kind}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Insert palette */}
            <InsertPalette onInsert={handleInsert} />
          </div>
        </div>
      </div>

      {/* ── MODALS ────────────────────────────────────────────────────────── */}
      {showVariables && (
        <VariableManager
          variables={doc.variables ?? []}
          onChange={handleVariablesChange}
          onClose={() => setShowVariables(false)}
        />
      )}
      {showSchedule && (
        <ScheduleDialog canvasId={id} onClose={() => setShowSchedule(false)} />
      )}
    </div>
  )
}
