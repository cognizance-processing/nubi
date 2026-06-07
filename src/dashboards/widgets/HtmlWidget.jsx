/**
 * HtmlWidget.jsx — renders a widget's custom HTML template (widget.html).
 *
 * When a widget carries a `html` template string it is rendered HERE instead of
 * the default chart/kpi/table body. The template may reference the widget's
 * query result via {{tokens}} (see widgetHtml.js). Data is fetched with the same
 * runArrowQueryById + useResolvedParams path as the other data widgets, so
 * param binding and re-query-on-variable-change behave identically.
 *
 * SECURITY: the interpolated output is sanitized by renderWidgetHtml() before it
 * reaches dangerouslySetInnerHTML — scripts / event-handlers / unsafe URLs are
 * stripped (sanitize.js trust boundary).
 */

import { useState, useEffect, useMemo } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { useResolvedParams } from '../VariableStore.jsx'
import { renderWidgetHtml } from '../widgetHtml.js'

export default function HtmlWidget({ widget }) {
  const { query_id, html, props: wProps = {}, encoding = {}, params: widgetParams } = widget
  const resolvedParams = useResolvedParams(widgetParams)

  const [table, setTable] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!query_id) return
    let cancelled = false
    async function fetchData() {
      try {
        const hasParams = Object.keys(resolvedParams).length > 0
        const { table: t } = await runArrowQueryById(
          query_id,
          hasParams ? { namedParams: resolvedParams } : undefined,
        )
        if (!cancelled) setTable(t)
      } catch (err) {
        if (!cancelled) setError(err.message ?? 'Query failed.')
      }
    }
    fetchData()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query_id, JSON.stringify(resolvedParams)])

  // Headline value for {{value}} — the configured KPI value column, first row.
  const extra = useMemo(() => {
    if (!table || !encoding.value) return {}
    const child = table.getChild(encoding.value)
    return { value: child && table.numRows > 0 ? child.get(0) : undefined }
  }, [table, encoding.value])

  const safeHtml = useMemo(
    () => renderWidgetHtml(html, { table, props: wProps, extra }),
    [html, table, wProps, extra],
  )

  if (error) {
    return (
      <div className="px-3 py-1.5 text-xs h-full overflow-auto"
        style={{ color: '#d97706' }}>
        {error}
      </div>
    )
  }

  return (
    <div
      className="h-full w-full overflow-auto"
      // eslint-disable-next-line react/no-danger
      dangerouslySetInnerHTML={{ __html: safeHtml }}
    />
  )
}
