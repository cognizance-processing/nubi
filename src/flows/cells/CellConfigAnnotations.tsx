/**
 * CellConfigAnnotations.jsx — read-only inline annotation strip for a notebook
 * cell (v4 "cells, not kinds").
 *
 * Surfaces the cell's config blocks so the notebook mental model stays lossless
 * with the canvas: a cell is SQL or Python, and everything advanced is a setting
 * shown here. Editing happens in NodeInspector (canvas); the notebook just reads
 * the same `config`.
 *
 *   materialized.kind ∈ {full, incremental} → "→ table (<kind>)" (+ target)
 *   for_each                                 → "for each: <items>"
 *   run_when                                 → "runs when: <expr>"
 *
 * Props:
 *   config {object} — the cell's config object
 */

import { Database, Layers, Filter } from 'lucide-react'

function truncate(s, n = 32) {
  if (s == null) return ''
  const str = String(s)
  return str.length > n ? str.slice(0, n - 1) + '…' : str
}

export default function CellConfigAnnotations({ config }) {
  const c = config ?? {}

  const mat = c.materialized
  const materialized = mat && mat.kind && mat.kind !== 'view' ? mat : null

  const fe = c.for_each
  const forEach = fe && fe.items != null && fe.items !== '' ? fe : null

  const runWhen = typeof c.run_when === 'string' && c.run_when.trim() ? c.run_when.trim() : null

  if (!materialized && !forEach && !runWhen) return null

  return (
    <div className="flex items-center gap-1.5 flex-wrap px-3 py-1.5 bg-surface-2/20 border-b border-border">
      {materialized && (
        <span
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-medium border bg-cyan-500/10 text-cyan-700 dark:text-cyan-300 border-cyan-300/50 dark:border-cyan-800"
          title={materialized.target ? `→ table (${materialized.kind}) · ${materialized.target}` : undefined}
        >
          <Database size={10} className="shrink-0" />
          → table ({materialized.kind})
          {materialized.target && (
            <span className="font-mono opacity-70">· {truncate(materialized.target, 20)}</span>
          )}
        </span>
      )}
      {forEach && (
        <span
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-medium border bg-indigo-500/10 text-indigo-700 dark:text-indigo-300 border-indigo-300/50 dark:border-indigo-800"
          title={`for each: ${forEach.items}`}
        >
          <Layers size={10} className="shrink-0" />
          for each: <span className="font-mono opacity-80">{truncate(forEach.items)}</span>
        </span>
      )}
      {runWhen && (
        <span
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-medium border bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-300/50 dark:border-amber-800"
          title={`runs when: ${runWhen}`}
        >
          <Filter size={10} className="shrink-0" />
          runs when: <span className="font-mono opacity-80">{truncate(runWhen)}</span>
        </span>
      )}
    </div>
  )
}
