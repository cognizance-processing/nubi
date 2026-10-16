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

import { useState, useRef, useEffect, useCallback, type KeyboardEvent } from 'react'
import {
  X, Send, Square, Clock,
  Check, Pin, ExternalLink,
} from 'lucide-react'
import Button from '../components/ui/Button.jsx'
import MarkdownRenderer from '../components/MarkdownRenderer.jsx'
import '../components/app/appPatterns.css'
import ToolCard from './ToolCard.jsx'
import { toolLabel } from './toolMeta.js'
import { streamChatMessage, sendChatMessageAsync, pinAnswer } from './chatApi.js'
import { useUi } from '../contexts/UiContext.jsx'
import ModelPicker, { MODEL_DEFAULT_VALUE } from '../components/ai/ModelPicker.jsx'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StreamAction {
  id: string
  tool: string
  arguments?: Record<string, any>
  status: 'running' | 'done' | 'error'
  result?: any
  ok?: boolean | null
}

interface ChatUiMessage {
  role: 'user' | 'assistant'
  content: string
  actions?: StreamAction[]
  streaming?: boolean
  status?: string | null
  error?: string | null
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Persist the model choice like other UI prefs (e.g. nubi-theme, nubi-sidebar-*).
// The model list itself is fetched live from GET /ai/providers (ModelPicker) —
// it self-corrects back to the default sentinel if a stored id is no longer
// valid (e.g. the operator disabled that provider).
const MODEL_STORAGE_KEY = 'nubi-chat-model'

function loadStoredModel(): string {
  try {
    const stored = localStorage.getItem(MODEL_STORAGE_KEY)
    if (stored) return stored
  } catch { /* localStorage unavailable (private mode / SSR) — ignore */ }
  return MODEL_DEFAULT_VALUE
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
function isPinnable(tool: string, result: any): boolean {
  if (!result || typeof result !== 'object' || result.error) return false
  return Boolean(
    result.query_id || result.metric_id ||
    result.spec || result.widget_id ||
    (tool === 'run_query' && result.query_id),
  )
}

// Map a result's chart type → a viz descriptor for /ai/pin.
function vizFromResult(result: Record<string, any>) {
  const chart = result.chart_type || result.viz?.chart_type || result.spec?.chart_type
  if (chart) {
    return { type: 'chart', chart_type: chart, ...(result.encoding ? { encoding: result.encoding } : {}) }
  }
  // A single-value/metric result reads best as a KPI; otherwise a table.
  if (result.metric_id && !result.query_id) return { type: 'kpi' }
  return { type: 'table' }
}

// Build the POST /ai/pin body from a (pinnable) tool result.
function buildPinPayload(tool: string, result: Record<string, any>) {
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

function PinButton({ tool, result }: { tool: string; result: Record<string, any> }) {
  const [state, setState]   = useState<'idle' | 'pinning' | 'pinned' | 'error'>('idle')
  const [boardId, setBoard] = useState<string | null>(null)
  const [error, setError]   = useState<string | null>(null)

  const onPin = useCallback(async () => {
    setState('pinning'); setError(null)
    try {
      const res = await pinAnswer(buildPinPayload(tool, result) as any)
      setBoard(res?.board_id ?? null)
      setState('pinned')
    } catch (err: any) {
      // Surface a structured 400 (validation errors) inline.
      const detail =
        err?.payload?.error?.message ??
        (Array.isArray(err?.payload?.detail)
          ? err.payload.detail.map((d: any) => d?.msg ?? String(d)).join(', ')
          : err?.payload?.detail) ??
        err?.message ?? 'Could not pin.'
      setError(detail)
      setState('error')
    }
  }, [tool, result])

  if (state === 'pinned') {
    const href = boardId ? `/d/${boardId}` : '/dashboards'
    return (
      <a href={href} className="inline-flex items-center gap-1.5 text-xs font-medium text-accent underline underline-offset-4">
        <Check size={12} className="shrink-0" aria-hidden="true" />
        Pinned. Open dashboard
        <ExternalLink size={11} className="shrink-0 opacity-70" aria-hidden="true" />
      </a>
    )
  }

  return (
    <div className="space-y-1">
      <Button variant="secondary" size="xs" onClick={onPin} loading={state === 'pinning'}>
        {state !== 'pinning' && <Pin size={12} aria-hidden="true" />}
        {state === 'pinning' ? 'Pinning' : 'Pin to dashboard'}
      </Button>
      {state === 'error' && error && (
        <p className="nubi-field-error m-0" role="alert">{error}</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ToolBlock — adapts the global panel's action shape to the shared ToolCard,
// adding a Pin-to-dashboard footer for pinnable results.
// ---------------------------------------------------------------------------

function ToolBlock({ action }: { action: StreamAction }) {
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
// Transcript — a ruled agent log: one turn per block, a mono role label, tool
// calls as mono rows, then prose. No bubbles, no avatars.
// ---------------------------------------------------------------------------

function StatusLine({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 font-mono text-xs text-muted" role="status">
      <span className="nubi-dot nubi-dot-live animate-pulse" aria-hidden="true" />
      <span>{text}</span>
    </div>
  )
}

function Turn({ label, children, tone = 'assistant' }: { label: string; children: React.ReactNode; tone?: 'user' | 'assistant' }) {
  return (
    <article className="border-b border-border px-4 py-3" data-role={tone}>
      <p className="t-label m-0 mb-1.5" style={tone === 'assistant' ? { color: 'var(--text)' } : undefined}>{label}</p>
      {children}
    </article>
  )
}

function MessageBubble({ message }: { message: ChatUiMessage }) {
  if (message.role === 'user') {
    return (
      <Turn label="You" tone="user">
        <p className="m-0 whitespace-pre-wrap text-sm leading-relaxed text-fg">{message.content}</p>
      </Turn>
    )
  }

  // assistant
  const hasTools = message.actions && message.actions.length > 0
  return (
    <Turn label="Nubi">
      <div className="min-w-0 space-y-2">
        {hasTools && (
          <div className="border-t border-border">
            {message.actions.map((a) => <ToolBlock key={a.id} action={a} />)}
          </div>
        )}

        {message.status && !message.content && <StatusLine text={message.status} />}

        {message.content && (
          <div className="prose-chat overflow-hidden text-sm leading-relaxed text-fg">
            <MarkdownRenderer content={message.content} />
            {message.streaming && (
              <span
                className="ml-0.5 inline-block h-[14px] w-[7px] -mb-0.5 align-middle"
                style={{ background: 'var(--ink)', animation: 'nubiChatCaret 1s step-end infinite' }}
                aria-hidden="true"
              />
            )}
          </div>
        )}

        {message.error && (
          <div className="nubi-notice nubi-notice-error text-xs" role="alert">
            <span>{message.error}</span>
          </div>
        )}
      </div>
    </Turn>
  )
}

/** A starter prompt as a ruled row (not a pill). */
function SuggestionRow({ text, index, onClick, disabled }: {
  text: string
  index: number
  onClick: (text: string) => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={() => onClick(text)}
      disabled={disabled}
      className="nubi-focus-inset flex w-full items-baseline gap-3 border-b border-border px-4 py-2 text-left text-sm text-fg transition-colors hover:bg-frame-hover disabled:cursor-not-allowed disabled:opacity-45"
    >
      <span className="ap-meta shrink-0" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
      <span>{text}</span>
    </button>
  )
}

// ---------------------------------------------------------------------------
// ChatPanel
// ---------------------------------------------------------------------------

export function ChatPanel({ onClose }: { onClose: () => void }) {
  const [messages, setMessages]   = useState<ChatUiMessage[]>([])
  const [draft, setDraft]         = useState('')
  const [loading, setLoading]     = useState(false)
  const [queuing, setQueuing]     = useState(false)
  const [selectedModel, setModel] = useState(loadStoredModel)
  const { chatDraft, setChatDraft } = useUi()

  const listRef     = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const abortRef    = useRef<AbortController | null>(null)

  useEffect(() => {
    const el = listRef.current
    if (!el) return
    requestAnimationFrame(() => { el.scrollTop = el.scrollHeight })
  }, [messages, loading])

  useEffect(() => { textareaRef.current?.focus() }, [])
  // Another surface (Spotlight "Ask AI about this") handed us text: seed the
  // composer with it, focus, then clear the hand-off so it is not re-applied.
  useEffect(() => {
    if (!chatDraft) return
    setDraft(chatDraft)
    setChatDraft('')
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (!el) return
      el.focus()
      el.setSelectionRange(el.value.length, el.value.length)
    })
  }, [chatDraft, setChatDraft])
  // Abort any in-flight stream on unmount
  useEffect(() => () => abortRef.current?.abort(), [])

  // Persist the model choice so it survives panel re-opens / reloads.
  useEffect(() => {
    try { localStorage.setItem(MODEL_STORAGE_KEY, selectedModel) }
    catch { /* localStorage unavailable — ignore */ }
  }, [selectedModel])

  // Update the most recent assistant message immutably.
  const updateLast = useCallback((fn: (m: ChatUiMessage) => ChatUiMessage) => {
    setMessages(prev => {
      const copy = prev.slice()
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].role === 'assistant') { copy[i] = fn({ ...copy[i] }); break }
      }
      return copy
    })
  }, [])

  const sendMessage = useCallback(async (text?: string) => {
    const trimmed = (text ?? draft).trim()
    if (!trimmed || loading) return

    const userMsg: ChatUiMessage = { role: 'user', content: trimmed }
    const history = [...messages, userMsg]
    setMessages([...history, {
      role: 'assistant', content: '', actions: [], streaming: true, status: 'Thinking…', error: null,
    }])
    setDraft('')
    setLoading(true)

    const controller = new AbortController()
    abortRef.current = controller

    const handleEvent = (ev: any) => {
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
        messages: history.map(m => ({ role: m.role, content: m.content })),
        model: selectedModel === MODEL_DEFAULT_VALUE ? undefined : selectedModel,
        onEvent: handleEvent,
        signal: controller.signal,
      })
      // Ensure the streaming flag is cleared if the stream closed without a done event.
      updateLast(m => (m.streaming ? { ...m, streaming: false, status: null } : m))
    } catch (err: any) {
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

  // Long-running builds (e.g. "read this spec and build the dashboard") don't
  // need you watching the stream — queue the same agent turn server-side and
  // move on. It finishes in the notification bell, not this panel.
  const sendMessageBackground = useCallback(async () => {
    const trimmed = draft.trim()
    if (!trimmed || loading || queuing) return

    const userMsg: ChatUiMessage = { role: 'user', content: trimmed }
    const history = [...messages, userMsg]
    setMessages([...history, {
      role: 'assistant',
      content: "Building this in the background — I'll notify you (bell, top right) when it's ready. Keep going in the meantime.",
      actions: [], streaming: false, status: null, error: null,
    }])
    setDraft('')
    setQueuing(true)
    try {
      await sendChatMessageAsync({
        messages: history.map(m => ({ role: m.role, content: m.content })),
        model: selectedModel === MODEL_DEFAULT_VALUE ? undefined : selectedModel,
      })
    } catch (err: any) {
      updateLast(m => ({ ...m, error: err?.message ?? 'Could not queue the background build.' }))
    } finally {
      setQueuing(false)
      requestAnimationFrame(() => textareaRef.current?.focus())
    }
  }, [draft, loading, queuing, messages, selectedModel, updateLast])

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const handleKeyDown = useCallback((e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }, [sendMessage])

  const isEmpty = messages.length === 0

  return (
      <div className="flex h-full flex-col overflow-hidden bg-surface">
        {/* Header: a 2px ink rule opens the panel */}
        <div className="flex h-11 shrink-0 items-center justify-between border-b border-border pl-4 pr-2" style={{ borderTop: '2px solid var(--ink)' }}>
          <span className="t-label" style={{ color: 'var(--text)' }}>Nubi AI</span>

          <div className="flex items-center gap-1.5">
            <ModelPicker value={selectedModel} onChange={setModel} disabled={loading} />
            <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close chat panel">
              <X size={14} aria-hidden="true" />
            </Button>
          </div>
        </div>

        {/* Transcript */}
        <div ref={listRef} className="flex-1 overflow-y-auto" role="log" aria-live="polite" aria-label="Chat messages">
          {isEmpty && (
            <div className="pb-2">
              <div className="px-4 pb-3 pt-5">
                <p className="m-0 text-[15px] font-semibold text-fg">Ask about your data</p>
                <p className="mb-0 mt-1 max-w-[260px] text-[13px] leading-relaxed text-muted">
                  I build dashboards, write and run SQL, and explore your connectors. Each tool call shows up as a line you can open.
                </p>
              </div>
              <div className="border-t border-border">
                {SUGGESTIONS.map((s, i) => <SuggestionRow key={s} text={s} index={i} onClick={sendMessage} disabled={loading} />)}
              </div>
            </div>
          )}

          {messages.map((msg, i) => <MessageBubble key={i} message={msg} />)}
        </div>

        {/* Composer */}
        <div className="shrink-0 border-t border-frame-line bg-surface px-3 pb-2 pt-3">
          <div className="flex items-end gap-2">
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={loading ? 'Streaming' : 'Ask anything about your data'}
              rows={1}
              aria-label="Chat input"
              className="nubi-input min-h-[36px] max-h-32 flex-1 resize-none py-[7px] text-[13px] leading-relaxed"
              style={{ overflowY: draft.split('\n').length > 4 ? 'auto' : 'hidden' }}
              onInput={e => { e.currentTarget.style.height = 'auto'; e.currentTarget.style.height = Math.min(e.currentTarget.scrollHeight, 128) + 'px' }}
            />
            {loading ? (
              <Button variant="secondary" size="icon" onClick={stopStreaming} aria-label="Stop" title="Stop">
                <Square size={12} className="fill-current" aria-hidden="true" />
              </Button>
            ) : (
              <>
                <Button
                  variant="secondary"
                  size="icon"
                  onClick={sendMessageBackground}
                  disabled={!draft.trim()}
                  loading={queuing}
                  title="Build in the background. You get a notification when it is ready."
                  aria-label="Build in background"
                >
                  {!queuing && <Clock size={14} aria-hidden="true" />}
                </Button>
                <Button size="icon" onClick={() => sendMessage()} disabled={!draft.trim()} aria-label="Send message" title="Send">
                  <Send size={14} aria-hidden="true" />
                </Button>
              </>
            )}
          </div>
          <p className="m-0 mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-subtle">
            {loading ? (
              <span>Streaming. Press stop to cancel.</span>
            ) : (
              <>
                <span><kbd className="nubi-kbd">Enter</kbd> send</span>
                <span><kbd className="nubi-kbd">Shift</kbd> <kbd className="nubi-kbd">Enter</kbd> new line</span>
                <span className="inline-flex items-center gap-1"><Clock size={11} aria-hidden="true" /> background build</span>
              </>
            )}
          </p>
        </div>
      </div>
  )
}

export default ChatPanel
