/**
 * ExplainDrawer.jsx — metric explain drill-down panel.
 *
 * Calls POST /api/v1/metrics/{id}/explain with two time windows (current vs
 * comparison period) and renders the ranked dimension-contribution breakdown.
 *
 * Usage:
 *   <ExplainDrawer metricId="..." open onClose={() => ...} />
 *
 * The user supplies current + comparison date ranges; the drawer fetches and
 * renders a table of dimension × member contributions ranked by explanatory
 * power (delta share).
 */

import { useState, useCallback, useEffect, useRef } from 'react'
import { X, Loader2, AlertCircle, TrendingUp, TrendingDown, Minus, ChevronDown, ChevronRight } from 'lucide-react'
import { post } from '../../lib/api.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function today() {
  return new Date().toISOString().slice(0, 10)
}

function daysAgo(n) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

function fmt(n, digits = 2) {
  if (n == null) return '—'
  const v = Number(n)
  if (Number.isNaN(v)) return '—'
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(digits)}M`
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(digits)}K`
  return v.toLocaleString(undefined, { maximumFractionDigits: digits })
}

// ---------------------------------------------------------------------------
// DimensionBreakdown — collapsible per-dimension section
// ---------------------------------------------------------------------------

function DimensionBreakdown({ dim }) {
  const [open, setOpen] = useState(false)

  const pwr = (dim.explanatory_power ?? 0) * 100

  return (
    <div className="border border-border rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2.5 bg-surface hover:bg-surface-2/50 transition-colors text-left"
      >
        {open ? <ChevronDown size={13} className="text-muted shrink-0" /> : <ChevronRight size={13} className="text-muted shrink-0" />}
        <span className="flex-1 text-xs font-semibold text-fg font-mono">{dim.dimension}</span>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[10px] text-muted">power</span>
          <div className="w-16 h-1.5 rounded-full bg-surface-2 overflow-hidden">
            <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, pwr)}%` }} />
          </div>
          <span className="text-[10px] font-mono text-muted w-8 text-right">{pwr.toFixed(0)}%</span>
        </div>
      </button>

      {open && (
        <div className="border-t border-border overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="bg-surface-2/40">
                <th className="text-left px-3 py-2 font-semibold text-muted">Member</th>
                <th className="text-right px-3 py-2 font-semibold text-muted">Current</th>
                <th className="text-right px-3 py-2 font-semibold text-muted">Comparison</th>
                <th className="text-right px-3 py-2 font-semibold text-muted">Delta</th>
                <th className="text-right px-3 py-2 font-semibold text-muted">Share</th>
              </tr>
            </thead>
            <tbody>
              {(dim.members ?? []).map((m, i) => {
                const dir = m.direction ?? (m.delta > 0 ? 'up' : m.delta < 0 ? 'down' : 'flat')
                return (
                  <tr key={i} className="border-t border-border/40 hover:bg-surface-2/20">
                    <td className="px-3 py-1.5 font-mono text-fg">
                      {m.member != null ? String(m.member) : '—'}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-muted">{fmt(m.current)}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-muted">{fmt(m.comparison)}</td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      <span className={
                        dir === 'up' ? 'text-emerald-600 dark:text-emerald-400' :
                        dir === 'down' ? 'text-red-500 dark:text-red-400' :
                        'text-muted'
                      }>
                        {dir === 'up' ? <TrendingUp size={11} className="inline mr-0.5" /> :
                         dir === 'down' ? <TrendingDown size={11} className="inline mr-0.5" /> :
                         <Minus size={11} className="inline mr-0.5" />}
                        {m.delta > 0 ? '+' : ''}{fmt(m.delta)}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-muted">
                      {((m.share ?? 0) * 100).toFixed(1)}%
                    </td>
                  </tr>
                )
              })}
              {dim.other && (
                <tr className="border-t border-border/40 bg-surface-2/10">
                  <td className="px-3 py-1.5 font-mono text-muted italic">other</td>
                  <td className="px-3 py-1.5 text-right font-mono text-muted">{fmt(dim.other.current)}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-muted">{fmt(dim.other.comparison)}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-muted">{fmt(dim.other.delta)}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-muted">
                    {((dim.other.share ?? 0) * 100).toFixed(1)}%
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          {dim.coverage != null && (
            <p className="text-[10px] text-muted px-3 py-1.5 border-t border-border/40">
              Coverage: {(dim.coverage * 100).toFixed(0)}% of total delta explained.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ExplainDrawer
// ---------------------------------------------------------------------------

const inputCls = [
  'h-7 text-xs border border-border rounded-lg px-2.5',
  'bg-surface text-fg placeholder:text-muted/50',
  'focus:outline-none focus:ring-1 focus:ring-ring/60',
  'transition-colors',
].join(' ')

/**
 * @param {{
 *   metricId: string,
 *   open: boolean,
 *   onClose: () => void,
 * }} props
 */
export default function ExplainDrawer({ metricId, open, onClose }) {
  const [currentStart, setCurrentStart] = useState(() => daysAgo(7))
  const [currentEnd, setCurrentEnd] = useState(() => today())
  const [compStart, setCompStart] = useState(() => daysAgo(14))
  const [compEnd, setCompEnd] = useState(() => daysAgo(7))
  const [topN, setTopN] = useState(10)

  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const drawerRef = useRef(null)

  const runExplain = useCallback(async () => {
    if (!metricId) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const data = await post(`/metrics/${metricId}/explain`, {
        current: { start: currentStart, end: currentEnd },
        comparison: { start: compStart, end: compEnd },
        top_n: topN,
        include_summary: true,
      })
      setResult(data)
    } catch (err) {
      setError(err?.message || 'Explain request failed.')
    } finally {
      setLoading(false)
    }
  }, [metricId, currentStart, currentEnd, compStart, compEnd, topN])

  // Escape to close
  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  // Focus the drawer panel on open
  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => drawerRef.current?.focus(), 50)
    return () => clearTimeout(t)
  }, [open])

  if (!open) return null

  const delta = result?.delta_total ?? null
  const deltaDir = delta != null ? (delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat') : null

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      {/* Drawer */}
      <div
        ref={drawerRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="explain-drawer-title"
        className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-xl flex flex-col bg-surface border-l border-border shadow-2xl outline-none"
      >
        {/* Header */}
        <div className="shrink-0 flex items-center gap-3 px-5 py-4 border-b border-border">
          <div className="flex-1 min-w-0">
            <h2 id="explain-drawer-title" className="text-sm font-semibold text-fg">Explain metric</h2>
            <p className="text-xs text-muted font-mono truncate">{metricId}</p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="h-8 w-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          >
            <X size={15} />
          </button>
        </div>

        {/* Time window form */}
        <div className="shrink-0 px-5 py-4 border-b border-border space-y-3">
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider">Time windows</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <p className="text-[10px] text-muted mb-1">Current start</p>
              <input type="date" value={currentStart} onChange={e => setCurrentStart(e.target.value)} className={`${inputCls} w-full`} />
            </div>
            <div>
              <p className="text-[10px] text-muted mb-1">Current end</p>
              <input type="date" value={currentEnd} onChange={e => setCurrentEnd(e.target.value)} className={`${inputCls} w-full`} />
            </div>
            <div>
              <p className="text-[10px] text-muted mb-1">Comparison start</p>
              <input type="date" value={compStart} onChange={e => setCompStart(e.target.value)} className={`${inputCls} w-full`} />
            </div>
            <div>
              <p className="text-[10px] text-muted mb-1">Comparison end</p>
              <input type="date" value={compEnd} onChange={e => setCompEnd(e.target.value)} className={`${inputCls} w-full`} />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-muted">Top N</span>
              <select
                value={topN}
                onChange={e => setTopN(Number(e.target.value))}
                className={`${inputCls} w-16`}
              >
                {[5, 10, 20, 50].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <button
              onClick={runExplain}
              disabled={loading || !metricId}
              className="flex items-center gap-1.5 h-8 px-4 text-xs font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 transition-all ml-auto"
            >
              {loading ? <Loader2 size={12} className="animate-spin" /> : null}
              {loading ? 'Running…' : 'Run explain'}
            </button>
          </div>
          {error && (
            <div className="flex items-center gap-1.5 text-xs text-red-500">
              <AlertCircle size={12} /> {error}
            </div>
          )}
        </div>

        {/* Results */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {loading && (
            <div className="flex flex-col items-center gap-2 py-12 text-center text-muted" aria-busy="true" aria-live="polite">
              <Loader2 size={18} className="animate-spin" aria-hidden="true" />
              <p className="text-xs">Running explain…</p>
            </div>
          )}

          {!result && !loading && !error && (
            <div className="flex flex-col items-center gap-2 py-12 text-center text-muted">
              <p className="text-xs">Set the time windows above and click Run explain to see which dimensions drove the change.</p>
            </div>
          )}

          {result && (
            <>
              {/* Summary header */}
              <div className="rounded-xl border border-border bg-surface-2/30 p-4">
                <div className="grid grid-cols-3 gap-3 text-center">
                  <div>
                    <p className="text-[10px] text-muted mb-1">Current</p>
                    <p className="text-sm font-semibold font-mono text-fg">{fmt(result.current_total)}</p>
                  </div>
                  <div>
                    <p className="text-[10px] text-muted mb-1">Comparison</p>
                    <p className="text-sm font-semibold font-mono text-fg">{fmt(result.comparison_total)}</p>
                  </div>
                  <div>
                    <p className="text-[10px] text-muted mb-1">Delta</p>
                    <p className={[
                      'text-sm font-semibold font-mono',
                      deltaDir === 'up' ? 'text-emerald-600 dark:text-emerald-400' :
                      deltaDir === 'down' ? 'text-red-500 dark:text-red-400' : 'text-fg',
                    ].join(' ')}>
                      {delta != null && delta > 0 ? '+' : ''}{fmt(result.delta_total)}
                    </p>
                  </div>
                </div>
                {result.summary && (
                  <p className="mt-3 pt-3 border-t border-border/60 text-xs text-muted leading-relaxed">{result.summary}</p>
                )}
              </div>

              {/* Dimension breakdowns */}
              <p className="text-[10px] font-semibold text-muted uppercase tracking-wider">
                Dimension breakdown ({(result.dimensions ?? []).length})
              </p>
              {(result.dimensions ?? []).length === 0 && (
                <p className="text-xs text-muted italic">No dimensions available for this metric.</p>
              )}
              {(result.dimensions ?? []).map((dim, i) => (
                <DimensionBreakdown key={i} dim={dim} />
              ))}
            </>
          )}
        </div>
      </div>
    </>
  )
}
