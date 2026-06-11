/**
 * WatchesPage — proactive metric alerts (WATCHES).
 *
 * Route:  /watches
 *
 * A *watch* monitors a single governed metric and fires when a threshold (or a
 * change-over-time rule) is breached. On breach the backend composes an AI
 * explanation and dispatches it to a notify channel (Slack). This page is the
 * full CRUD + manual-evaluate surface:
 *
 *  - lists watches (name, metric, condition summary, enabled, last state).
 *  - create / edit form (modal): pick a metric, choose dimensions / time grain,
 *    set a threshold (op + value) OR a change_pct rule, set channel config
 *    (Slack webhook / channel), enabled toggle.
 *  - "Evaluate now" per watch → POST /evaluate, shows {breached, value,
 *    explanation}.
 *  - delete (inline confirm).
 *
 * Styling/conventions mirror AutomationsPage / FlowsPage: the page toolbar is
 * portaled into the single AppShell topbar (useUi().topbarSlot); writes are
 * gated on useCanWrite(); Tailwind design tokens (bg-surface, border-border,
 * text-fg/text-muted, bg-primary/text-primary-fg) are used throughout.
 */

import { useEffect, useState, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import {
  Bell,
  Plus,
  RefreshCw,
  Loader2,
  Trash2,
  X,
  Check,
  Pencil,
  Zap,
  AlertTriangle,
  TrendingUp,
  Gauge,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react'

import { useUi } from '../../contexts/UiContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import {
  listWatches,
  createWatch,
  updateWatch,
  deleteWatch,
  evaluateWatch,
  listMetrics,
} from '../../lib/watches.js'
import { listIntegrations } from '../../lib/integrationsApi.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const THRESHOLD_OPS = ['<', '<=', '>', '>=', '==']
const RULE_KINDS = [
  { id: 'threshold', label: 'Threshold', Icon: Gauge, hint: 'Fire when the metric value crosses a fixed level.' },
  { id: 'change_pct', label: 'Change %', Icon: TrendingUp, hint: 'Fire when the metric moves vs the previous period.' },
]
const TIME_GRAINS = ['', 'hour', 'day', 'week', 'month', 'quarter', 'year']

// ---------------------------------------------------------------------------
// Helpers — derive a clean view model from a watch record
// ---------------------------------------------------------------------------

function ruleOf(watch) {
  const cfg = watch?.config ?? {}
  const comparison = cfg.comparison ?? cfg.change ?? null
  if (comparison && comparison.kind === 'change_pct') return { kind: 'change_pct', comparison }
  if (cfg.threshold) return { kind: 'threshold', threshold: cfg.threshold }
  return { kind: 'none' }
}

/** A compact, human-readable summary of a watch's breach condition. */
function conditionSummary(watch) {
  const r = ruleOf(watch)
  if (r.kind === 'threshold') {
    const t = r.threshold ?? {}
    return `value ${t.op ?? '?'} ${fmtNum(t.value)}`
  }
  if (r.kind === 'change_pct') {
    const c = r.comparison ?? {}
    return `change ${c.op ?? '?'} ${fmtNum(c.value)}% vs previous period`
  }
  return 'no rule'
}

function fmtNum(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return String(v)
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

function isEnabled(watch) {
  // Default true when unset (mirrors the backend Watch.enabled default).
  const e = watch?.config?.enabled
  return e === undefined ? true : Boolean(e)
}

// ---------------------------------------------------------------------------
// Last-state pill
// ---------------------------------------------------------------------------

function StatePill({ state }) {
  const map = {
    breached: { cls: 'bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20', label: 'Breached', Icon: AlertCircle },
    ok:       { cls: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20', label: 'OK', Icon: CheckCircle2 },
    error:    { cls: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20', label: 'Error', Icon: AlertTriangle },
  }
  const m = map[state]
  if (!m) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded border border-border text-muted">
        Not evaluated
      </span>
    )
  }
  const { Icon } = m
  return (
    <span className={['inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded border', m.cls].join(' ')}>
      <Icon size={11} /> {m.label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// WatchRow — one watch in the list
// ---------------------------------------------------------------------------

function WatchRow({ watch, metricName, canWrite, onEdit, onDeleted, onToast }) {
  const [evaluating, setEvaluating] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [result, setResult] = useState(null)

  const handleEvaluate = useCallback(async () => {
    setEvaluating(true)
    setResult(null)
    try {
      const r = await evaluateWatch(watch.id)
      setResult(r)
    } catch (err) {
      setResult({ state: 'error', error: err?.message || 'Evaluation failed.' })
    } finally {
      setEvaluating(false)
    }
  }, [watch.id])

  const handleDelete = useCallback(async () => {
    setDeleting(true)
    const ok = await deleteWatch(watch.id)
    setDeleting(false)
    if (ok) {
      onToast?.(`Deleted "${watch.name}".`)
      onDeleted?.(watch.id)
    } else {
      onToast?.('Delete failed — check the console.')
      setConfirmDelete(false)
    }
  }, [watch.id, watch.name, onDeleted, onToast])

  // Last state: prefer the live evaluate result, else the watch's stored state.
  const lastState = result?.state ?? watch.last_state ?? watch.config?.last_state ?? null

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <Bell size={14} className="text-muted shrink-0" strokeWidth={2.2} />
            <p className="text-sm font-semibold text-fg truncate">{watch.name}</p>
            {!isEnabled(watch) && (
              <span className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-surface-2 text-muted border border-border">
                Disabled
              </span>
            )}
            <StatePill state={lastState} />
          </div>
          <p className="text-xs text-muted mt-1.5 truncate">
            <span className="font-medium text-fg/80">Metric:</span>{' '}
            {metricName || watch.metric_id || '—'}
          </p>
          <p className="text-xs text-muted mt-0.5">
            <span className="font-medium text-fg/80">When:</span> {conditionSummary(watch)}
            {watch.config?.time_grain ? ` · per ${watch.config.time_grain}` : ''}
            {Array.isArray(watch.config?.dimensions) && watch.config.dimensions.length > 0
              ? ` · by ${watch.config.dimensions.join(', ')}`
              : ''}
          </p>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={handleEvaluate}
            disabled={evaluating}
            title="Evaluate now"
            className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {evaluating ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} strokeWidth={2.2} />}
            {evaluating ? 'Evaluating…' : 'Evaluate'}
          </button>

          {canWrite && (
            <button
              onClick={() => onEdit?.(watch)}
              title="Edit"
              className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Pencil size={13} />
            </button>
          )}

          {!canWrite ? null : confirmDelete ? (
            <div className="flex items-center gap-1.5">
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="inline-flex items-center gap-1 h-8 px-2.5 rounded-lg bg-red-600 text-white text-xs font-semibold hover:bg-red-700 disabled:opacity-50 transition-colors"
              >
                {deleting ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                Confirm
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="h-8 w-8 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
              >
                <X size={14} />
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              title="Delete"
              className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border text-muted hover:text-red-600 hover:border-red-200 hover:bg-red-50 dark:hover:text-red-400 dark:hover:border-red-900/40 dark:hover:bg-red-900/10 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Evaluate result */}
      {result && (
        <div
          className={[
            'mt-3 rounded-lg border p-3 text-xs',
            result.state === 'breached'
              ? 'border-red-500/20 bg-red-500/5'
              : result.state === 'error'
                ? 'border-amber-500/20 bg-amber-500/5'
                : 'border-emerald-500/20 bg-emerald-500/5',
          ].join(' ')}
        >
          <div className="flex items-center gap-2">
            <StatePill state={result.state} />
            {result.value !== undefined && result.value !== null && (
              <span className="text-muted">
                value <span className="font-mono text-fg">{fmtNum(result.value)}</span>
              </span>
            )}
            {typeof result.sent === 'number' && result.sent > 0 && (
              <span className="text-muted">· alerted {result.sent} channel{result.sent === 1 ? '' : 's'}</span>
            )}
          </div>
          {result.explanation && (
            <p className="mt-2 text-fg/90 leading-relaxed">{result.explanation}</p>
          )}
          {result.error && (
            <p className="mt-2 text-amber-600 dark:text-amber-400">{result.error}</p>
          )}
          {result.state === 'ok' && !result.explanation && (
            <p className="mt-2 text-muted">Within threshold — no alert sent.</p>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// WatchModal — create / edit form
// ---------------------------------------------------------------------------

function blankDraft() {
  return {
    id: null,
    name: '',
    metric_id: '',
    dimensions: '',
    time_grain: '',
    ruleKind: 'threshold',
    thresholdOp: '>',
    thresholdValue: '',
    changeOp: '>',
    changeValue: '',
    integrationId: '',
    slackWebhook: '',
    slackChannel: '',
    enabled: true,
  }
}

/** Hydrate a draft from an existing watch record. */
function draftFromWatch(watch) {
  const cfg = watch.config ?? {}
  const r = ruleOf(watch)
  return {
    id: watch.id,
    name: watch.name ?? '',
    metric_id: watch.metric_id ?? '',
    dimensions: Array.isArray(cfg.dimensions) ? cfg.dimensions.join(', ') : '',
    time_grain: cfg.time_grain ?? '',
    ruleKind: r.kind === 'change_pct' ? 'change_pct' : 'threshold',
    thresholdOp: r.threshold?.op ?? '>',
    thresholdValue: r.threshold?.value ?? '',
    changeOp: r.comparison?.op ?? '>',
    changeValue: r.comparison?.value ?? '',
    integrationId: cfg.channel_config?.integration_id ?? '',
    slackWebhook: cfg.channel_config?.slack_webhook ?? '',
    slackChannel: cfg.channel_config?.slack_channel ?? '',
    enabled: cfg.enabled === undefined ? true : Boolean(cfg.enabled),
  }
}

/** Build the API body { name, metric_id, config } from a draft. Returns
 *  { body } on success or { error } when the draft is invalid. */
function bodyFromDraft(draft) {
  const name = draft.name.trim()
  if (!name) return { error: 'Give the watch a name.' }
  if (!draft.metric_id) return { error: 'Pick a metric to monitor.' }

  const config = {}

  const dimensions = draft.dimensions
    .split(',')
    .map(s => s.trim())
    .filter(Boolean)
  if (dimensions.length > 0) config.dimensions = dimensions
  if (draft.time_grain) config.time_grain = draft.time_grain

  if (draft.ruleKind === 'change_pct') {
    const value = Number(draft.changeValue)
    if (draft.changeValue === '' || Number.isNaN(value)) {
      return { error: 'Enter a numeric percentage for the change rule.' }
    }
    config.comparison = {
      kind: 'change_pct',
      vs: 'previous_period',
      op: draft.changeOp,
      value,
    }
  } else {
    const value = Number(draft.thresholdValue)
    if (draft.thresholdValue === '' || Number.isNaN(value)) {
      return { error: 'Enter a numeric threshold value.' }
    }
    config.threshold = { op: draft.thresholdOp, value }
  }

  const channel_config = {}
  // Prefer a connected integration; fall back to the free-form Slack fields
  // (kept for orgs that have not connected any integration yet).
  if (draft.integrationId) {
    channel_config.integration_id = draft.integrationId
  } else {
    if (draft.slackWebhook.trim()) channel_config.slack_webhook = draft.slackWebhook.trim()
    if (draft.slackChannel.trim()) channel_config.slack_channel = draft.slackChannel.trim()
  }
  if (Object.keys(channel_config).length > 0) config.channel_config = channel_config

  config.enabled = Boolean(draft.enabled)

  return { body: { name, metric_id: draft.metric_id, config } }
}

const FIELD_CLS =
  'w-full h-9 px-2.5 text-sm rounded-lg border border-border bg-surface text-fg placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60'
const LABEL_CLS = 'block text-xs font-medium text-fg/80 mb-1'

function WatchModal({ open, initial, metrics, integrations, onClose, onSaved }) {
  const [draft, setDraft] = useState(blankDraft)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  // Re-seed the draft each time the modal opens (create vs edit).
  useEffect(() => {
    if (!open) return
    setDraft(initial ? draftFromWatch(initial) : blankDraft())
    setError(null)
  }, [open, initial])

  // The metric selected (for dimension/grain hints).
  const selectedMetric = useMemo(
    () => metrics.find(m => m.id === draft.metric_id) || null,
    [metrics, draft.metric_id],
  )

  const set = (patch) => setDraft(d => ({ ...d, ...patch }))

  const handleSave = useCallback(async () => {
    const { body, error: validationError } = bodyFromDraft(draft)
    if (validationError) { setError(validationError); return }
    setSaving(true)
    setError(null)
    try {
      const saved = draft.id
        ? await updateWatch(draft.id, body)
        : await createWatch(body)
      onSaved?.(saved)
      onClose?.()
    } catch (err) {
      setError(err?.message || 'Save failed.')
    } finally {
      setSaving(false)
    }
  }, [draft, onSaved, onClose])

  // Close on Escape.
  useEffect(() => {
    if (!open) return undefined
    const handler = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full sm:max-w-lg max-h-[92dvh] flex flex-col bg-surface border border-border rounded-t-2xl sm:rounded-2xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="flex items-center gap-2">
            <Bell size={15} className="text-primary" strokeWidth={2.2} />
            <h2 className="text-sm font-semibold text-fg">
              {draft.id ? 'Edit watch' : 'New watch'}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="h-8 w-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {/* Name */}
          <div>
            <label className={LABEL_CLS}>Name</label>
            <input
              type="text"
              value={draft.name}
              onChange={e => set({ name: e.target.value })}
              placeholder="Revenue dropped"
              className={FIELD_CLS}
              autoFocus
            />
          </div>

          {/* Metric */}
          <div>
            <label className={LABEL_CLS}>Metric to monitor</label>
            <select
              value={draft.metric_id}
              onChange={e => set({ metric_id: e.target.value })}
              className={FIELD_CLS}
            >
              <option value="">Select a metric…</option>
              {metrics.map(m => (
                <option key={m.id} value={m.id}>
                  {m.name || m.id}
                </option>
              ))}
            </select>
            {metrics.length === 0 && (
              <p className="text-[11px] text-muted mt-1">
                No metrics found. Define a metric first, then create a watch on it.
              </p>
            )}
          </div>

          {/* Dimensions + grain */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={LABEL_CLS}>Dimensions</label>
              <input
                type="text"
                value={draft.dimensions}
                onChange={e => set({ dimensions: e.target.value })}
                placeholder="region, plan"
                className={FIELD_CLS}
              />
              {selectedMetric?.dimensions?.length > 0 && (
                <p className="text-[11px] text-muted mt-1 truncate">
                  Available: {selectedMetric.dimensions.join(', ')}
                </p>
              )}
            </div>
            <div>
              <label className={LABEL_CLS}>Time grain</label>
              <select
                value={draft.time_grain}
                onChange={e => set({ time_grain: e.target.value })}
                className={FIELD_CLS}
              >
                {TIME_GRAINS.map(g => (
                  <option key={g || 'none'} value={g}>{g || 'none'}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Rule kind switcher */}
          <div>
            <label className={LABEL_CLS}>Breach rule</label>
            <div className="flex rounded-lg border border-border overflow-hidden">
              {RULE_KINDS.map((k, i) => {
                const active = draft.ruleKind === k.id
                const { Icon } = k
                return (
                  <button
                    key={k.id}
                    type="button"
                    onClick={() => set({ ruleKind: k.id })}
                    className={[
                      'flex-1 flex items-center justify-center gap-1.5 h-9 text-xs font-medium transition-colors',
                      i > 0 ? 'border-l border-border' : '',
                      active ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
                    ].join(' ')}
                  >
                    <Icon size={13} /> {k.label}
                  </button>
                )
              })}
            </div>
            <p className="text-[11px] text-muted mt-1">
              {RULE_KINDS.find(k => k.id === draft.ruleKind)?.hint}
            </p>
          </div>

          {/* Rule inputs */}
          {draft.ruleKind === 'threshold' ? (
            <div className="grid grid-cols-[auto_1fr] gap-3 items-end">
              <div>
                <label className={LABEL_CLS}>Operator</label>
                <select
                  value={draft.thresholdOp}
                  onChange={e => set({ thresholdOp: e.target.value })}
                  className={[FIELD_CLS, 'font-mono'].join(' ')}
                >
                  {THRESHOLD_OPS.map(op => <option key={op} value={op}>{op}</option>)}
                </select>
              </div>
              <div>
                <label className={LABEL_CLS}>Value</label>
                <input
                  type="number"
                  value={draft.thresholdValue}
                  onChange={e => set({ thresholdValue: e.target.value })}
                  placeholder="1000"
                  className={FIELD_CLS}
                />
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-[auto_1fr] gap-3 items-end">
              <div>
                <label className={LABEL_CLS}>Operator</label>
                <select
                  value={draft.changeOp}
                  onChange={e => set({ changeOp: e.target.value })}
                  className={[FIELD_CLS, 'font-mono'].join(' ')}
                >
                  {THRESHOLD_OPS.map(op => <option key={op} value={op}>{op}</option>)}
                </select>
              </div>
              <div>
                <label className={LABEL_CLS}>Change %</label>
                <input
                  type="number"
                  value={draft.changeValue}
                  onChange={e => set({ changeValue: e.target.value })}
                  placeholder="-10"
                  className={FIELD_CLS}
                />
              </div>
            </div>
          )}

          {/* Channel config — pick a connected integration, or fall back to a
              free-form Slack webhook when none are connected. */}
          <div className="rounded-lg border border-border bg-surface-2/30 p-3 space-y-3">
            <p className="text-xs font-semibold text-fg/80">Alert channel</p>

            {integrations.length > 0 ? (
              <>
                <div>
                  <label className={LABEL_CLS}>Connected integration</label>
                  <select
                    value={draft.integrationId}
                    onChange={e => set({ integrationId: e.target.value })}
                    className={FIELD_CLS}
                  >
                    <option value="">No notification (test mode)</option>
                    {integrations.map(it => (
                      <option key={it.id} value={it.id}>
                        {(it.name || it.kind)}
                        {it.enabled === false ? ' (disabled)' : ''}
                      </option>
                    ))}
                  </select>
                </div>
                <p className="text-[11px] text-muted">
                  Manage channels in{' '}
                  <span className="font-medium text-fg/80">Settings → Integrations</span>.
                </p>
              </>
            ) : (
              <>
                <p className="text-[11px] text-muted">
                  No integrations connected — enter a Slack webhook below, or connect a
                  channel in <span className="font-medium text-fg/80">Settings → Integrations</span>.
                </p>
                <div>
                  <label className={LABEL_CLS}>Webhook URL</label>
                  <input
                    type="text"
                    value={draft.slackWebhook}
                    onChange={e => set({ slackWebhook: e.target.value })}
                    placeholder="https://hooks.slack.com/services/…"
                    className={[FIELD_CLS, 'font-mono text-xs'].join(' ')}
                  />
                </div>
                <div>
                  <label className={LABEL_CLS}>Channel</label>
                  <input
                    type="text"
                    value={draft.slackChannel}
                    onChange={e => set({ slackChannel: e.target.value })}
                    placeholder="#alerts"
                    className={FIELD_CLS}
                  />
                </div>
                <p className="text-[11px] text-muted">
                  Leave blank to evaluate without notifying (test mode).
                </p>
              </>
            )}
          </div>

          {/* Enabled toggle */}
          <label className="flex items-center gap-2 text-sm text-fg cursor-pointer select-none">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={e => set({ enabled: e.target.checked })}
              className="accent-primary w-4 h-4"
            />
            Enabled
            <span className="text-xs text-muted">— included in scheduled sweeps</span>
          </label>

          {error && (
            <div className="flex items-start gap-2 text-xs text-red-600 dark:text-red-400">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="shrink-0 flex items-center justify-end gap-2 px-4 py-3 border-t border-border">
          <button
            onClick={onClose}
            className="h-9 px-3 text-sm rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 h-9 px-4 text-sm font-semibold rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            {draft.id ? 'Save changes' : 'Create watch'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

function Toast({ message, onDone }) {
  useEffect(() => {
    if (!message) return undefined
    const t = setTimeout(onDone, 2600)
    return () => clearTimeout(t)
  }, [message, onDone])
  if (!message) return null
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[80] px-4 py-2 rounded-lg bg-fg text-bg text-xs font-medium shadow-lg">
      {message}
    </div>
  )
}

// ---------------------------------------------------------------------------
// WatchesPage
// ---------------------------------------------------------------------------

export default function WatchesPage() {
  const { topbarSlot } = useUi()
  const canWrite = useCanWrite()

  const [watches, setWatches] = useState([])
  const [metrics, setMetrics] = useState([])
  const [integrations, setIntegrations] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [toast, setToast] = useState(null)

  const metricNameById = useMemo(() => {
    const map = {}
    for (const m of metrics) map[m.id] = m.name || m.id
    return map
  }, [metrics])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const [w, m, ints] = await Promise.all([listWatches(), listMetrics(), listIntegrations()])
    setWatches(Array.isArray(w) ? w : [])
    setMetrics(Array.isArray(m) ? m : [])
    setIntegrations(Array.isArray(ints) ? ints : [])
    setLoading(false)
  }, [])

  // Defer the initial load so it isn't a synchronous setState in the effect body.
  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load])

  const handleNew = useCallback(() => { setEditing(null); setModalOpen(true) }, [])
  const handleEdit = useCallback((watch) => { setEditing(watch); setModalOpen(true) }, [])

  const handleSaved = useCallback((saved) => {
    if (!saved) { load(); return }
    setWatches(prev => {
      const idx = prev.findIndex(w => w.id === saved.id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = saved
        return next
      }
      return [saved, ...prev]
    })
    setToast(editing ? 'Watch updated.' : 'Watch created.')
  }, [editing, load])

  const handleDeleted = useCallback((id) => {
    setWatches(prev => prev.filter(w => w.id !== id))
  }, [])

  return (
    <div className="flex flex-col min-h-full bg-bg">
      {/* Page toolbar — portaled into the single AppShell topbar */}
      {topbarSlot && createPortal(
        <div className="flex items-center gap-2 w-full min-w-0">
          <Bell size={15} className="text-muted shrink-0 hidden sm:block" strokeWidth={2.2} />
          <span className="text-sm font-semibold font-display text-fg truncate">Watches</span>
          <div className="flex-1" />
          <button
            onClick={load}
            disabled={loading}
            title="Refresh"
            aria-label="Refresh watches"
            className="flex items-center justify-center w-8 h-8 rounded-lg shrink-0 border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} strokeWidth={2} />
          </button>
          {canWrite && (
            <button
              onClick={handleNew}
              title="New watch"
              className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg shrink-0 bg-primary text-primary-fg text-xs font-medium hover:opacity-90 transition-opacity focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Plus size={13} strokeWidth={2.5} />
              <span className="hidden sm:inline">New watch</span>
            </button>
          )}
        </div>,
        topbarSlot
      )}

      {/* Content */}
      <div className="flex-1 px-4 sm:px-6 py-4 max-w-4xl w-full mx-auto">
        <p className="text-xs text-muted mb-4 max-w-2xl leading-relaxed">
          A watch monitors a metric and fires when a threshold or change rule is
          breached — it composes an explanation and notifies your channel. Use
          <span className="font-medium text-fg/80"> Evaluate</span> to test a watch right now.
        </p>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted py-12 justify-center">
            <Loader2 size={16} className="animate-spin" /> Loading watches…
          </div>
        )}

        {!loading && error && (
          <div className="flex flex-col items-center justify-center py-12 gap-3 rounded-xl border border-dashed border-red-200 dark:border-red-900/40">
            <AlertTriangle size={20} className="text-red-500" />
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            <button onClick={load} className="text-xs text-muted hover:text-fg underline">Retry</button>
          </div>
        )}

        {!loading && !error && watches.length === 0 && (
          <div className="flex flex-col items-center justify-center py-14 px-6 text-center rounded-xl border border-dashed border-border">
            <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-brand-gradient shadow-lg mb-5">
              <Bell size={28} className="text-white" />
            </div>
            <h3 className="font-display font-semibold text-xl text-fg mb-2">No watches yet</h3>
            <p className="text-sm text-muted max-w-sm leading-relaxed mb-6">
              Create a watch to get proactively alerted when a metric crosses a
              threshold or moves sharply versus the previous period.
            </p>
            {canWrite && (
              <button
                onClick={handleNew}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity shadow-sm"
              >
                <Plus size={15} /> New watch
              </button>
            )}
          </div>
        )}

        {!loading && !error && watches.length > 0 && (
          <div className="space-y-3">
            {watches.map(w => (
              <WatchRow
                key={w.id}
                watch={w}
                metricName={metricNameById[w.metric_id]}
                canWrite={canWrite}
                onEdit={handleEdit}
                onDeleted={handleDeleted}
                onToast={setToast}
              />
            ))}
          </div>
        )}
      </div>

      <WatchModal
        open={modalOpen}
        initial={editing}
        metrics={metrics}
        integrations={integrations}
        onClose={() => setModalOpen(false)}
        onSaved={handleSaved}
      />

      <Toast message={toast} onDone={() => setToast(null)} />
    </div>
  )
}
