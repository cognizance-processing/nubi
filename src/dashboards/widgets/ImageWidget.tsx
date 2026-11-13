/**
 * ImageWidget.tsx — an image block (logo, header art, branding, or a
 * per-row picture whose URL comes from a query).
 *
 * widget shape (type === 'image'):
 *   {
 *     id, type: 'image',
 *     query_id?,                     // only used when props.source === 'query'
 *     encoding: { value?: string },  // URL column read when props.source === 'query'
 *     source?: { provider, result }, // BET-3 provider binding (see below)
 *     props: {
 *       source?: 'static' | 'query',  // where the image URL comes from; default 'static'
 *       url?: string,         // static only (required then) — backend-relative,
 *                             // e.g. "/api/v1/images/{id}" (from uploadImage/
 *                             // uploadImageFromUrl/the upload_image AI tool),
 *                             // resolved via resolveImageUrl() before use as
 *                             // <img src>. A full http(s):// URL passes as-is.
 *       row?: number,         // query only: row index (default 0; negative → 0;
 *                             // past the last row → no image, not the last row's)
 *       alt?: string,
 *       fit?: 'contain' | 'cover',   // default 'contain'
 *       align?: 'left' | 'center' | 'right',  // default 'center'
 *     },
 *     pos
 *   }
 *
 * Two different `source` fields — do not confuse them:
 *   - `props.source` ('static' | 'query') says where the IMAGE URL comes from.
 *   - `widget.source` ({ provider, result }) is the BET-3 DataProvider binding,
 *     an alternative to `query_id` for supplying the rows in query mode. When
 *     the board resolves one, SpecRenderer hands the table in as
 *     `providerTable` and no query is run here.
 *
 * In query mode the cell is coerced to a trimmed string and goes through the
 * same resolveImageUrl() as a static URL, so a query may return either an
 * absolute http(s) URL or a stored "/api/v1/images/{id}" path. Query mode
 * mirrors DateDisplayWidget's fetch (params, filters, refresh, cancellation).
 *
 * Every resolved src (static or query) then passes safeImageSrc(): only
 * http(s) URLs and "/api/v1/images/{32 hex}" render; anything else
 * (javascript:, data:, //host, other paths) shows "Invalid image URL".
 */

import { useEffect, useState } from 'react'
import { resolveImageUrl } from '../../lib/api'
import { safeImageSrc } from '../safeImageSrc.js'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { useResolvedParams, useResolvedFilters } from '../VariableStore.jsx'
import { useRefreshEpoch } from '../RefreshContext.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import WidgetError from '../../components/app/WidgetError.jsx'

// Only show "Loading image…" if a fetch is still running after this long, so
// a fast WASM query doesn't flash a loading line before the image appears.
const LOADING_DELAY_MS = 200

function cellToUrl(value) {
  if (value === null || value === undefined) return ''
  return String(value).trim()
}

