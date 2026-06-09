/**
 * PlanGateDialog.jsx — SQLMesh-style plan gate dialog.
 *
 * Shown before "Run All" (durable) when the flow has a saved id.
 * Calls POST /lineage/plan with the current spec + (optionally) a changed cell
 * key to show downstream impact, then presents an IMPACT summary the user must
 * confirm before the durable run fires.
 *
 * Props:
 *   open          {boolean}        — controlled visibility
 *   spec          {object}         — current FlowSpec
 *   changedCellKey {string}        — optional; which cell key changed last; if
 *                                    empty, uses the first task key
 *   onConfirm     {Function}       — called when user clicks "Run"
 *   onCancel      {Function}       — called when user dismisses
 */

import { useState, useEffect, useCallback } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Play,
  X,
  ChevronDown,
  ChevronRight,
  Zap,
  AlertCircle,
  GitBranch,
} from 'lucide-react'
import { fetchLineagePlan } from '../lib/notebooks.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CHANGE_TYPE_META = {
  breaking: {
    label: 'Breaking',
    cls: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/25',
    icon: AlertTriangle,
    iconCls: 'text-rose-500',
  },
  non_breaking: {
    label: 'Non-breaking',
    cls: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/25',
    icon: Zap,
    iconCls: 'text-amber-500',
  },
}

function ChangeTypeBadge({ changeType }) {
  const meta = CHANGE_TYPE_META[changeType] ?? CHANGE_TYPE_META.non_breaking
  const Icon = meta.icon
  return (
    <span className={[
      'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold',
      meta.cls,
    ].join(' ')}>
      <Icon size={9} className={meta.iconCls} />
      {meta.label}
    </span>
  )
}

