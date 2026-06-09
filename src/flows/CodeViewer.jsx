/**
 * CodeViewer.jsx — read-only syntax-highlighted Python viewer for generated
 * nubi.flows SDK code.
 *
 * Props:
 *   flowId      {string|null}  — flow id; if null the inline-spec path is used
 *   spec        {object|null}  — flow spec (used when flowId is null)
 *   onClose     {Function}     — called to dismiss the viewer
 *
 * Behaviour:
 *   - On mount (or when flowId / spec changes) calls POST /flows/{id}/codegen
 *     (or POST /flows/codegen for an inline spec) to fetch the generated source.
 *   - Degrades gracefully on 404 (shows a "not available" notice) or any other
 *     transport error (shows an error banner).
 *   - Copy-to-clipboard button in the header.
 */

import { useState, useEffect, useCallback } from 'react'
import Editor from '@monaco-editor/react'
import { X, Copy, Check, Loader2, AlertCircle, Code2 } from 'lucide-react'
import { post } from '../lib/api.js'

// ---------------------------------------------------------------------------
// Codegen fetcher
// ---------------------------------------------------------------------------

async function fetchCodegen(flowId, spec) {
  const BASE = '/flows'
  try {
    let data
    if (flowId) {
      data = await post(`${BASE}/${flowId}/codegen`, {})
    } else if (spec) {
      data = await post(`${BASE}/codegen`, { spec })
    } else {
      return { source: null, error: 'No flow id or spec provided.' }
    }
    return { source: data?.source ?? data?.code ?? null, error: null }
  } catch (err) {
    // 404 → feature not yet deployed; degrade gracefully
    if (err?.status === 404 || err?.message?.includes('404')) {
      return { source: null, error: null, unavailable: true }
    }
    return { source: null, error: err.message ?? 'Codegen request failed.' }
  }
}

// ---------------------------------------------------------------------------
// CodeViewer
// ---------------------------------------------------------------------------

export default function CodeViewer({ flowId, spec, onClose }) {
  // All async state packed into one object to avoid multiple synchronous setState
  // calls inside useEffect (which triggers the react-hooks/set-state-in-effect rule).
  const [state, setState] = useState({ loading: true, source: null, error: null, unavailable: false })
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let cancelled = false

    fetchCodegen(flowId, spec).then(result => {
      if (cancelled) return
      setState({ loading: false, source: result.source ?? null, error: result.error ?? null, unavailable: result.unavailable ?? false })
    })

    return () => { cancelled = true }
  }, [flowId, spec])

  const { loading, source, error, unavailable } = state

  const handleCopy = useCallback(() => {
    if (!source) return
    navigator.clipboard.writeText(source).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    })
  }, [source])

  return (
    <div className="flex flex-col h-full bg-surface border-l border-border overflow-hidden">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Code2 size={15} className="text-violet-500 shrink-0" />
          <div>
            <h3 className="text-sm font-semibold text-fg">Generated SDK code</h3>
            <p className="text-[11px] text-muted mt-0.5">nubi.flows Python SDK</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {source && (
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg border border-border bg-surface-2 hover:bg-surface text-fg transition-colors"
              title="Copy to clipboard"
            >
              {copied ? <Check size={12} className="text-green-500" /> : <Copy size={12} />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
          )}
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            title="Close code viewer"
          >
            <X size={15} />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-hidden">
        {loading && (
          <div className="flex items-center justify-center h-full gap-2 text-sm text-muted">
            <Loader2 size={16} className="animate-spin" />
            Generating code…
          </div>
        )}

        {!loading && error && (
          <div className="flex items-start gap-2 m-4 p-3 rounded-xl border border-rose-500/20 bg-rose-500/5 text-xs text-rose-600 dark:text-rose-400">
            <AlertCircle size={13} className="shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {!loading && unavailable && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6">
            <Code2 size={28} className="text-muted/40" />
            <div>
              <p className="text-sm font-medium text-fg">Codegen not available</p>
              <p className="text-xs text-muted mt-1">
                The codegen endpoint is not yet deployed on this backend.
                Save the flow and try again after upgrading.
              </p>
            </div>
          </div>
        )}

        {!loading && !error && !unavailable && source && (
          <Editor
            language="python"
            value={source}
            theme="vs-dark"
            options={{
              readOnly: true,
              fontSize: 12,
              minimap: { enabled: false },
              lineNumbers: 'on',
              scrollBeyondLastLine: false,
              padding: { top: 12, bottom: 12 },
              wordWrap: 'on',
              folding: true,
              renderLineHighlight: 'none',
              contextmenu: false,
              scrollbar: { vertical: 'auto', horizontal: 'auto' },
            }}
          />
        )}

        {!loading && !error && !unavailable && !source && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6">
            <Code2 size={28} className="text-muted/40" />
            <p className="text-sm text-muted">No source returned by the codegen endpoint.</p>
          </div>
        )}
      </div>
    </div>
  )
}
