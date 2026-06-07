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
  X, Sparkles, Send, Square, ChevronDown, ChevronRight, AlertCircle,
  Wrench, BarChart3, Database, Search, Code2, Table2, Check, Loader2, Bot,
} from 'lucide-react'
import MarkdownRenderer from '../components/MarkdownRenderer.jsx'
import { streamChatMessage } from './chatApi.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MODELS = [
  { id: 'default', label: 'Nubi Default' },
  { id: 'claude',  label: 'Claude'       },
  { id: 'gpt-4o',  label: 'GPT-4o'       },
]

const SUGGESTIONS = [
  'Build a sales dashboard',
  'Show revenue by region',
  'Which queries run slowest?',
  'Summarise connected data sources',
]

// tool name → icon + human label + accent
const TOOL_META = {
  generate_sql:     { icon: Code2,     label: 'Generate SQL',    color: 'text-brand-blue', bg: 'bg-brand-blue/10' },
  run_query:        { icon: Database,  label: 'Run query',       color: 'text-brand-teal', bg: 'bg-brand-teal/10' },
  create_dashboard: { icon: BarChart3, label: 'Create dashboard',color: 'text-brand-blue', bg: 'bg-brand-blue/10' },
  edit_dashboard:   { icon: BarChart3, label: 'Edit dashboard',  color: 'text-brand-blue', bg: 'bg-brand-blue/10' },
  get_schema:       { icon: Table2,    label: 'Get schema',      color: 'text-accent',     bg: 'bg-accent/10' },
  list_queries:     { icon: Search,    label: 'List queries',    color: 'text-accent',     bg: 'bg-accent/10' },
  default:          { icon: Wrench,    label: null,              color: 'text-muted',      bg: 'bg-surface-2' },
}
const getToolMeta = (name) => TOOL_META[name] ?? TOOL_META.default
const toolLabel = (name) => getToolMeta(name).label ?? (name ? name.replace(/_/g, ' ') : 'tool')

const truncate = (s, n = 64) => (s && s.length > n ? s.slice(0, n) + '…' : s || '')

// ---------------------------------------------------------------------------
// Tool result renderers
// ---------------------------------------------------------------------------

