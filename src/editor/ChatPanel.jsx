/**
 * ChatPanel.jsx — Cursor-like streamed chat for the dashboard editor.
 *
 * Mounted by DashboardEditor (a parallel agent owns that file) exactly as:
 *   <ChatPanel boardId={savedBoardId} spec={spec} onApplySpec={handleAIApply} />
 *
 * Props
 * -----
 * boardId      {string|null}                  current board id (for history scoping + stream context)
 * spec         {object}                        current DashboardSpec (model context)
 * onApplySpec  {(spec, mode:'replace'|'merge') => void}
 *              Called when a turn yields a dashboard spec (from the
 *              propose_dashboard_spec tool result, or a spec in the final
 *              message). We call it with mode 'replace'.
 *
 * Layout
 * ------
 * Fits a fixed-width (w-80) right sidebar that does NOT scroll itself. The
 * MESSAGE LIST is the only internal scroller; the model picker + history
 * controls (top) and the composer (bottom) stay pinned.
 *
 * Features: streamed assistant text, inline tool_use/tool_result blocks,
 * model picker (remembered per session), Stop (AbortController), conversation
 * history + New chat, and an "Applied to dashboard" affordance on spec apply.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
  streamChat,
  listChatModels,
  listConversations,
  getConversation,
} from '../lib/chat.js'

const MODEL_STORAGE_KEY = 'nubi.chat.model'
const PROPOSE_SPEC_TOOL = 'propose_dashboard_spec'

// ---------------------------------------------------------------------------
// Spec extraction — find a DashboardSpec inside a tool result / message
// ---------------------------------------------------------------------------

/** Heuristic: does this object look like a DashboardSpec? */
function looksLikeSpec(obj) {
  return !!obj && typeof obj === 'object' && Array.isArray(obj.widgets)
}

/**
 * Coerce a tool output (object | JSON string | wrapper) into a DashboardSpec,
 * or return null if none is present.
 */
function extractSpec(output) {
  if (output == null) return null
  let val = output
  if (typeof val === 'string') {
    try { val = JSON.parse(val) } catch { return null }
  }
  if (looksLikeSpec(val)) return val
  if (looksLikeSpec(val?.spec)) return val.spec
  if (looksLikeSpec(val?.dashboard)) return val.dashboard
  return null
}

// ---------------------------------------------------------------------------
// ToolBlock — collapsible tool_use / tool_result display
// ---------------------------------------------------------------------------

function pretty(value) {
  if (value == null) return ''
  if (typeof value === 'string') {
    try { return JSON.stringify(JSON.parse(value), null, 2) } catch { return value }
  }
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}

