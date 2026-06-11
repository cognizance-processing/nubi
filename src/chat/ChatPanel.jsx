/**
 * ChatPanel — live, tool-streaming AI assistant for Nubi.
 *
 * Mounts in the right-hand slide-in panel of AppShell (full-screen on mobile).
 *
 * Streaming model (Claude-Code-style): on send we open an SSE stream
 * (streamChatMessage) and render events live as they arrive —
 *   - status   → a "● doing X…" line under the assistant avatar
 *   - tool_start / tool_result → a ToolBlock that animates running → result,
 *     with specialised rendering per tool (SQL code, result table, dashboard)
 *   - text     → the reply streams in token-by-token with a blinking caret
 *
 * Props:
 *   onClose {Function} — called when the user closes the panel
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import {
  X, Sparkles, Send, Square, ChevronDown, AlertCircle,
  Check, Loader2, Bot, Pin, ExternalLink,
} from 'lucide-react'
import MarkdownRenderer from '../components/MarkdownRenderer.jsx'
import ToolCard from './ToolCard.jsx'
import { toolLabel } from './toolMeta.js'
import { streamChatMessage, pinAnswer } from './chatApi.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Model ids MUST match the backend allowlist (POST /api/v1/ai/chat → `model`).
// An unknown id returns 400 model_not_allowed, surfaced in the chat error UI.
const MODELS = [
  { id: 'default',           label: 'Nubi Default'    },
  { id: 'claude-opus-4-8',   label: 'Claude Opus 4.8' },
  { id: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6' },
  { id: 'claude-haiku-4-5',  label: 'Claude Haiku 4.5' },
  { id: 'gpt-4o',            label: 'GPT-4o'          },
  { id: 'gpt-4o-mini',       label: 'GPT-4o mini'     },
  { id: 'gemini-1.5-pro',    label: 'Gemini 1.5 Pro'  },
  { id: 'gemini-1.5-flash',  label: 'Gemini 1.5 Flash' },
]

// Persist the model choice like other UI prefs (e.g. nubi-theme, nubi-sidebar-*).
const MODEL_STORAGE_KEY = 'nubi-chat-model'

function loadStoredModel() {
  try {
    const stored = localStorage.getItem(MODEL_STORAGE_KEY)
    if (stored && MODELS.some(m => m.id === stored)) return stored
  } catch { /* localStorage unavailable (private mode / SSR) — ignore */ }
  return MODELS[0].id
}

const SUGGESTIONS = [
  'Build a sales dashboard',
  'Show revenue by region',
  'Which queries run slowest?',
  'Summarise connected data sources',
]

// ---------------------------------------------------------------------------
// Ask → Pin: detect a pinnable tool result and build the /ai/pin payload
// ---------------------------------------------------------------------------

// A result is pinnable when it carries a query_id (a runnable query) or a
// generated spec/widget that the dashboard can render. Plain text turns and
// errored results are never pinnable.
function isPinnable(tool, result) {
  if (!result || typeof result !== 'object' || result.error) return false
  return Boolean(
    result.query_id || result.metric_id ||
    result.spec || result.widget_id ||
    (tool === 'run_query' && result.query_id),
  )
}

// Map a result's chart type → a viz descriptor for /ai/pin.
function vizFromResult(result) {
  const chart = result.chart_type || result.viz?.chart_type || result.spec?.chart_type
  if (chart) {
    return { type: 'chart', chart_type: chart, ...(result.encoding ? { encoding: result.encoding } : {}) }
  }
  // A single-value/metric result reads best as a KPI; otherwise a table.
  if (result.metric_id && !result.query_id) return { type: 'kpi' }
  return { type: 'table' }
}

// Build the POST /ai/pin body from a (pinnable) tool result.
function buildPinPayload(tool, result) {
  const source = result.query_id
    ? { query_id: result.query_id }
    : result.metric_id
      ? { metric_id: result.metric_id }
      : {}
  const title =
    result.title || result.name ||
    (tool === 'run_query' ? 'Query result' : toolLabel(tool))
  return { title, source, viz: vizFromResult(result) }
}

