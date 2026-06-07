/**
 * ExportShareMenu.jsx — topbar dropdown for exporting and sharing a dashboard.
 *
 * Props
 * -----
 * board {string|null}  The board id (needed for server-side data export + share).
 * spec  {object}       The current DashboardSpec (used for filenames / fallback).
 *
 * Export
 *   - PNG / PDF: client-side capture of the rendered dashboard DOM via
 *     html2canvas + jspdf (both lazily imported).
 *   - CSV: per-widget data from GET /boards/:id/export.json, built client-side.
 * Share
 *   - POST /boards/:id/share → embed url + snippet + the RLS/auth model.
 *   - Surfaces that embedding uses a short-lived HOST-signed JWT whose claims
 *     carry RLS policies; row filtering happens server-side in the connector
 *     (the browser is untrusted). Nubi does not mint the token — the host signs it.
 */

import { useEffect, useRef, useState } from 'react'
import { Download, Share2, Image, FileText, Table, Link2, ShieldCheck, X, Copy, Check } from 'lucide-react'
import { get, post } from '../lib/api.js'

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function slugify(s) {
  return (s || 'dashboard').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'dashboard'
}

/** Find the rendered dashboard DOM node to capture. */
function dashboardNode() {
  return (
    document.querySelector('[data-dashboard-root]') ||
    document.querySelector('.spec-renderer') ||
    document.querySelector('[data-testid="editor-canvas"]') ||
    document.body
  )
}