function ImpactRow({ impact, expanded, onToggle }) {
  return (
    <div className="rounded-lg border border-border/60 bg-surface-2/20 overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-surface-2/40 transition-colors"
      >
        {expanded
          ? <ChevronDown size={11} className="text-muted shrink-0" />
          : <ChevronRight size={11} className="text-muted shrink-0" />
        }
        <span className="flex-1 text-[12px] font-mono text-fg truncate min-w-0">
          {impact.cell_key}
        </span>
        <ChangeTypeBadge changeType={impact.change_type} />
        <span className="text-[10px] text-muted shrink-0">
          {impact.affected_columns.length} col{impact.affected_columns.length !== 1 ? 's' : ''}
        </span>
      </button>

      {expanded && impact.affected_columns.length > 0 && (
        <div className="px-3 pb-2 pt-0">
          <div className="flex flex-wrap gap-1 pt-1 border-t border-border/30">
            {impact.affected_columns.map(col => (
              <span
                key={col}
                className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-mono bg-blue-500/8 text-blue-600 dark:text-blue-400 border border-blue-500/15"
              >
                {col}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PlanGateDialog
// ---------------------------------------------------------------------------

export default function PlanGateDialog({ open, spec, changedCellKey, onConfirm, onCancel }) {
  const [loading, setLoading] = useState(false)
  const [plan, setPlan] = useState(null)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState({})
  const [confirming, setConfirming] = useState(false)

  // Derive the changed cell key: use the provided one, else first task key
  const resolvedCellKey = changedCellKey
    || (spec?.tasks?.[0]?.key ?? '')

  const loadPlan = useCallback(async () => {
    if (!spec || !open) return
    setLoading(true)
    setError(null)
    setPlan(null)
    setExpanded({})
    const res = await fetchLineagePlan({ spec, changed_cell_key: resolvedCellKey })
    setLoading(false)
    if (!res) {
      setError('Plan call failed — check the console.')
    } else {
      setPlan(res)
    }
  }, [spec, resolvedCellKey, open])

  useEffect(() => {
    if (open) loadPlan()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const handleConfirm = useCallback(async () => {
    setConfirming(true)
    await onConfirm?.()
    setConfirming(false)
  }, [onConfirm])

  const toggleExpand = useCallback((key) => {
    setExpanded(prev => ({ ...prev, [key]: !prev[key] }))
  }, [])

  if (!open) return null

  const impact = plan?.downstream_impact ?? []
  const breakingCount = impact.filter(i => i.change_type === 'breaking').length
  const nonBreakingCount = impact.filter(i => i.change_type === 'non_breaking').length
  const hasIssues = (plan?.issues ?? []).length > 0
  const isValid = plan?.valid ?? true

  return (
    // Backdrop
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-[2px]"
      onClick={e => { if (e.target === e.currentTarget) onCancel?.() }}
    >
      <div className="relative w-full max-w-lg mx-4 rounded-2xl border border-border bg-surface shadow-2xl overflow-hidden max-h-[80vh] flex flex-col">

        {/* Header */}
        <div className="shrink-0 flex items-center gap-2.5 px-5 py-4 border-b border-border bg-surface-2/30">
          <div className="w-8 h-8 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
            <GitBranch size={15} className="text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-fg leading-tight">Run Plan</p>
            <p className="text-[11px] text-muted mt-0.5">
              Review downstream impact before executing
            </p>
          </div>
          <button
            onClick={onCancel}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            title="Cancel"
          >
            <X size={13} />
          </button>
        </div>

        {/* Body (scrollable) */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 min-h-0">

          {/* Loading state */}
          {loading && (
            <div className="flex flex-col items-center justify-center py-10 gap-3 text-muted">
              <Loader2 size={22} className="animate-spin text-primary" />
              <span className="text-xs">Analysing column lineage…</span>
            </div>
          )}

          {/* Error state */}
          {!loading && error && (
            <div className="flex items-start gap-2 px-3 py-3 rounded-lg bg-rose-500/5 border border-rose-500/20 text-xs text-rose-600 dark:text-rose-400">
              <AlertCircle size={13} className="shrink-0 mt-0.5" />
              <span className="flex-1">{error}</span>
            </div>
          )}

          {/* Validation issues banner */}
          {!loading && !error && hasIssues && (
            <div className="flex items-start gap-2 px-3 py-3 rounded-lg bg-amber-500/5 border border-amber-500/20 text-xs text-amber-700 dark:text-amber-400">
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold mb-1">Spec has {isValid ? 'warnings' : 'errors'}</p>
                <ul className="space-y-0.5">
                  {plan.issues.map((issue, i) => (
                    <li key={i} className="opacity-80">{issue}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}

          {/* Impact summary */}
          {!loading && !error && plan && (
            <>
              {/* Changed cell */}
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-muted shrink-0">Changed cell:</span>
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-primary/15 text-primary border border-primary/30">
                  {resolvedCellKey || '(all)'}
                </span>
              </div>

              {/* Stat chips */}
              <div className="flex items-center gap-2 flex-wrap">
                {impact.length === 0 ? (
                  <div className="flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400">
                    <CheckCircle2 size={13} />
                    No downstream cells affected
                  </div>
                ) : (
                  <>
                    {breakingCount > 0 && (
                      <div className="flex items-center gap-1 text-xs text-rose-600 dark:text-rose-400">
                        <AlertTriangle size={11} />
                        <span className="font-semibold">{breakingCount}</span> breaking
                      </div>
                    )}
                    {nonBreakingCount > 0 && (
                      <div className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                        <Zap size={11} />
                        <span className="font-semibold">{nonBreakingCount}</span> non-breaking
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Impact list */}
              {impact.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    Affected downstream cells
                  </p>
                  {impact.map(item => (
                    <ImpactRow
                      key={item.cell_key}
                      impact={item}
                      expanded={!!expanded[item.cell_key]}
                      onToggle={() => toggleExpand(item.cell_key)}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer actions */}
        <div className="shrink-0 flex items-center justify-end gap-2 px-5 py-3.5 border-t border-border bg-surface-2/20">
          <button
            onClick={onCancel}
            className="px-3.5 h-9 text-xs font-medium rounded-lg border border-border text-fg hover:bg-surface-2 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={loading || confirming || (!isValid && hasIssues)}
            title={!isValid && hasIssues ? 'Fix spec errors before running' : 'Run all cells durably'}
            className="flex items-center gap-1.5 px-4 h-9 text-xs font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {confirming
              ? <Loader2 size={12} className="animate-spin" />
              : <Play size={12} />
            }
            {confirming ? 'Starting…' : 'Run all'}
          </button>
        </div>
      </div>
    </div>
  )
}
