/**
 * DashboardView.jsx — React component that renders a sanitized dashboard HTML doc.
 *
 * Usage:
 *   <DashboardView html="<div><nubi-kpi query-id='demo_all' value-col='n'/></div>" />
 *
 * The component:
 *   1. Holds a ref to a container <div>.
 *   2. On mount (and whenever `html` changes), calls renderDashboardDoc() which
 *      sanitizes the HTML and sets container.innerHTML, upgrading Nubi widgets.
 *   3. Calls the returned cleanup function on unmount / before re-render so
 *      widgets can disconnect cleanly.
 *
 * SECURITY: raw `html` is never set as innerHTML directly — it always passes
 * through renderDashboardDoc → sanitizeDashboardHtml (DOMPurify) first.
 *
 * Props:
 *   html     {string}  Raw dashboard HTML (LLM-authored / from boards resource).
 *   className {string} Optional extra CSS classes for the outer wrapper.
 */

import { useEffect, useRef } from 'react'
import { renderDashboardDoc } from './renderDashboardDoc.js'
import { getAccessToken } from '../lib/api.js'

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? 'http://localhost:8000'

/**
 * @param {{ html: string, className?: string }} props
 */
export default function DashboardView({ html, className = '' }) {
  const containerRef = useRef(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const cleanup = renderDashboardDoc(container, html ?? '', {
      backend: BACKEND_URL,
      getToken: () => getAccessToken(),
    })

    return cleanup
  }, [html])

  return (
    <div
      ref={containerRef}
      className={`nubi-dashboard-view ${className}`}
    />
  )
}
