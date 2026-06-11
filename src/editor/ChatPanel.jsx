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
 * Fits a fixed-width right sidebar that does NOT scroll itself. The MESSAGE
 * LIST is the only internal scroller; the header (top) and the composer
 * (bottom) stay pinned.
 *
 * Features: streamed assistant text rendered as markdown, rich collapsible
 * tool cards (shared with the global chat panel), gradient assistant avatar,
 * a model picker (remembered per session), Stop (AbortController), conversation
 * history + New chat, example-prompt empty state, and an "Applied to dashboard"
 * affordance on spec apply.
 *
 * Presentation reuses the app design tokens (bg-surface / bg-surface-2 /
 * border-border / text-fg / text-muted / primary / brand-gradient), the shared
 * MarkdownRenderer (`prose-chat` compact overrides live in index.css) and the
 * shared <ToolCard>. The streaming/data flow is unchanged from the prior version.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
  Sparkles, History, Plus, Send, Square, ChevronDown, Check, AlertCircle,
} from 'lucide-react'
import MarkdownRenderer from '../components/MarkdownRenderer.jsx'
import ToolCard from '../chat/ToolCard.jsx'
import {
  streamChat,
  listChatModels,
  listConversations,
  getConversation,
} from '../lib/chat.js'

const MODEL_STORAGE_KEY = 'nubi.chat.model'
const PROPOSE_SPEC_TOOL = 'propose_dashboard_spec'

const SUGGESTIONS = [
  'Add a revenue-by-month bar chart',
  'Summarise what this dashboard shows',
  'Add a KPI for total orders',
  'Make the layout two columns',
]

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
// AssistantAvatar — the brand gradient mark used on every assistant turn
// ---------------------------------------------------------------------------

function AssistantAvatar() {
  return (
    <div className="flex items-center justify-center w-6 h-6 rounded-full shrink-0 mt-0.5 bg-brand-gradient">
      <Sparkles size={12} className="text-white" />
    </div>
  )
}

function TypingDots() {
  return (
    <span className="inline-flex gap-1 items-center">
      <span className="w-1.5 h-1.5 rounded-full bg-muted/70 animate-bounce" style={{ animationDelay: '0ms' }} />
      <span className="w-1.5 h-1.5 rounded-full bg-muted/70 animate-bounce" style={{ animationDelay: '120ms' }} />
      <span className="w-1.5 h-1.5 rounded-full bg-muted/70 animate-bounce" style={{ animationDelay: '240ms' }} />
    </span>
  )
}

// ---------------------------------------------------------------------------
// MessageBubble — renders one user/assistant turn (text + interleaved tools)
// ---------------------------------------------------------------------------

