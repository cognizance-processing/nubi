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
import Tag from '../../components/ui/Tag.jsx'

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
    <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-1.5">
      {materialized && (
        <Tag
          caps={false}
          title={materialized.target ? `→ table (${materialized.kind}) · ${materialized.target}` : undefined}
        >
          <Database size={10} className="shrink-0" aria-hidden="true" />
          → table ({materialized.kind})
          {materialized.target && <span className="text-subtle">· {truncate(materialized.target, 20)}</span>}
        </Tag>
      )}
      {forEach && (
        <Tag caps={false} title={`for each: ${forEach.items}`}>
          <Layers size={10} className="shrink-0" aria-hidden="true" />
          for each: <span className="text-subtle">{truncate(forEach.items)}</span>
        </Tag>
      )}
      {runWhen && (
        <Tag caps={false} title={`runs when: ${runWhen}`}>
          <Filter size={10} className="shrink-0" aria-hidden="true" />
          runs when: <span className="text-subtle">{truncate(runWhen)}</span>
        </Tag>
      )}
    </div>
  )
}
