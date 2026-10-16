/**
 * WatchesPage — proactive metric alerts (WATCHES).
 *
 * Route:  /watches
 *
 * A *watch* monitors a single governed metric and fires when a threshold (or a
 * change-over-time rule) is breached. On breach the backend composes an AI
 * explanation and dispatches it to a connected notify channel (Email). This
 * page is the full CRUD + manual-evaluate surface:
 *
 *  - lists watches as ruled rows (name, metric, condition summary, last state).
 *  - create / edit form (modal): pick a metric, choose dimensions / time grain,
 *    set a threshold (op + value) OR a change_pct rule, pick a connected
 *    integration to alert, enabled switch.
 *  - "Evaluate" per watch → POST /evaluate, shows {breached, value,
 *    explanation} inline under the row.
 *  - delete (inline confirm).
 *
 * Presentation follows the Swiss app-page pattern (PageShell + .ap-row list,
 * Tags for state, EmptyWatches art). Writes are gated on useCanWrite().
 */

import { useEffect, useState, useCallback, useMemo } from 'react'
import {
  Plus,
  RefreshCw,
  Trash2,
  Pencil,
  Zap,
  TrendingUp,
  Gauge,
} from 'lucide-react'

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
import { toast } from '../../components/ui/Toast.jsx'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'
import Modal from '../../components/ui/Modal.jsx'
import Input, { Select, Field } from '../../components/ui/Input.jsx'
import SegmentedControl from '../../components/ui/SegmentedControl.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import PageShell from '../../components/app/PageShell.jsx'
import { EmptyWatches } from '../../components/illustrations'
import { ListSkeleton, LoadError } from './appShared.jsx'
import '../../components/app/appPatterns.css'

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
// Last-state tag
// ---------------------------------------------------------------------------

function StateTag({ state }) {
  const map = {
    breached: { variant: 'danger', label: 'Breached' },
    ok:       { variant: 'success', label: 'OK' },
    error:    { variant: 'warning', label: 'Error' },
  }
  const m = map[state]
  if (!m) return <Tag>Not evaluated</Tag>
  return <Tag variant={m.variant} dot>{m.label}</Tag>
}

// ---------------------------------------------------------------------------
// WatchRow — one watch in the list
// ---------------------------------------------------------------------------

