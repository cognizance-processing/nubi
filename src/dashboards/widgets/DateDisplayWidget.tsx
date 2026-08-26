/**
 * DateDisplayWidget.jsx — a read-only caption showing WHICH period a board is
 * currently reporting on ("Period 1 · 1 Jan 2025 – 31 Dec 2025").
 *
 * widget shape (type === 'datedisplay'):
 *   {
 *     id, type: 'datedisplay',
 *     query_id?,                       // only used when source === 'query'
 *     encoding: { value?: string },    // column read when source === 'query'
 *     props: {
 *       label?: string,        // caption above the date, e.g. "Period 1"
 *       source?: 'variable' | 'query' | 'static' | 'now',   // default 'variable'
 *       variable?: string,     // variable name read when source === 'variable'
 *       text?: string,         // literal shown when source === 'static'
 *       row?: number,          // row index read when source === 'query' (default 0)
 *       format?: 'iso' | 'medium' | 'long' | 'short' | 'month' | 'raw',
 *       separator?: string,    // between from/to on a range, default '–'
 *       prefix?: string, suffix?: string,
 *       align?: 'left' | 'center' | 'right',
 *     },
 *     pos
 *   }
 *
 * Why a widget rather than a `text` widget with a token: a board's reporting
 * period is a LIVE value — it moves when a date filter moves, and a relative
 * preset ("last 30 days") has no literal text to interpolate at author time.
 * `source: 'variable'` is the common case: point it at the same variable a
 * daterange filter writes and the caption tracks the filter, presets included.
 */

import { useEffect, useMemo, useState } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { useResolvedParams, useResolvedFilters, useVariableValues } from '../VariableStore.jsx'
import { useRefreshEpoch } from '../RefreshContext.jsx'
import { formatDateValue, captionForVariable } from '../legacyWidgetText.js'
import { resolveTextColors } from '../textColors.js'

export default function DateDisplayWidget({ widget, providerTable = null }) {
  const props = widget.props ?? {}
  const source = props.source ?? 'variable'
  const format = props.format ?? 'medium'
  const separator = props.separator ?? '–'
  const valueCol = widget.encoding?.value ?? ''

  const variables = useVariableValues()
  const resolvedParams = useResolvedParams(widget.params)
  const resolvedFilters = useResolvedFilters(widget.filters)
  const refreshEpoch = useRefreshEpoch()

  const [queryValue, setQueryValue] = useState(null)

  const wantsQuery = source === 'query'

  function extractFromTable(table) {
    if (!table || !table.numRows || !valueCol) { setQueryValue(null); return }
    const col = table.getChild(valueCol)
    if (!col) { setQueryValue(null); return }
    const row = Number.isInteger(props.row) ? Math.min(props.row, table.numRows - 1) : 0
    setQueryValue(col.get(row))
  }

  useEffect(() => {
    if (!wantsQuery) return
    if (providerTable !== null) { extractFromTable(providerTable); return }
    if (!widget.query_id) return
    let cancelled = false
    ;(async () => {
      try {
        const hasParams = Object.keys(resolvedParams).length > 0
        const hasFilters = resolvedFilters.length > 0
        const { table, error } = await runArrowQueryById(
          widget.query_id,
          (hasParams || hasFilters)
            ? { namedParams: hasParams ? resolvedParams : undefined, filters: hasFilters ? resolvedFilters : undefined }
            : undefined,
        )
        if (cancelled) return
        if (error) setQueryValue(null)
        else extractFromTable(table)
      } catch {
        if (!cancelled) setQueryValue(null)
      }
    })()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantsQuery, widget.query_id, valueCol, props.row, JSON.stringify(resolvedParams), JSON.stringify(resolvedFilters), refreshEpoch, providerTable])

  const text = useMemo(() => {
    if (source === 'static') return props.text ?? ''
    if (source === 'now') return formatDateValue(new Date().toISOString().slice(0, 10), format)
    if (source === 'query') return formatDateValue(queryValue, format)
    return captionForVariable(variables[props.variable ?? ''], format, separator)
  }, [source, props.text, props.variable, format, separator, queryValue, variables])

  const alignCls = props.align === 'center' ? 'items-center text-center'
    : props.align === 'right' ? 'items-end text-right'
    : 'items-start text-left'

  // The caption is the `muted` role and the date itself is `body`, so the two
  // are independently colourable from the Appearance panel.
  const ink = resolveTextColors(widget.style)

  return (
    <div className={`flex flex-col justify-center h-full w-full px-2 py-1 min-w-0 ${alignCls}`}>
      {props.label && (
        <span
          className="text-[11px] uppercase tracking-wide text-muted truncate max-w-full"
          style={ink.muted ? { color: ink.muted } : undefined}
        >
          {props.label}
        </span>
      )}
      <span
        className="text-sm font-semibold text-fg truncate max-w-full"
        style={ink.body ? { color: ink.body } : undefined}
        title={text || undefined}
      >
        {props.prefix ?? ''}{text || '—'}{props.suffix ?? ''}
      </span>
    </div>
  )
}