function MessageBubble({ message }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary text-primary-fg px-3.5 py-2.5 text-[13px] leading-relaxed shadow-sm whitespace-pre-wrap break-words">
          {message.content}
        </div>
      </div>
    )
  }

  const tools = message.tools ?? []
  const empty = !message.content && tools.length === 0

  return (
    <div className="flex items-start gap-2.5 max-w-full">
      <AssistantAvatar />
      <div className="flex-1 min-w-0 space-y-2">
        {tools.length > 0 && (
          <div className="space-y-1.5">
            {tools.map(t => (
              <ToolCard
                key={t.id}
                action={{
                  id: t.id,
                  tool: t.name,
                  args: t.input,
                  result: t.output,
                  status: t.output === undefined ? 'running' : 'done',
                }}
              />
            ))}
          </div>
        )}

        {message.content && (
          <div className="prose-chat text-[13px] leading-relaxed text-fg bg-surface-2 border border-border px-3.5 py-2.5 rounded-2xl rounded-bl-sm overflow-hidden">
            <MarkdownRenderer content={message.content} />
            {message.streaming && (
              <span
                className="inline-block w-[7px] h-[14px] -mb-0.5 ml-0.5 bg-brand-teal rounded-[1px] align-middle"
                style={{ animation: 'nubiChatCaret 1s step-end infinite' }}
              />
            )}
          </div>
        )}

        {empty && message.streaming && (
          <div className="rounded-2xl rounded-bl-sm bg-surface-2 border border-border px-3.5 py-3">
            <TypingDots />
          </div>
        )}

        {message.applied && (
          <div className="inline-flex items-center gap-1.5 text-[11px] font-medium text-brand-teal bg-brand-teal/10 border border-brand-teal/20 rounded-lg px-2 py-1">
            <Check size={12} className="shrink-0" />
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
    <div className="absolute right-0 top-10 z-20 w-64 max-h-72 overflow-y-auto rounded-xl border border-border bg-surface shadow-lg py-1">
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

function SuggestionChip({ text, onClick, disabled }) {
  return (
    <button
      type="button"
      onClick={() => onClick(text)}
      disabled={disabled}
      className="px-3 py-1.5 rounded-full border border-border bg-surface-2 text-[12px] text-muted hover:border-primary hover:text-primary hover:bg-primary/5 disabled:opacity-40 disabled:cursor-not-allowed transition-all focus:outline-none focus:ring-2 focus:ring-ring text-left"
    >
      {text}
    </button>
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
  const textareaRef = useRef(null)
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

  // --- auto-scroll to newest (DOM only — no setState in effect) ----------
  useEffect(() => {
    const el = scrollRef.current
    if (el) requestAnimationFrame(() => { el.scrollTop = el.scrollHeight })
  }, [messages])

  // --- helpers to mutate the in-flight assistant message -----------------
  const updateAssistant = useCallback((id, updater) => {
    setMessages(prev => prev.map(m => (m.id === id ? updater(m) : m)))
  }, [])

  // --- send a message -----------------------------------------------------
  const send = useCallback(async (override) => {
    const text = (override ?? input).trim()
    if (!text || streaming || !model) return

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
    setMessages([{ id: 'loading', role: 'assistant', content: '', tools: [], streaming: true }])
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
    <div className="flex flex-col h-full w-full bg-surface overflow-hidden">
      {/* ── Header: title + history + new chat ── */}
      <div className="relative shrink-0 flex items-center gap-2 px-3 py-2.5 border-b border-border">
        <div className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0 bg-brand-gradient">
          <Sparkles size={13} className="text-white" />
        </div>
        <span className="font-display font-semibold text-sm text-fg leading-none">Nubi AI</span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => { if (!historyOpen) refreshConversations(); setHistoryOpen(o => !o) }}
          title="Conversation history"
          aria-label="Conversation history"
          className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <History size={15} />
        </button>
        <button
          type="button"
          onClick={newChat}
          title="New chat"
          aria-label="New chat"
          className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <Plus size={16} />
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
      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-3 scroll-smooth"
        role="log"
        aria-live="polite"
        aria-label="Chat messages"
      >
        {isEmpty && (
          <div className="h-full flex flex-col items-center justify-center text-center gap-5 px-4 pb-4">
            <div className="flex items-center justify-center w-14 h-14 rounded-2xl shadow-lg bg-brand-gradient">
              <Sparkles size={24} className="text-white" />
            </div>
            <div>
              <p className="font-display font-semibold text-fg text-[14px] mb-1">Build with Nubi AI</p>
              <p className="text-[12px] text-muted leading-relaxed max-w-[230px]">
                Describe a change and I&apos;ll inspect your data, propose a dashboard spec, and apply it — watch each tool run live.
              </p>
            </div>
            <div className="flex flex-wrap gap-2 justify-center">
              {SUGGESTIONS.map(s => (
                <SuggestionChip key={s} text={s} onClick={send} disabled={streaming || !model} />
              ))}
            </div>
          </div>
        )}
        {messages.map(m => <MessageBubble key={m.id} message={m} />)}
      </div>

      {/* ── Error banner with retry ── */}
      {error && (
        <div className="shrink-0 mx-3 mb-2 flex items-start gap-2 text-[12px] rounded-xl px-3 py-2 border border-red-500/30 bg-red-500/10 text-red-500">
          <AlertCircle size={14} className="shrink-0 mt-0.5" />
          <span className="flex-1 leading-relaxed">{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="shrink-0 font-medium hover:underline focus:outline-none"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* ── Footer: composer + model picker (pinned) ── */}
      <div className="shrink-0 px-3 py-3 border-t border-border bg-surface">
        <div
          className={`flex items-end gap-2 bg-surface-2 border rounded-xl px-3 py-2 transition-colors ${
            streaming ? 'border-border' : 'border-border focus-within:border-primary focus-within:ring-1 focus-within:ring-ring'
          }`}
        >
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onInput={e => { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 128) + 'px' }}
            placeholder={streaming ? 'Streaming…' : 'Ask Nubi to change this dashboard…'}
            aria-label="Chat input"
            className="flex-1 resize-none bg-transparent text-[13px] text-fg leading-relaxed placeholder:text-muted focus:outline-none min-h-[22px] max-h-32 py-0.5"
            style={{ overflowY: input.split('\n').length > 4 ? 'auto' : 'hidden' }}
          />
          {streaming ? (
            <button
              type="button"
              onClick={stop}
              aria-label="Stop"
              className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0 bg-surface border border-border text-fg hover:bg-surface-2 active:scale-95 transition-all focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Square size={12} className="fill-current" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => send()}
              disabled={!input.trim() || !model}
              aria-label="Send message"
              className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0 bg-primary text-primary-fg hover:opacity-90 active:scale-95 disabled:opacity-35 disabled:cursor-not-allowed transition-all focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Send size={13} />
            </button>
          )}
        </div>

        <div className="flex items-center justify-between mt-1.5">
          <div className="relative">
            <select
              value={model}
              onChange={e => setModel(e.target.value)}
              disabled={streaming}
              aria-label="Select AI model"
              className="appearance-none pl-2 pr-5 py-0.5 text-[10px] font-medium text-muted bg-transparent border border-transparent rounded-md hover:text-fg disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
            >
              {modelOptions.length === 0 && <option value="">No models</option>}
              {modelOptions.map(m => (
                <option key={m.id} value={m.id}>{m.label ?? m.id}</option>
              ))}
            </select>
            <ChevronDown size={10} className="absolute right-1 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          </div>
          <p className="text-[10px] text-muted/60 leading-none">
            {streaming ? 'Streaming…' : 'Shift+Enter for newline'}
          </p>
        </div>
      </div>
    </div>
  )
}