function MiniTable({ columns = [], rows = [] }) {
  const cols = columns.length ? columns : (rows[0] ? Object.keys(rows[0]) : [])
  if (!cols.length) return <p className="text-[11px] text-muted">No columns.</p>
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-[11px] font-mono border-collapse">
        <thead>
          <tr className="bg-surface-2">
            {cols.map(c => (
              <th key={c} className="text-left font-semibold text-muted px-2 py-1 border-b border-border whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 8).map((r, i) => (
            <tr key={i} className="even:bg-surface-2/40">
              {cols.map(c => (
                <td key={c} className="px-2 py-1 text-fg whitespace-nowrap max-w-[160px] truncate border-b border-border/50">
                  {String(r?.[c] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ResultBody({ tool, result }) {
  if (!result || typeof result !== 'object') {
    return <pre className="text-[11px] text-fg whitespace-pre-wrap break-all">{String(result ?? '')}</pre>
  }
  if (result.error) {
    return (
      <p className="text-[11px] text-red-500 flex items-start gap-1.5">
        <AlertCircle size={12} className="shrink-0 mt-0.5" />{result.error}
      </p>
    )
  }

  if (tool === 'generate_sql') {
    return (
      <div className="space-y-1.5">
        <pre className="text-[11px] leading-relaxed font-mono text-fg bg-surface-2 rounded-lg px-2.5 py-2 overflow-x-auto whitespace-pre-wrap break-words">
          {result.sql || '—'}
        </pre>
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-medium ${result.valid ? 'bg-brand-teal/15 text-brand-teal' : 'bg-amber-500/15 text-amber-600'}`}>
            {result.valid ? 'valid' : 'needs review'}
          </span>
          {(result.tables || []).slice(0, 4).map(t => (
            <span key={t} className="text-[10px] px-1.5 py-0.5 rounded-md bg-surface-2 text-muted font-mono">{t}</span>
          ))}
        </div>
        {(result.issues || []).length > 0 && (
          <ul className="text-[10px] text-amber-600 list-disc ml-4">
            {result.issues.slice(0, 3).map((iss, i) => <li key={i}>{iss}</li>)}
          </ul>
        )}
      </div>
    )
  }

  if (tool === 'run_query') {
    return (
      <div className="space-y-1.5">
        <p className="text-[11px] text-muted">
          <span className="font-semibold text-fg">{result.row_count ?? 0}</span> rows ·{' '}
          <span className="font-semibold text-fg">{(result.columns || []).length}</span> cols
        </p>
        {(result.rows || []).length > 0 && (
          <MiniTable columns={result.columns} rows={result.rows} />
        )}
      </div>
    )
  }

  if (tool === 'create_dashboard') {
    return (
      <div className="space-y-1.5">
        <p className="text-[11px] text-fg font-medium">{result.title || 'Dashboard'}</p>
        <div className="flex flex-wrap gap-1">
          {(result.widgets || []).map((w, i) => (
            <span key={i} className="text-[10px] px-1.5 py-0.5 rounded-md bg-brand-blue/10 text-brand-blue font-mono">
              {w.type}{w.title ? ` · ${truncate(w.title, 18)}` : ''}
            </span>
          ))}
          {!result.widgets?.length && (
            <span className="text-[10px] text-muted">{result.widget_count ?? 0} widget(s)</span>
          )}
        </div>
      </div>
    )
  }

  return (
    <pre className="text-[11px] text-fg whitespace-pre-wrap break-all leading-relaxed">
      {JSON.stringify(result, null, 2)}
    </pre>
  )
}

function summaryLine(tool, result) {
  if (!result) return ''
  if (result.error) return result.error
  if (tool === 'generate_sql') return truncate(result.sql, 56)
  if (tool === 'run_query') return `${result.row_count ?? 0} rows · ${(result.columns || []).length} cols`
  if (tool === 'create_dashboard') return `${result.widget_count ?? 0} widgets`
  return ''
}

// ---------------------------------------------------------------------------
// ToolBlock — one tool call, animates running → result
// ---------------------------------------------------------------------------

function ToolBlock({ action }) {
  const [open, setOpen] = useState(false)
  const { icon: Icon, color, bg } = getToolMeta(action.tool)
  const running = action.status === 'running'
  const errored = action.status === 'error'

  return (
    <div className={`rounded-xl border overflow-hidden ${errored ? 'border-red-500/30' : 'border-border'} ${open ? 'shadow-sm' : ''}`}>
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2.5 px-2.5 py-2 bg-surface-2 hover:bg-surface-2/70 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-inset"
        aria-expanded={open}
      >
        <span className={`flex items-center justify-center w-6 h-6 rounded-lg ${bg} shrink-0`}>
          <Icon size={13} className={color} />
        </span>
        <span className="flex-1 min-w-0">
          <span className="block font-mono text-[11px] font-semibold text-fg leading-tight">
            {toolLabel(action.tool)}
          </span>
          {!running && summaryLine(action.tool, action.result) && (
            <span className="block text-[10px] text-muted font-mono truncate">
              {summaryLine(action.tool, action.result)}
            </span>
          )}
          {running && (
            <span className="block text-[10px] text-brand-teal font-mono">running…</span>
          )}
        </span>
        {/* status indicator */}
        <span className="shrink-0 flex items-center">
          {running && <Loader2 size={14} className="text-brand-teal animate-spin" />}
          {action.status === 'done' && <Check size={14} className="text-brand-teal" />}
          {errored && <AlertCircle size={14} className="text-red-500" />}
        </span>
        <span className="text-muted shrink-0">
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
      </button>

      {open && (
        <div className="px-2.5 py-2.5 bg-surface border-t border-border space-y-2">
          {action.arguments && Object.keys(action.arguments).length > 0 && (
            <div>
              <p className="text-muted uppercase tracking-wider mb-1 text-[10px] font-semibold">Arguments</p>
              <pre className="text-[11px] text-fg font-mono whitespace-pre-wrap break-all bg-surface-2 rounded-lg px-2 py-1.5">
                {JSON.stringify(action.arguments, null, 2)}
              </pre>
            </div>
          )}
          {action.result != null && (
            <div>
              <p className="text-muted uppercase tracking-wider mb-1 text-[10px] font-semibold">Result</p>
              <ResultBody tool={action.tool} result={action.result} />
            </div>
          )}
          {running && <p className="text-[11px] text-muted italic">Executing…</p>}
        </div>
      )}
    </div>
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
  const [selectedModel, setModel] = useState(MODELS[0].id)

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
    <>
      <style>{`
        @keyframes nubiChatPulse { 0%,100%{opacity:0.35;transform:scale(0.85)} 50%{opacity:1;transform:scale(1.1)} }
        @keyframes nubiChatCaret { 0%,100%{opacity:1} 50%{opacity:0} }
        .prose-chat .my-4 { margin-top: 0.5rem; margin-bottom: 0.5rem; }
        .prose-chat .mt-10, .prose-chat .mt-8, .prose-chat .mt-6 { margin-top: 0.75rem; }
        .prose-chat .mb-4, .prose-chat .mb-3, .prose-chat .mb-6 { margin-bottom: 0.5rem; }
        .prose-chat p:first-child { margin-top: 0; }
        .prose-chat p:last-child { margin-bottom: 0; }
        .prose-chat h1, .prose-chat h2, .prose-chat h3, .prose-chat h4 { font-size: 0.875rem; margin-top: 0.5rem; margin-bottom: 0.25rem; }
        .prose-chat ul, .prose-chat ol { margin-top: 0.25rem; margin-bottom: 0.25rem; }
        .prose-chat pre { font-size: 0.75rem; }
      `}</style>

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
    </>
  )
}

export default ChatPanel