export default function ImageWidget({ widget, providerTable = null }) {
  const props = widget.props ?? {}
  const source = props.source === 'query' ? 'query' : 'static'
  const wantsQuery = source === 'query'
  const valueCol = widget.encoding?.value ?? ''
  const alt = props.alt ?? ''
  const fit = props.fit === 'cover' ? 'cover' : 'contain'
  const align = props.align === 'left' ? 'justify-start' : props.align === 'right' ? 'justify-end' : 'justify-center'

  const resolvedParams = useResolvedParams(widget.params)
  const resolvedFilters = useResolvedFilters(widget.filters)
  const refreshEpoch = useRefreshEpoch()

  const [queryUrl, setQueryUrl] = useState('')
  const [loading, setLoading] = useState(false)
  // A failed query shows the real failure, never "the selected cell is empty".
  const [queryError, setQueryError] = useState(null)
  // Bumped by the error state's Retry button to re-run the fetch effect.
  const [retryEpoch, setRetryEpoch] = useState(0)
  // The src whose <img> errored; `failed` is derived, so a new src is never
  // shown as failed, even for one render.
  const [failedSrc, setFailedSrc] = useState(null)
  // props.row is past the last returned row (e.g. a filter shrank the table).
  const [rowOutOfRange, setRowOutOfRange] = useState(false)

  function extractFromTable(table) {
    setRowOutOfRange(false)
    if (!table || !table.numRows || !valueCol) { setQueryUrl(''); return }
    const col = table.getChild(valueCol)
    if (!col) { setQueryUrl(''); return }
    // Negative → 0; past the end → no image (never another row's picture).
    // Same rule as svg_render._image_effective_url.
    const row = Number.isInteger(props.row) ? Math.max(props.row, 0) : 0
    if (row >= table.numRows) { setQueryUrl(''); setRowOutOfRange(true); return }
    setQueryUrl(cellToUrl(col.get(row)))
  }

  useEffect(() => {
    setQueryError(null)
    if (!wantsQuery) return
    if (providerTable !== null) { extractFromTable(providerTable); return }
    if (!widget.query_id) { setQueryUrl(''); return }
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
          setQueryUrl('')
          setQueryError(error.message ?? 'Query failed.')
        } else {
          extractFromTable(table)
        }
      } catch (err) {
        if (!cancelled) {
          setQueryUrl('')
          setQueryError(err?.message ?? 'Query failed.')
        }
      } finally {
        clearTimeout(loadingTimer)
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true; clearTimeout(loadingTimer); setLoading(false) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantsQuery, widget.query_id, valueCol, props.row, JSON.stringify(resolvedParams), JSON.stringify(resolvedFilters), refreshEpoch, retryEpoch, providerTable])

  // A refresh or retry gives the same URL another chance to load (e.g. after
  // a transient CDN error); a new URL already gets one via failedSrc !== src.
  useEffect(() => { setFailedSrc(null) }, [refreshEpoch, retryEpoch])

  const url = wantsQuery ? queryUrl : (props.url ?? '')
  const src = safeImageSrc(resolveImageUrl(url))
  const failed = failedSrc !== null && failedSrc === src

  if (wantsQuery && queryError) {
    return (
      <WidgetError
        message={queryError}
        queryId={widget.query_id}
        kind="image"
        onRetry={() => setRetryEpoch(e => e + 1)}
        compact
      />
    )
  }

  if (!url) {
    if (wantsQuery && loading) {
      return (
        <div className="flex items-center justify-center h-full px-4">
          <span className="text-xs text-muted animate-pulse">Loading image…</span>
        </div>
      )
    }
    const queryBound =
      (widget.query_id || providerTable !== null || widget.source) && valueCol
    if (wantsQuery) {
      if (!queryBound) {
        return (
          <div className="flex items-center justify-center h-full px-4">
            <EmptyState
              title="No image URL"
              description="Pick a query and the column that holds the image URL."
              compact
            />
          </div>
        )
      }
      if (rowOutOfRange) {
        const row = Number.isInteger(props.row) ? Math.max(props.row, 0) : 0
        return (
          <div className="flex items-center justify-center h-full px-4">
            <EmptyState
              title="No image at this row"
              description={`The query returned fewer than ${row + 1} rows.`}
              compact
            />
          </div>
        )
      }
      // Bound but no URL yet — provider still loading, empty cell, etc. (same as TextWidget).
      return (
        <div className="flex items-center justify-center h-full px-4 text-muted text-sm">
          —
        </div>
      )
    }
    return (
      <div className="flex items-center justify-center h-full px-4">
        <EmptyState title="No image set" description="Upload a file or paste a URL." compact />
      </div>
    )
  }

  if (src === null) {
    return (
      <div className="flex items-center justify-center h-full px-4">
        <EmptyState title="Invalid image URL" description="Use an http(s) URL or an uploaded image." compact />
      </div>
    )
  }

  if (failed) {
    return (
      <WidgetError
        message="The image could not be loaded."
        queryId={wantsQuery ? widget.query_id : undefined}
        kind="image"
        onRetry={() => setRetryEpoch(e => e + 1)}
        compact
      />
    )
  }

  return (
    <div className={`flex items-center h-full w-full p-2 ${align}`}>
      <img
        src={src}
        alt={alt}
        style={{ maxHeight: '100%', maxWidth: '100%', objectFit: fit }}
        onError={() => setFailedSrc(src)}
      />
    </div>
  )
}