function PinButton({ tool, result }) {
  const [state, setState]   = useState('idle') // idle | pinning | pinned | error
  const [boardId, setBoard] = useState(null)
  const [error, setError]   = useState(null)

  const onPin = useCallback(async () => {
    setState('pinning'); setError(null)
    try {
      const res = await pinAnswer(buildPinPayload(tool, result))
      setBoard(res?.board_id ?? null)
      setState('pinned')
    } catch (err) {
      // Surface a structured 400 (validation errors) inline.
      const detail =
        err?.payload?.error?.message ??
        (Array.isArray(err?.payload?.detail)
          ? err.payload.detail.map(d => d?.msg ?? String(d)).join(', ')
          : err?.payload?.detail) ??
        err?.message ?? 'Could not pin.'
      setError(detail)
      setState('error')
    }
  }, [tool, result])

  if (state === 'pinned') {
    const href = boardId ? `/d/${boardId}` : '/dashboards'
    return (
      <a
        href={href}
        className="inline-flex items-center gap-1.5 text-[11px] font-medium text-brand-teal hover:underline focus:outline-none focus:ring-2 focus:ring-ring rounded-md px-1.5 py-1"
      >
        <Check size={12} className="shrink-0" />
        Pinned — open dashboard
        <ExternalLink size={11} className="shrink-0 opacity-70" />
      </a>
    )
  }

  return (
    <div className="space-y-1">
      <button
        onClick={onPin}
        disabled={state === 'pinning'}
        className="inline-flex items-center gap-1.5 text-[11px] font-medium text-primary bg-primary/10 hover:bg-primary/15 border border-primary/20 rounded-lg px-2 py-1 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
      >
        {state === 'pinning'
          ? <Loader2 size={12} className="animate-spin shrink-0" />
          : <Pin size={12} className="shrink-0" />}
        {state === 'pinning' ? 'Pinning…' : 'Pin to dashboard'}
      </button>
      {state === 'error' && error && (
        <p className="text-[10px] text-red-500 flex items-start gap-1">
          <AlertCircle size={11} className="shrink-0 mt-0.5" />{error}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ToolBlock — adapts the global panel's action shape to the shared ToolCard,
// adding a Pin-to-dashboard footer for pinnable results.
// ---------------------------------------------------------------------------

function ToolBlock({ action }) {
  const pinnable = isPinnable(action.tool, action.result)
  return (
    <ToolCard
      action={{
        id: action.id,
        tool: action.tool,
        args: action.arguments,
        result: action.result,
        status: action.status,
      }}
      footer={pinnable ? (
        <div className="mt-2">
          <PinButton tool={action.tool} result={action.result} />
        </div>
      ) : null}
    />
  )
}

// ---------------------------------------------------------------------------
// Message bubble
// ---------------------------------------------------------------------------

function StatusLine({ text }) {
  return (
    <div className="flex items-center gap-2 text-[12px] text-muted">
      <span className="block w-1.5 h-1.5 rounded-full bg-brand-teal" style={{ animation: 'nubiChatPulse 1s ease-in-out infinite' }} />
      <span>{text}</span>
    </div>
  )
}

function MessageBubble({ message }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end px-3 py-1">
        <div className="max-w-[82%] px-3.5 py-2.5 rounded-2xl rounded-br-sm bg-primary text-primary-fg text-[13px] leading-relaxed font-sans shadow-sm whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    )
  }

  // assistant
  const hasTools = message.actions && message.actions.length > 0
  return (
    <div className="flex items-start gap-2.5 px-3 py-1 max-w-full">
      <div
        className="flex items-center justify-center w-6 h-6 rounded-full shrink-0 mt-0.5"
        style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
      >
        <Bot size={12} className="text-white" />
      </div>

      <div className="flex-1 min-w-0 space-y-2">
        {hasTools && (
          <div className="space-y-1.5">
            {message.actions.map((a) => <ToolBlock key={a.id} action={a} />)}
          </div>
        )}

        {message.status && !message.content && <StatusLine text={message.status} />}

        {message.content && (
          <div className="prose-chat text-[13px] leading-relaxed text-fg bg-surface-2 border border-border px-3.5 py-2.5 rounded-2xl rounded-bl-sm overflow-hidden">
            <MarkdownRenderer content={message.content} />
            {message.streaming && (
              <span className="inline-block w-[7px] h-[14px] -mb-0.5 ml-0.5 bg-brand-teal rounded-[1px] align-middle"
                style={{ animation: 'nubiChatCaret 1s step-end infinite' }} />
            )}
          </div>
        )}

        {message.error && (
          <div className="flex items-start gap-2">
            <AlertCircle size={14} className="text-red-500 shrink-0 mt-0.5" />
            <p className="text-[13px] text-red-500 leading-relaxed">{message.error}</p>
          </div>
        )}
      </div>
    </div>
  )
}

function SuggestionChip({ text, onClick, disabled }) {
  return (
    <button
      onClick={() => onClick(text)}
      disabled={disabled}
      className="px-3 py-1.5 rounded-full border border-border bg-surface-2 text-[12px] text-muted font-sans hover:border-primary hover:text-primary hover:bg-primary/5 disabled:opacity-40 disabled:cursor-not-allowed transition-all focus:outline-none focus:ring-2 focus:ring-ring text-left"
    >
      {text}
    </button>
  )
}

// ---------------------------------------------------------------------------
// ChatPanel
// ---------------------------------------------------------------------------

export function ChatPanel({ onClose }) {
  const [messages, setMessages]   = useState([])
  const [draft, setDraft]         = useState('')
  const [loading, setLoading]     = useState(false)
  const [selectedModel, setModel] = useState(loadStoredModel)

  const listRef     = useRef(null)
  const textareaRef = useRef(null)
  const abortRef    = useRef(null)

  useEffect(() => {
    const el = listRef.current
    if (!el) return
    requestAnimationFrame(() => { el.scrollTop = el.scrollHeight })
  }, [messages, loading])

  useEffect(() => { textareaRef.current?.focus() }, [])
  // Abort any in-flight stream on unmount
  useEffect(() => () => abortRef.current?.abort(), [])

  // Persist the model choice so it survives panel re-opens / reloads.
  useEffect(() => {
    try { localStorage.setItem(MODEL_STORAGE_KEY, selectedModel) }
    catch { /* localStorage unavailable — ignore */ }
  }, [selectedModel])

  // Update the most recent assistant message immutably.
  const updateLast = useCallback((fn) => {
    setMessages(prev => {
      const copy = prev.slice()
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].role === 'assistant') { copy[i] = fn({ ...copy[i] }); break }
      }
      return copy
    })
  }, [])

  const sendMessage = useCallback(async (text) => {
    const trimmed = (text ?? draft).trim()
    if (!trimmed || loading) return

    const userMsg = { role: 'user', content: trimmed }
    const history = [...messages, userMsg]
    setMessages([...history, {
      role: 'assistant', content: '', actions: [], streaming: true, status: 'Thinking…', error: null,
    }])
    setDraft('')
    setLoading(true)

    const controller = new AbortController()
    abortRef.current = controller

    const handleEvent = (ev) => {
      switch (ev.type) {
        case 'status':
          updateLast(m => ({ ...m, status: ev.text }))
          break
        case 'tool_start':
          updateLast(m => ({
            ...m, status: null,
            actions: [...(m.actions || []), {
              id: ev.id, tool: ev.tool, arguments: ev.arguments, status: 'running', result: null, ok: null,
            }],
          }))
          break
        case 'tool_result':
          updateLast(m => ({
            ...m,
            actions: (m.actions || []).map(a =>
              a.id === ev.id ? { ...a, status: ev.ok ? 'done' : 'error', result: ev.result, ok: ev.ok } : a),
          }))
          break
        case 'text':
          updateLast(m => ({ ...m, status: null, content: (m.content || '') + (ev.delta || '') }))
          break
        case 'done':
          updateLast(m => ({
            ...m, streaming: false, status: null,
            content: m.content || ev.reply || '',
            actions: m.actions && m.actions.length ? m.actions : (ev.actions || []),
          }))
          break
        case 'error':
          updateLast(m => ({ ...m, streaming: false, status: null, error: ev.message || 'Something went wrong.' }))
          break
        default:
          break
      }
    }

    try {
      await streamChatMessage({
        messages: history.map(m => ({ role: m.role === 'error' ? 'user' : m.role, content: m.content })),
        model: selectedModel === 'default' ? undefined : selectedModel,
        onEvent: handleEvent,
        signal: controller.signal,
      })
      // Ensure the streaming flag is cleared if the stream closed without a done event.
      updateLast(m => (m.streaming ? { ...m, streaming: false, status: null } : m))
    } catch (err) {
      if (err?.name === 'AbortError') {
        updateLast(m => ({ ...m, streaming: false, status: null }))
      } else {
        updateLast(m => ({ ...m, streaming: false, status: null, error: err?.message ?? 'Something went wrong. Please try again.' }))
      }
    } finally {
      abortRef.current = null
      setLoading(false)
      requestAnimationFrame(() => textareaRef.current?.focus())
    }
  }, [draft, loading, messages, selectedModel, updateLast])

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }, [sendMessage])

  const isEmpty = messages.length === 0

  return (
      <div className="flex flex-col h-full bg-surface overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0"
              style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}>
              <Sparkles size={13} className="text-white" />
            </div>
            <span className="font-display font-semibold text-sm text-fg leading-none">Nubi AI</span>
          </div>

          <div className="flex items-center gap-1.5">
            <div className="relative">
              <select
                value={selectedModel}
                onChange={e => setModel(e.target.value)}
                disabled={loading}
                aria-label="Select AI model"
                className="appearance-none pl-2.5 pr-6 py-1 text-[11px] font-sans font-medium text-muted bg-surface-2 border border-border rounded-lg hover:border-primary hover:text-fg disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
              >
                {MODELS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
              <ChevronDown size={11} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            </div>
            <button
              onClick={onClose}
              aria-label="Close chat panel"
              className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Message list */}
        <div ref={listRef} className="flex-1 overflow-y-auto py-3 scroll-smooth" role="log" aria-live="polite" aria-label="Chat messages">
          {isEmpty && (
            <div className="flex flex-col items-center justify-center h-full px-6 text-center gap-5 pb-4">
              <div className="flex items-center justify-center w-14 h-14 rounded-2xl shadow-lg"
                style={{ background: 'linear-gradient(135deg, #1b2363 0%, #2456a6 50%, #17b3a3 100%)' }}>
                <Sparkles size={24} className="text-white" />
              </div>
              <div>
                <p className="font-display font-semibold text-fg text-[14px] mb-1">Ask Nubi anything</p>
                <p className="text-[12px] text-muted leading-relaxed max-w-[210px]">
                  I build dashboards, write &amp; run SQL, and explore your data — watch each tool run live.
                </p>
              </div>
              <div className="flex flex-wrap gap-2 justify-center">
                {SUGGESTIONS.map(s => <SuggestionChip key={s} text={s} onClick={sendMessage} disabled={loading} />)}
              </div>
            </div>
          )}

          {messages.map((msg, i) => <MessageBubble key={i} message={msg} />)}
        </div>

        {/* Composer */}
        <div className="shrink-0 px-3 py-3 border-t border-border bg-surface">
          <div className={`flex items-end gap-2 bg-surface-2 border rounded-xl px-3 py-2 transition-colors ${loading ? 'border-border' : 'border-border focus-within:border-primary focus-within:ring-1 focus-within:ring-ring'}`}>
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={loading ? 'Streaming…' : 'Ask anything… (Enter to send)'}
              rows={1}
              aria-label="Chat input"
              className="flex-1 resize-none bg-transparent text-[13px] text-fg font-sans leading-relaxed placeholder:text-muted focus:outline-none min-h-[22px] max-h-32 py-0.5"
              style={{ overflowY: draft.split('\n').length > 4 ? 'auto' : 'hidden' }}
              onInput={e => { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 128) + 'px' }}
            />
            {loading ? (
              <button
                onClick={stopStreaming}
                aria-label="Stop"
                className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0 bg-surface border border-border text-fg hover:bg-surface-2 active:scale-95 transition-all focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <Square size={12} className="fill-current" />
              </button>
            ) : (
              <button
                onClick={() => sendMessage()}
                disabled={!draft.trim()}
                aria-label="Send message"
                className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0 bg-primary text-primary-fg hover:opacity-90 active:scale-95 disabled:opacity-35 disabled:cursor-not-allowed transition-all focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <Send size={13} />
              </button>
            )}
          </div>
          <p className="text-[10px] text-muted/60 text-center mt-1.5 leading-none">
            {loading ? 'Streaming — click ◼ to stop' : 'Shift+Enter for newline'}
          </p>
        </div>
      </div>
  )
}

export default ChatPanel
