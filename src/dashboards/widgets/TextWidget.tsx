/**
 * TextWidget.jsx — Spec-driven text widget for the SpecRenderer.
 *
 * Props
 * -----
 * widget         {object}  A spec Widget object with type === 'text'.
 * providerTable  {Table?}  BET-3 provider rows (query mode only; see below).
 *
 * widget shape:
 *   {
 *     id, type: 'text',
 *     query_id?,                     // only used when props.source === 'query'
 *     encoding: { value?: string },  // text column read when props.source === 'query'
 *     source?: { provider, result }, // BET-3 provider binding
 *     props: {
 *       source?: 'static' | 'query', // default 'static'
 *       content?: string,            // static only — markdown
 *       row?: number,                // query only: row index (default 0; negative → 0;
 *                                    // past the last row → nothing, not the last row)
 *       prefix?: string, suffix?: string,  // query only: plain-text affixes
 *       fontSize?, fontWeight?, align?, heading?  // see textStyle.ts
 *     }
 *   }
 *
 * Behaviour
 * ---------
 * - Static: renders `props.content` as markdown via MarkdownRenderer.
 * - Query: renders `prefix + cell + suffix` as PLAIN text (no markdown parse
 *   on query values). Fetch mirrors ImageWidget / DateDisplayWidget (params,
 *   filters, refresh, cancellation). `props.source` is unrelated to
 *   `widget.source` (the provider binding) — see ImageWidget.tsx.
 * - Empty content / value renders a subtle placeholder so the slot stays visible.
 */

import { useEffect, useState } from 'react'
import MarkdownRenderer from '../../components/MarkdownRenderer.jsx'
import { textWidgetLook } from './textStyle.js'
import { textQueryRow, textQueryValue } from './textQuery.js'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { useResolvedParams, useResolvedFilters } from '../VariableStore.jsx'
import { useRefreshEpoch } from '../RefreshContext.jsx'
import WidgetError from '../../components/app/WidgetError.jsx'

// Only show "Loading…" if a fetch is still running after this long.
const LOADING_DELAY_MS = 200

function Placeholder({ children }) {
  return (
    <div className="flex items-center justify-center h-full px-5 py-4 text-sm text-muted italic opacity-60">
      {children}
    </div>
  )
}

export default function TextWidget({ widget, providerTable = null }) {
  const { props: wProps = {} } = widget
  const wantsQuery = wProps.source === 'query'
  const content = wProps.content ?? ''
  const valueCol = widget.encoding?.value ?? ''

  const resolvedParams = useResolvedParams(widget.params)
  const resolvedFilters = useResolvedFilters(widget.filters)
  const refreshEpoch = useRefreshEpoch()

  // The raw cell; prefix/suffix are applied at render so editing them never
  // re-runs the query.
  const [queryCell, setQueryCell] = useState(null)
  const [loading, setLoading] = useState(false)
  const [queryError, setQueryError] = useState(null)
  const [retryEpoch, setRetryEpoch] = useState(0)
  const [rowOutOfRange, setRowOutOfRange] = useState(false)

  function extractFromTable(table) {
    setRowOutOfRange(false)
    if (!table || !table.numRows || !valueCol) { setQueryCell(null); return }
    const col = table.getChild(valueCol)
    if (!col) { setQueryCell(null); return }
    const row = textQueryRow(wProps.row)
    if (row >= table.numRows) { setQueryCell(null); setRowOutOfRange(true); return }
    setQueryCell(col.get(row))
  }

  useEffect(() => {
    setQueryError(null)
    if (!wantsQuery) return
    if (providerTable !== null) { extractFromTable(providerTable); return }
    if (!widget.query_id) { setQueryCell(null); return }
    let cancelled = false
    const loadingTimer = setTimeout(() => { if (!cancelled) setLoading(true) }, LOADING_DELAY_MS)
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
        if (error) {
          setQueryCell(null)
          setQueryError(error.message ?? 'Query failed.')
        } else {
          extractFromTable(table)
        }
      } catch (err) {
        if (!cancelled) {
          setQueryCell(null)
          setQueryError(err?.message ?? 'Query failed.')
        }
      } finally {
        clearTimeout(loadingTimer)
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true; clearTimeout(loadingTimer); setLoading(false) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantsQuery, widget.query_id, valueCol, wProps.row, JSON.stringify(resolvedParams), JSON.stringify(resolvedFilters), refreshEpoch, retryEpoch, providerTable])

  // The grid cell already carries widget.style (including an author-set
  // `color`), so an explicit colour must be allowed to win — the blanket
  // `text-fg` class silently overrode it, which turned coloured section
  // headings on migrated boards into plain theme-foreground text.
  const inheritsColor = typeof widget.style?.color === 'string' && widget.style.color

  // MarkdownRenderer's own elements carry `text-fg`, which beats plain
  // inheritance, so an explicit colour additionally forces descendants to
  // inherit. Without an author colour nothing changes.
  // Markdown blocks carry their own vertical margins (`my-4` on a paragraph),
  // which on top of this padding pushed a ONE-LINE heading past a short grid
  // cell and produced a scrollbar on every section title. Collapse the leading
  // and trailing margins — the same reset used elsewhere in MarkdownRenderer —
  // at both nesting levels, since the renderer wraps its output in an element.
  const marginReset =
    '[&>*:first-child]:mt-0 [&>*:last-child]:mb-0 ' +
    '[&>*>*:first-child]:mt-0 [&>*>*:last-child]:mb-0'

  const look = textWidgetLook(wProps)
  const className =
    `h-full prose-sm flex flex-col justify-center ${look.className} ${marginReset}` +
    (inheritsColor ? ' [&_*]:text-inherit' : ' text-fg')

  if (wantsQuery) {
    const queryText = textQueryValue(queryCell, wProps.prefix, wProps.suffix)
    if (queryError) {
      return (
        <WidgetError
          message={queryError}
          queryId={widget.query_id}
          kind="text"
          onRetry={() => setRetryEpoch(e => e + 1)}
          compact
        />
      )
    }
    if (!queryText) {
      if (loading) {
        return <Placeholder><span className="not-italic animate-pulse text-xs">Loading…</span></Placeholder>
      }
      // A provider binding counts as bound even before its table arrives (or
      // when the provider failed) — it is not an unconfigured widget.
      const bound = (widget.query_id || providerTable !== null || widget.source) && valueCol
      return (
        <Placeholder>
          {!bound ? 'Pick a query and the text column.'
            : rowOutOfRange ? 'No value at this row'
            : '—'}
        </Placeholder>
      )
    }
    return (
      <div data-testid="text-widget" style={look.style} className={className}>
        <p className="whitespace-pre-wrap break-words m-0">{queryText}</p>
      </div>
    )
  }

  if (!content.trim()) {
    return <Placeholder>(empty text widget)</Placeholder>
  }

  return (
    <div data-testid="text-widget" style={look.style} className={className}>
      <MarkdownRenderer content={content} />
    </div>
  )
}
