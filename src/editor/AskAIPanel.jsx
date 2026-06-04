/**
 * AskAIPanel.jsx — "Ask AI" panel for the DashboardEditor (Wave EDITOR-2D).
 *
 * Props
 * -----
 * onApply  {(spec: DashboardSpec, mode: 'replace' | 'merge') => void}
 *          Called when the user clicks "Replace canvas" or "Merge widgets".
 *          The parent is responsible for updating its spec state.
 *
 * Behaviour
 * ---------
 * 1. User types a dashboard description in the textarea and clicks "Generate".
 * 2. POSTs to POST /ai/dashboard { question } via api.js post() helper.
 * 3. Receives { spec, html, grounding, provider, valid, issues }.
 * 4. Displays provider badge + grounding summary + validation issues.
 * 5. "Replace canvas" → onApply(spec, 'replace')
 *    "Merge widgets"  → onApply(spec, 'merge')   (parent handles dedup/offset)
 *
 * Note: With no LLM key configured the backend NullProvider returns a
 * deterministic grounded draft seeded from real query_ids. The panel
 * surfaces the provider name so users understand what they're getting.
 */

import { useState, useCallback } from 'react'
import { post } from '../lib/api.js'

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ProviderBadge({ provider }) {
  const isNull = !provider || provider === 'null' || provider === 'NullProvider'
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full border"
      style={isNull
        ? { background: 'color-mix(in srgb, #f59e0b 10%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 25%, transparent)' }
        : { background: 'color-mix(in srgb, #22c55e 10%, transparent)', color: '#16a34a', borderColor: 'color-mix(in srgb, #22c55e 25%, transparent)' }
      }
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: isNull ? '#f59e0b' : '#22c55e' }} />
      {isNull ? 'No LLM key — deterministic draft' : `AI: ${provider}`}
    </span>
  )
}

function GroundingSummary({ grounding }) {
  if (!grounding) return null
  // grounding may be a string or an object with .tables / .summary
  const text =
    typeof grounding === 'string'
      ? grounding
      : grounding.summary ?? JSON.stringify(grounding)

  return (
    <div className="text-xs text-fg bg-surface-2 border border-border rounded-lg p-2.5 leading-relaxed">
      <span className="font-semibold text-muted">Grounding: </span>
      {text}
    </div>
  )
}

function IssuesList({ issues }) {
  if (!issues || issues.length === 0) return null
  return (
    <div className="text-xs rounded-lg p-2.5 space-y-0.5 border"
      style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)' }}>
      <p className="font-semibold" style={{ color: '#d97706' }}>Validation issues</p>
      <ul className="list-disc list-inside space-y-0.5" style={{ color: '#d97706' }}>
        {issues.map((iss, i) => (
          <li key={i}>{typeof iss === 'string' ? iss : JSON.stringify(iss)}</li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AskAIPanel — main export
// ---------------------------------------------------------------------------

/**
 * @param {{ onApply: (spec: object, mode: 'replace'|'merge') => void }} props
 */
export default function AskAIPanel({ onApply }) {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null) // { spec, grounding, provider, valid, issues }

  const handleGenerate = useCallback(async () => {
    const q = question.trim()
    if (!q) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const data = await post('/ai/dashboard', { question: q })
      setResult(data)
    } catch (err) {
      setError(err.message ?? 'Generation failed.')
    } finally {
      setLoading(false)
    }
  }, [question])

  const handleKeyDown = useCallback(
    (e) => {
      // Ctrl+Enter or Cmd+Enter to submit
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        handleGenerate()
      }
    },
    [handleGenerate],
  )

  const handleReplace = useCallback(() => {
    if (result?.spec) onApply(result.spec, 'replace')
  }, [result, onApply])

  const handleMerge = useCallback(() => {
    if (result?.spec) onApply(result.spec, 'merge')
  }, [result, onApply])

  const hasResult = !!result?.spec

  return (
    <div className="flex flex-col h-full bg-surface">
      {/* ── Header ── */}
      <div className="px-4 pt-4 pb-3 border-b border-border shrink-0">
        <h3 className="text-sm font-semibold text-fg flex items-center gap-1.5">
          <span>✨</span> Ask AI
        </h3>
        <p className="text-xs text-muted mt-0.5 leading-snug">
          Describe your dashboard in plain English. With no LLM key configured,
          the backend returns a deterministic grounded draft.
        </p>
      </div>

      {/* ── Scrollable body ── */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {/* Textarea */}
        <div className="space-y-1">
          <textarea
            rows={4}
            placeholder={'Describe the dashboard you want…\n\ne.g. "Show me daily active users with a line chart, a KPI for total signups, and a table of recent events."'}
            className="w-full text-sm border border-border rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent placeholder:text-muted leading-relaxed bg-surface text-fg transition-colors"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
          />
          <p className="text-xs text-muted/60">Ctrl+Enter to generate</p>
        </div>

        {/* Generate button */}
        <button
          onClick={handleGenerate}
          disabled={loading || !question.trim()}
          className="w-full flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
        >
          {loading ? (
            <>
              <svg
                className="w-4 h-4 animate-spin"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v8H4z"
                />
              </svg>
              Generating…
            </>
          ) : (
            <>
              <span>✨</span>
              Generate
            </>
          )}
        </button>

        {/* Error */}
        {error && (
          <div className="text-xs rounded-lg px-3 py-2 border"
            style={{ background: 'color-mix(in srgb, #ef4444 8%, transparent)', color: '#ef4444', borderColor: 'color-mix(in srgb, #ef4444 25%, transparent)' }}>
            {error}
          </div>
        )}

        {/* Result card */}
        {hasResult && (
          <div className="space-y-2.5 bg-surface-2 border border-border rounded-xl p-3">
            {/* Provider + validity row */}
            <div className="flex items-center justify-between flex-wrap gap-1">
              <ProviderBadge provider={result.provider} />
              {result.valid === false ? (
                <span className="text-xs font-medium" style={{ color: '#d97706' }}>⚠ Spec has issues</span>
              ) : (
                <span className="text-xs font-medium" style={{ color: '#22c55e' }}>✓ Valid spec</span>
              )}
            </div>

            {/* Spec title preview */}
            {result.spec?.title && (
              <p className="text-xs text-muted">
                Title: <span className="font-semibold text-fg">{result.spec.title}</span>
              </p>
            )}

            {/* Widget count */}
            {Array.isArray(result.spec?.widgets) && (
              <p className="text-xs text-muted">
                {result.spec.widgets.length} widget{result.spec.widgets.length !== 1 ? 's' : ''}
              </p>
            )}

            {/* Grounding */}
            <GroundingSummary grounding={result.grounding} />

            {/* Validation issues */}
            <IssuesList issues={result.issues} />

            {/* Action buttons */}
            <div className="flex gap-2 pt-0.5">
              <button
                onClick={handleReplace}
                className="flex-1 px-3 py-1.5 text-xs font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
                title="Replace the entire canvas with the AI-generated spec"
              >
                Replace canvas
              </button>
              <button
                onClick={handleMerge}
                className="flex-1 px-3 py-1.5 text-xs font-medium bg-surface text-primary border border-border rounded-lg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
                title="Append the AI-generated widgets to the current canvas (offsets positions to avoid overlap)"
              >
                Merge widgets
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