/** RFC-4180-ish CSV cell. */
function csvCell(v) {
  if (v == null) return ''
  const s = String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

function widgetsToCsv(widgets) {
  const parts = []
  for (const w of widgets) {
    parts.push(`# widget: ${w.widget_id ?? w.query_id ?? ''}`)
    if (w.error) { parts.push(`# error: ${w.error}`); parts.push(''); continue }
    const cols = w.columns ?? []
    parts.push(cols.map(csvCell).join(','))
    for (const row of w.rows ?? []) {
      parts.push((Array.isArray(row) ? row : cols.map(c => row[c])).map(csvCell).join(','))
    }
    parts.push('')
  }
  return parts.join('\n')
}

function download(filename, content, mime) {
  const blob = content instanceof Blob ? content : new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// ---------------------------------------------------------------------------
// component
// ---------------------------------------------------------------------------

export default function ExportShareMenu({ board, spec }) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState('export')
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [share, setShare] = useState(null)
  const [copied, setCopied] = useState(null)
  const ref = useRef(null)

  const name = slugify(spec?.title)

  useEffect(() => {
    if (!open) return
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])

  async function capture() {
    const { default: html2canvas } = await import('html2canvas')
    return html2canvas(dashboardNode(), { backgroundColor: null, scale: 2, useCORS: true, logging: false })
  }

  async function exportPng() {
    setBusy('png'); setError(null)
    try {
      const canvas = await capture()
      canvas.toBlob((blob) => blob && download(`${name}.png`, blob, 'image/png'))
    } catch (e) { setError(e.message || 'PNG export failed.') } finally { setBusy(null) }
  }

  async function exportPdf() {
    setBusy('pdf'); setError(null)
    try {
      const canvas = await capture()
      const { jsPDF } = await import('jspdf')
      const img = canvas.toDataURL('image/png')
      const orientation = canvas.width >= canvas.height ? 'landscape' : 'portrait'
      const pdf = new jsPDF({ orientation, unit: 'pt', format: [canvas.width, canvas.height] })
      pdf.addImage(img, 'PNG', 0, 0, canvas.width, canvas.height)
      pdf.save(`${name}.pdf`)
    } catch (e) { setError(e.message || 'PDF export failed.') } finally { setBusy(null) }
  }

  async function exportCsv() {
    if (!board) { setError('Save the dashboard first to export data.'); return }
    setBusy('csv'); setError(null)
    try {
      const data = await get(`/boards/${board}/export.json`)
      const widgets = Array.isArray(data) ? data : (data?.widgets ?? [])
      download(`${name}.csv`, widgetsToCsv(widgets), 'text/csv')
    } catch (e) { setError(e.message || 'CSV export failed.') } finally { setBusy(null) }
  }

  async function loadShare() {
    if (!board) { setError('Save the dashboard first to share it.'); return }
    setBusy('share'); setError(null)
    try {
      setShare(await post(`/boards/${board}/share`))
    } catch {
      // Documented local fallback when the backend/board is unavailable.
      const origin = window.location.origin
      setShare({
        _local: true,
        embed_url: `${origin}/embed/${board ?? '<board-id>'}`,
        snippet: `<nubi-dashboard board="${board ?? '<board-id>'}" get-token="yourGetToken"></nubi-dashboard>`,
        rls: 'Embedding uses a short-lived host-signed JWT whose claims carry RLS policies; row filtering happens server-side in the connector.',
        mint: { token: null, max_ttl_minutes: 15 },
      })
    } finally { setBusy(null) }
  }

  function copy(key, text) {
    navigator.clipboard?.writeText(text).then(() => { setCopied(key); setTimeout(() => setCopied(null), 1500) })
  }

  const itemCls = 'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-fg rounded-lg hover:bg-surface-2 disabled:opacity-50 transition-colors text-left'

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => { setOpen(o => !o); if (!share && board) loadShare() }}
        className={`px-3 py-1.5 text-sm font-medium rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 flex items-center gap-1.5 ${
          open ? 'bg-surface-2 border-primary text-primary' : 'bg-surface text-fg border-border hover:bg-surface-2'
        }`}
        title="Export & share dashboard"
      >
        <Share2 size={14} /> Export &amp; Share
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 z-50 w-80 bg-surface border border-border rounded-xl shadow-xl overflow-hidden">
          <div className="flex border-b border-border">
            {['export', 'share'].map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`flex-1 py-2.5 text-xs font-medium capitalize transition-colors flex items-center justify-center gap-1.5 ${
                  tab === t ? 'text-primary border-b-2 border-primary bg-surface-2' : 'text-muted hover:text-fg hover:bg-surface-2'
                }`}>
                {t === 'export' ? <Download size={13} /> : <Link2 size={13} />} {t}
              </button>
            ))}
          </div>

          {error && <div className="px-3 py-2 text-xs" style={{ color: '#dc2626' }}>{error}</div>}

          {tab === 'export' ? (
            <div className="p-2 space-y-0.5">
              <button onClick={exportPng} disabled={busy === 'png'} className={itemCls}>
                <Image size={15} className="text-muted" /> {busy === 'png' ? 'Rendering…' : 'Export as PNG'}
              </button>
              <button onClick={exportPdf} disabled={busy === 'pdf'} className={itemCls}>
                <FileText size={15} className="text-muted" /> {busy === 'pdf' ? 'Rendering…' : 'Export as PDF'}
              </button>
              <button onClick={exportCsv} disabled={busy === 'csv'} className={itemCls}>
                <Table size={15} className="text-muted" /> {busy === 'csv' ? 'Fetching…' : 'Export data as CSV'}
              </button>
              <p className="px-3 pt-1.5 text-[10px] text-muted/60">PNG/PDF capture the live dashboard; CSV pulls each widget's data from the server.</p>
            </div>
          ) : (
            <div className="p-3 space-y-3 max-h-96 overflow-y-auto">
              {busy === 'share' && <p className="text-xs text-muted animate-pulse">Preparing embed…</p>}
              {share && (
                <>
                  <div className="space-y-1">
                    <label className="text-[10px] font-semibold text-muted uppercase tracking-wider">Embed link</label>
                    <div className="flex items-center gap-1.5">
                      <input readOnly value={share.embed_url ?? ''} className="flex-1 text-xs font-mono bg-surface-2 border border-border rounded-lg px-2 py-1.5 text-fg" />
                      <button onClick={() => copy('url', share.embed_url ?? '')} className="p-1.5 rounded-lg border border-border hover:border-primary text-muted hover:text-primary" title="Copy">
                        {copied === 'url' ? <Check size={13} /> : <Copy size={13} />}
                      </button>
                    </div>
                  </div>
                  {share.snippet && (
                    <div className="space-y-1">
                      <label className="text-[10px] font-semibold text-muted uppercase tracking-wider">Embed snippet</label>
                      <div className="relative">
                        <pre className="text-[11px] font-mono bg-surface-2 border border-border rounded-lg p-2 overflow-x-auto text-fg whitespace-pre-wrap">{share.snippet}</pre>
                        <button onClick={() => copy('snip', share.snippet)} className="absolute top-1.5 right-1.5 p-1 rounded border border-border bg-surface hover:text-primary text-muted">
                          {copied === 'snip' ? <Check size={12} /> : <Copy size={12} />}
                        </button>
                      </div>
                    </div>
                  )}
                  <div className="rounded-lg border border-border bg-surface-2/50 p-2.5 space-y-1.5">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-fg">
                      <ShieldCheck size={14} className="text-emerald-500" /> Row-level security
                    </div>
                    <p className="text-[11px] text-muted leading-relaxed">
                      Embedding uses a <strong>short-lived, host-signed JWT</strong> whose claims carry RLS policies.
                      Row filtering happens <strong>server-side in the connector</strong> — the browser is untrusted, so a
                      tenant can never see rows their token doesn't permit. Nubi does not mint the token; your app signs it
                      (RS256/ES256, verified via JWKS), max {share.mint?.max_ttl_minutes ?? 15} min.
                    </p>
                    {share._local && <p className="text-[10px] text-amber-600">Showing the documented snippet (offline/unsaved).</p>}
                  </div>
                </>
              )}
              {!busy && !share && <p className="text-xs text-muted">Save the dashboard to generate an embed link.</p>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