function ToolBlock({ tool }) {
  const [open, setOpen] = useState(false)
  const hasResult = tool.output !== undefined
  return (
    <div className="rounded-lg border border-border bg-surface-2/60 text-xs overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-left hover:bg-surface-2 transition-colors"
      >
        <svg
          className={`w-3 h-3 text-muted transition-transform ${open ? 'rotate-90' : ''}`}
          viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"
        >
          <path d="M4.5 3l3 3-3 3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span aria-hidden>🔧</span>
        <span className="font-mono font-medium text-fg truncate flex-1">{tool.name}</span>
        <span className={`text-[10px] font-medium ${hasResult ? 'text-emerald-500' : 'text-muted animate-pulse'}`}>
          {hasResult ? 'done' : 'running…'}
        </span>
      </button>
      {open && (
        <div className="px-2.5 pb-2 pt-0.5 space-y-2 border-t border-border">
          <div>
            <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-0.5">Input</p>
            <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-fg/90 bg-surface rounded p-2 max-h-48 overflow-auto">
              {pretty(tool.input) || '—'}
            </pre>
          </div>
          {hasResult && (
            <div>
              <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-0.5">Result</p>
              <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-fg/90 bg-surface rounded p-2 max-h-48 overflow-auto">
                {pretty(tool.output) || '—'}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// MessageBubble — renders one user/assistant turn (text + interleaved tools)
// ---------------------------------------------------------------------------

function MessageBubble({ message }) {
  const isUser = message.role === 'user'
  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg rounded-br-sm bg-primary text-primary-fg px-3 py-2 text-sm whitespace-pre-wrap break-words">
          {message.content}
        </div>
      </div>
    )
  }

  const tools = message.tools ?? []
  const empty = !message.content && tools.length === 0
  return (
    <div className="flex justify-start">
      <div className="max-w-[92%] w-full space-y-2">
        {message.content && (
          <div className="rounded-lg rounded-bl-sm bg-surface-2 text-fg px-3 py-2 text-sm whitespace-pre-wrap break-words border border-border">
            {message.content}
            {message.streaming && <span className="inline-block w-1.5 h-3.5 ml-0.5 align-middle bg-primary animate-pulse" />}
          </div>
        )}
        {tools.map(t => <ToolBlock key={t.id} tool={t} />)}
        {empty && message.streaming && (
          <div className="rounded-lg rounded-bl-sm bg-surface-2 border border-border px-3 py-2 text-sm text-muted">
            <span className="inline-flex gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-muted/60 animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-muted/60 animate-bounce" style={{ animationDelay: '120ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-muted/60 animate-bounce" style={{ animationDelay: '240ms' }} />
            </span>
          </div>
        )}
        {message.applied && (
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-emerald-500 px-1">
            <svg className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M16.7 5.3a1 1 0 010 1.4l-8 8a1 1 0 01-1.4 0l-4-4a1 1 0 011.4-1.4l3.3 3.29 7.3-7.3a1 1 0 011.4 0z" clipRule="evenodd" />
            </svg>
            Applied to dashboard
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// HistoryDropdown — list past conversations to reopen
// ---------------------------------------------------------------------------

function HistoryDropdown({ conversations, activeChatId, onSelect, onClose, loading }) {
  return (
    <div className="absolute right-0 top-9 z-20 w-64 max-h-72 overflow-y-auto rounded-lg border border-border bg-surface shadow-lg py-1">
      {loading && <p className="px-3 py-2 text-xs text-muted">Loading…</p>}
      {!loading && conversations.length === 0 && (
        <p className="px-3 py-2 text-xs text-muted">No past conversations.</p>
      )}
      {conversations.map(c => (
        <button
          key={c.id}
          onClick={() => { onSelect(c.id); onClose() }}
          className={`w-full text-left px-3 py-1.5 text-xs hover:bg-surface-2 transition-colors ${
            c.id === activeChatId ? 'bg-surface-2' : ''
          }`}
        >
          <p className="text-fg truncate font-medium">{c.title || 'Untitled chat'}</p>
          {c.updated_at && (
            <p className="text-[10px] text-muted">{new Date(c.updated_at).toLocaleString()}</p>
          )}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ChatPanel — main export
// ---------------------------------------------------------------------------

export default function ChatPanel({ boardId = null, spec = null, onApplySpec }) {
  // --- model picker -------------------------------------------------------
  const [models, setModels] = useState([])
  const [model, setModel] = useState(() => {
    try { return sessionStorage.getItem(MODEL_STORAGE_KEY) || '' } catch { return '' }
  })

  // --- conversation state -------------------------------------------------
  const [chatId, setChatId] = useState(null)
  const [messages, setMessages] = useState([]) // [{ id, role, content, tools, streaming, applied }]
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState(null)
  const [input, setInput] = useState('')

  // --- history ------------------------------------------------------------
  const [conversations, setConversations] = useState([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)

  const abortRef = useRef(null)
  const scrollRef = useRef(null)
  const specRef = useRef(spec)
  useEffect(() => { specRef.current = spec }, [spec])

  // --- load models on mount ----------------------------------------------
  useEffect(() => {
    let cancelled = false
    listChatModels().then(list => {
      if (cancelled) return
      setModels(list)
      // Default to the remembered model if still available, else first.
      setModel(prev => {
        if (prev && list.some(m => m.id === prev)) return prev
        return list[0]?.id ?? prev
      })
    })
    return () => { cancelled = true }
  }, [])

  // --- remember model per session ----------------------------------------
  useEffect(() => {
    if (!model) return
    try { sessionStorage.setItem(MODEL_STORAGE_KEY, model) } catch { /* ignore */ }
  }, [model])

  // --- load conversations when the board changes -------------------------
  const refreshConversations = useCallback(() => {
    setHistoryLoading(true)
    listConversations(boardId)
      .then(list => setConversations(list))
      .finally(() => setHistoryLoading(false))
  }, [boardId])

  useEffect(() => { refreshConversations() }, [refreshConversations])

  // --- auto-scroll to newest ---------------------------------------------
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  // --- helpers to mutate the in-flight assistant message -----------------
  const updateAssistant = useCallback((id, updater) => {
    setMessages(prev => prev.map(m => (m.id === id ? updater(m) : m)))
  }, [])

  // --- send a message -----------------------------------------------------
  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming) return

    setError(null)
    setInput('')

    const userMsg = { id: `u_${Date.now()}`, role: 'user', content: text }
    const assistantId = `a_${Date.now()}`
    const assistantMsg = { id: assistantId, role: 'assistant', content: '', tools: [], streaming: true, applied: false }
    setMessages(prev => [...prev, userMsg, assistantMsg])
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller

    // Track the spec proposed by a tool result during this turn, plus a
    // local id→toolName map (state is async, so we can't read it back in time).
    let proposedSpec = null
    let accumulated = '' // running assistant text (state is async; this is the source of truth for the fallback)
    const toolNames = new Map()

    const onEvent = (evt) => {
      switch (evt?.type) {
        case 'token':
          accumulated += evt.text ?? ''
          updateAssistant(assistantId, m => ({ ...m, content: m.content + (evt.text ?? '') }))
          break
        case 'tool_use':
          toolNames.set(evt.id, evt.name)
          updateAssistant(assistantId, m => ({
            ...m,
            tools: [...m.tools, { id: evt.id, name: evt.name, input: evt.input, output: undefined }],
          }))
          break
        case 'tool_result': {
          updateAssistant(assistantId, m => ({
            ...m,
            tools: m.tools.map(t => (t.id === evt.id ? { ...t, output: evt.output } : t)),
          }))
          // If this is a proposed dashboard spec, remember it for apply.
          // Prefer the dedicated tool; otherwise accept any spec-shaped output.
          const fromSpecTool = toolNames.get(evt.id) === PROPOSE_SPEC_TOOL
          const candidate = extractSpec(evt.output)
          if (candidate && (fromSpecTool || !proposedSpec)) proposedSpec = candidate
          break
        }
        case 'message':
          if (evt.chat_id) setChatId(evt.chat_id)
          break
        case 'error':
          setError(evt.message ?? 'Chat error.')
          break
        default:
          break
      }
    }

    try {
      await streamChat({
        chatId,
        boardId,
        model,
        message: text,
        signal: controller.signal,
        onEvent,
      })

      // Final-message fallback: if no tool proposed a spec, look for one in
      // the assistant's final text content.
      if (!proposedSpec) proposedSpec = extractSpec(accumulated)

      if (proposedSpec && onApplySpec) {
        onApplySpec(proposedSpec, 'replace')
        updateAssistant(assistantId, m => ({ ...m, applied: true }))
      }
    } catch (err) {
      if (err?.name !== 'AbortError') setError(err.message ?? 'Chat failed.')
    } finally {
      updateAssistant(assistantId, m => ({ ...m, streaming: false }))
      setStreaming(false)
      abortRef.current = null
      refreshConversations()
    }
  }, [input, streaming, chatId, boardId, model, onApplySpec, updateAssistant, refreshConversations])

  // --- stop the in-flight stream -----------------------------------------
  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  // --- new chat -----------------------------------------------------------
  const newChat = useCallback(() => {
    if (streaming) abortRef.current?.abort()
    setChatId(null)
    setMessages([])
    setError(null)
    setHistoryOpen(false)
  }, [streaming])

  // --- reopen a past conversation ----------------------------------------
  const openConversation = useCallback(async (id) => {
    if (streaming) abortRef.current?.abort()
    setError(null)
    setChatId(id)
    setMessages([{ id: 'loading', role: 'assistant', content: 'Loading conversation…', tools: [], streaming: true }])
    try {
      const conv = await getConversation(id)
      const loaded = (conv.messages ?? []).map((m, i) => ({
        id: `h_${id}_${i}`,
        role: m.role,
        content: m.content ?? '',
        tools: [],
        streaming: false,
        applied: false,
      }))
      setMessages(loaded)
    } catch (err) {
      setMessages([])
      setError(err.message ?? 'Could not load conversation.')
    }
  }, [streaming])

  const onKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }, [send])

  const isEmpty = messages.length === 0
  const modelOptions = useMemo(
    () => (models.length > 0 ? models : (model ? [{ id: model, label: model }] : [])),
    [models, model],
  )

  return (
    <div className="flex flex-col h-full w-full bg-surface">
      {/* ── Header: title + history + new chat ── */}
      <div className="relative shrink-0 flex items-center gap-2 px-3 py-2.5 border-b border-border">
        <span className="text-sm font-semibold text-fg flex items-center gap-1.5">
          <span aria-hidden>✨</span> Chat
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => { if (!historyOpen) refreshConversations(); setHistoryOpen(o => !o) }}
          title="Conversation history"
          className="text-xs px-2 py-1 rounded-lg border border-border text-muted hover:text-fg hover:border-border/80 transition-colors"
        >
          History
        </button>
        <button
          type="button"
          onClick={newChat}
          title="New chat"
          className="text-xs px-2 py-1 rounded-lg border border-border text-muted hover:text-fg hover:border-border/80 transition-colors"
        >
          + New
        </button>
        {historyOpen && (
          <HistoryDropdown
            conversations={conversations}
            activeChatId={chatId}
            loading={historyLoading}
            onSelect={openConversation}
            onClose={() => setHistoryOpen(false)}
          />
        )}
      </div>

      {/* ── Message list (the ONLY internal scroller) ── */}
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-3">
        {isEmpty && (
          <div className="h-full flex flex-col items-center justify-center text-center text-muted px-2">
            <span className="text-2xl mb-2" aria-hidden>💬</span>
            <p className="text-sm font-medium text-fg">Ask the assistant</p>
            <p className="text-xs mt-1 leading-snug">
              Describe changes to your dashboard. The assistant can inspect data
              and propose a spec you can apply.
            </p>
          </div>
        )}
        {messages.map(m => <MessageBubble key={m.id} message={m} />)}
      </div>

      {/* ── Error banner ── */}
      {error && (
        <div className="shrink-0 mx-3 mb-2 text-xs rounded-lg px-3 py-2 border"
          style={{ background: 'color-mix(in srgb, #ef4444 8%, transparent)', color: '#ef4444', borderColor: 'color-mix(in srgb, #ef4444 25%, transparent)' }}>
          {error}
        </div>
      )}

      {/* ── Footer: model picker + composer (pinned) ── */}
      <div className="shrink-0 border-t border-border p-3 space-y-2 bg-surface">
        <div className="flex items-center gap-2">
          <label className="text-[11px] font-medium text-muted shrink-0">Model</label>
          <select
            value={model}
            onChange={e => setModel(e.target.value)}
            disabled={streaming}
            className="flex-1 h-8 text-xs border border-border rounded-lg pl-2.5 pr-7 bg-surface text-fg appearance-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring/60 disabled:opacity-50 transition-colors"
          >
            {modelOptions.length === 0 && <option value="">No models</option>}
            {modelOptions.map(m => (
              <option key={m.id} value={m.id}>{m.label ?? m.id}</option>
            ))}
          </select>
        </div>

        <div className="relative">
          <textarea
            rows={3}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={streaming}
            placeholder={streaming ? 'Streaming…' : 'Message the assistant…  (Enter to send)'}
            className="w-full text-sm border border-border rounded-lg px-3 py-2 pr-3 resize-none bg-surface text-fg placeholder:text-muted/60 focus:outline-none focus:ring-2 focus:ring-ring/60 disabled:opacity-60 transition-colors"
          />
        </div>

        {streaming ? (
          <button
            type="button"
            onClick={stop}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors"
          >
            <span className="w-2.5 h-2.5 rounded-[2px] bg-current" aria-hidden />
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={send}
            disabled={!input.trim() || !model}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          >
            Send
          </button>
        )}
      </div>
    </div>
  )
}