function WatchRow({ watch, metricName, canWrite, onEdit, onDeleted }) {
  const [evaluating, setEvaluating] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [result, setResult] = useState<any>(null)

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
      toast.success(`Deleted "${watch.name}".`)
      onDeleted?.(watch.id)
    } else {
      toast.error('Couldn’t delete this watch. Try again.')
      setConfirmDelete(false)
    }
  }, [watch.id, watch.name, onDeleted])

  // Last state: prefer the live evaluate result, else the watch's stored state.
  const lastState = result?.state ?? watch.last_state ?? watch.config?.last_state ?? null

  const detail = [
    metricName || watch.metric_id || '—',
    conditionSummary(watch),
    watch.config?.time_grain ? `per ${watch.config.time_grain}` : null,
    Array.isArray(watch.config?.dimensions) && watch.config.dimensions.length > 0
      ? `by ${watch.config.dimensions.join(', ')}`
      : null,
  ].filter(Boolean)

  return (
    <li className="list-none">
      <div className="ap-row">
        <div className="ap-row-main">
          <div className="ap-row-title">
            <span className="ap-trunc">{watch.name}</span>
            <StateTag state={lastState} />
            {!isEnabled(watch) && <Tag>Disabled</Tag>}
          </div>
          <div className="ap-row-sub ap-mono" style={{ fontSize: 12 }}>
            {detail.join('  ·  ')}
          </div>
        </div>

        <div className="ap-actions">
          <Button variant="ghost" size="sm" onClick={handleEvaluate} loading={evaluating} title="Evaluate now">
            {!evaluating && <Zap size={14} aria-hidden="true" />}
            {evaluating ? 'Evaluating' : 'Evaluate'}
          </Button>

          {canWrite && (
            <Button variant="ghost" size="icon-sm" onClick={() => onEdit?.(watch)} title="Edit" aria-label={`Edit "${watch.name}"`}>
              <Pencil size={14} aria-hidden="true" />
            </Button>
          )}

          {!canWrite ? null : confirmDelete ? (
            <span className="inline-flex items-center gap-1.5" role="group" aria-label={`Delete "${watch.name}"?`}>
              <span className="text-xs text-muted">Delete this watch?</span>
              <Button variant="danger" size="xs" onClick={handleDelete} loading={deleting} aria-label={`Confirm delete "${watch.name}"`}>
                Delete
              </Button>
              <Button variant="ghost" size="xs" onClick={() => setConfirmDelete(false)} disabled={deleting}>
                Keep
              </Button>
            </span>
          ) : (
            <Button variant="ghost" size="icon-sm" onClick={() => setConfirmDelete(true)} title="Delete" aria-label={`Delete "${watch.name}"`}>
              <Trash2 size={14} aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>

      {/* Evaluate result — a 2px rule says ok / breached / error */}
      {result && (
        <div className="py-2 border-b border-border">
          <div
            className={`ap-result ${result.state === 'breached' || result.state === 'error' ? 'is-bad' : 'is-ok'}`}
            style={{ marginLeft: 12 }}
            role="status"
          >
            <div className="ap-result-head">
              <StateTag state={result.state} />
              {result.value !== undefined && result.value !== null && (
                <span className="ap-meta">value <b className="font-medium" style={{ color: 'var(--text)' }}>{fmtNum(result.value)}</b></span>
              )}
              {typeof result.sent === 'number' && result.sent > 0 && (
                <span className="ap-meta">alerted {result.sent} channel{result.sent === 1 ? '' : 's'}</span>
              )}
            </div>
            {result.explanation && <div className="ap-result-msg">{result.explanation}</div>}
            {result.error && <div className="ap-result-msg">{result.error}</div>}
            {result.state === 'ok' && !result.explanation && (
              <div className="ap-result-msg">Within threshold. No alert was sent.</div>
            )}
          </div>
        </div>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------
// Draft helpers (create / edit form state)
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
    enabled: cfg.enabled === undefined ? true : Boolean(cfg.enabled),
  }
}

/** Build the API body { name, metric_id, config } from a draft. Returns
 *  { body } on success or { error } when the draft is invalid. */
function bodyFromDraft(draft: Record<string, any>) {
  const name = draft.name.trim()
  if (!name) return { error: 'Give the watch a name.' }
  if (!draft.metric_id) return { error: 'Pick a metric to monitor.' }

  const config: Record<string, any> = {}

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

  const channel_config: Record<string, any> = {}
  if (draft.integrationId) {
    channel_config.integration_id = draft.integrationId
  }
  if (Object.keys(channel_config).length > 0) config.channel_config = channel_config

  config.enabled = Boolean(draft.enabled)

  return { body: { name, metric_id: draft.metric_id, config } }
}

/** Square switch (role=switch): ink when on. */
function Switch({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      onClick={() => onChange?.(!checked)}
      className="ap-switch"
    >
      <span className="ap-switch-knob" />
    </button>
  )
}

function WatchModal({ open, initial, metrics, integrations, onClose, onSaved }) {
  const [draft, setDraft] = useState(blankDraft)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  const ruleHint = RULE_KINDS.find(k => k.id === draft.ruleKind)?.hint
  const opId = draft.ruleKind === 'threshold' ? 'watch-threshold-op' : 'watch-change-op'
  const opVal = draft.ruleKind === 'threshold' ? draft.thresholdOp : draft.changeOp

  return (
    <Modal
      open={open}
      onClose={saving ? undefined : onClose}
      title={draft.id ? 'Edit watch' : 'New watch'}
      description="Watches a governed metric and alerts a channel when a rule is breached."
      size="lg"
    >
      <form onSubmit={(e) => { e.preventDefault(); handleSave() }}>
        <Modal.Body className="flex flex-col gap-5">
          <Input
            id="watch-name"
            label="Name"
            value={draft.name}
            onChange={e => set({ name: e.target.value })}
            placeholder="Revenue dropped"
            autoFocus
          />

          <Select
            id="watch-metric"
            label="Metric to monitor"
            value={draft.metric_id}
            onChange={e => set({ metric_id: e.target.value })}
            hint={metrics.length === 0 ? 'No metrics found. Expose a query as a metric first, then create a watch on it.' : undefined}
          >
            <option value="">Select a metric</option>
            {metrics.map(m => <option key={m.id} value={m.id}>{m.name || m.id}</option>)}
          </Select>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Input
              id="watch-dimensions"
              label="Dimensions"
              value={draft.dimensions}
              onChange={e => set({ dimensions: e.target.value })}
              placeholder="region, plan"
              hint={selectedMetric?.dimensions?.length > 0 ? `Available: ${selectedMetric.dimensions.join(', ')}` : 'Comma-separated. Optional.'}
            />
            <Select
              id="watch-time-grain"
              label="Time grain"
              value={draft.time_grain}
              onChange={e => set({ time_grain: e.target.value })}
            >
              {TIME_GRAINS.map(g => <option key={g || 'none'} value={g}>{g || 'none'}</option>)}
            </Select>
          </div>

          {/* Rule kind switcher */}
          <div className="flex flex-col gap-2">
            <span className="nubi-label">Breach rule</span>
            <SegmentedControl
              size="md"
              value={draft.ruleKind}
              onChange={(v) => set({ ruleKind: v })}
              options={RULE_KINDS.map(k => ({
                value: k.id,
                label: <span className="inline-flex items-center gap-1.5"><k.Icon size={14} aria-hidden="true" /> {k.label}</span>,
              }))}
              className="w-fit"
            />
            <p className="nubi-hint m-0">{ruleHint}</p>
          </div>

          {/* Rule inputs */}
          <div className="grid grid-cols-[120px_1fr] gap-4 items-start">
            <Select
              id={opId}
              label="Operator"
              value={opVal}
              onChange={e => set(draft.ruleKind === 'threshold' ? { thresholdOp: e.target.value } : { changeOp: e.target.value })}
              className="font-mono"
              options={THRESHOLD_OPS}
            />
            {draft.ruleKind === 'threshold' ? (
              <Input
                id="watch-threshold-value"
                label="Value"
                type="number"
                value={draft.thresholdValue}
                onChange={e => set({ thresholdValue: e.target.value })}
                placeholder="1000"
                className="font-mono"
              />
            ) : (
              <Input
                id="watch-change-value"
                label="Change %"
                type="number"
                value={draft.changeValue}
                onChange={e => set({ changeValue: e.target.value })}
                placeholder="-10"
                className="font-mono"
                hint="Compared with the previous period."
              />
            )}
          </div>

          {/* Channel config — pick a connected integration (Email). */}
          <fieldset className="ap-note" style={{ display: 'block', margin: 0 }}>
            <legend className="sr-only">Alert channel</legend>
            <span className="t-label block mb-2">Alert channel</span>
            {integrations.length > 0 ? (
              <>
                <Select
                  id="watch-integration"
                  label="Connected integration"
                  value={draft.integrationId}
                  onChange={e => set({ integrationId: e.target.value })}
                >
                  <option value="">No notification (test mode)</option>
                  {integrations.map(it => (
                    <option key={it.id} value={it.id}>
                      {(it.name || it.kind)}{it.enabled === false ? ' (disabled)' : ''}
                    </option>
                  ))}
                </Select>
                <p className="nubi-hint mt-2 mb-0">Manage channels in Settings, Integrations.</p>
              </>
            ) : (
              <p className="m-0">
                No integrations connected. Connect an email channel in Settings, Integrations to be
                alerted, or leave this watch in test mode (no notification is sent).
              </p>
            )}
          </fieldset>

          {/* Enabled switch */}
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="m-0 text-sm font-semibold text-fg">Enabled</p>
              <p className="m-0 text-xs text-muted">Included in scheduled sweeps.</p>
            </div>
            <Switch
              checked={draft.enabled}
              onChange={(v) => set({ enabled: v })}
              label={`${draft.enabled ? 'Disable' : 'Enable'} this watch`}
            />
          </div>

          {error && (
            <div className="nubi-notice nubi-notice-error text-xs" role="alert"><span>{error}</span></div>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button type="button" variant="secondary" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button type="submit" loading={saving}>{draft.id ? 'Save changes' : 'Create watch'}</Button>
        </Modal.Footer>
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------------------
// WatchesPage
// ---------------------------------------------------------------------------

export default function WatchesPage() {
  const canWrite = useCanWrite()

  const [watches, setWatches] = useState<any[]>([])
  const [metrics, setMetrics] = useState<any[]>([])
  const [integrations, setIntegrations] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<any>(null)

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<any>(null)

  const metricNameById = useMemo(() => {
    const map = {}
    for (const m of metrics) map[m.id] = m.name || m.id
    return map
  }, [metrics])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [w, m, ints] = await Promise.all([listWatches(), listMetrics(), listIntegrations()])
      setWatches(Array.isArray(w) ? w : [])
      setMetrics(Array.isArray(m) ? m : [])
      setIntegrations(Array.isArray(ints) ? ints : [])
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
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
    toast.success(editing ? 'Watch updated.' : 'Watch created.')
  }, [editing, load])

  const handleDeleted = useCallback((id) => {
    setWatches(prev => prev.filter(w => w.id !== id))
  }, [])

  const eyebrow = loading || error ? 'Operate' : `Operate · ${watches.length} ${watches.length === 1 ? 'watch' : 'watches'}`

  return (
    <PageShell
      eyebrow={eyebrow}
      title="Watches"
      description="A watch monitors a metric and fires when a threshold or change rule is breached. It composes an explanation and notifies your channel."
      actions={
        <>
          <Button variant="ghost" size="icon-sm" onClick={load} disabled={loading} aria-label="Refresh watches" title="Refresh">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          </Button>
          {canWrite && (
            <Button onClick={handleNew}>
              <Plus size={14} aria-hidden="true" /> New watch
            </Button>
          )}
        </>
      }
    >
      {loading && <ListSkeleton rows={3} tile={false} />}

      {!loading && error && (
        <LoadError title="Watches didn’t load" error={error} onRetry={load} />
      )}

      {!loading && !error && watches.length === 0 && (
        <EmptyState
          art={<EmptyWatches />}
          title="No watches yet"
          description={canWrite
            ? 'A watch checks a metric on a schedule and emails your team the moment it crosses a threshold or moves sharply against the previous period.'
            : 'Watches are created by editors. Ask one to set up an alert for a metric you care about.'}
          action={canWrite ? <Button onClick={handleNew}><Plus size={14} aria-hidden="true" /> Create watch</Button> : undefined}
        />
      )}

      {!loading && !error && watches.length > 0 && (
        <ul className="ap-list m-0 p-0" aria-label="Watches">
          {watches.map(w => (
            <WatchRow
              key={w.id}
              watch={w}
              metricName={metricNameById[w.metric_id]}
              canWrite={canWrite}
              onEdit={handleEdit}
              onDeleted={handleDeleted}
            />
          ))}
        </ul>
      )}

      <WatchModal
        open={modalOpen}
        initial={editing}
        metrics={metrics}
        integrations={integrations}
        onClose={() => setModalOpen(false)}
        onSaved={handleSaved}
      />
    </PageShell>
  )
}
