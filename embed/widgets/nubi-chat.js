/**
 * nubi-chat.js — <nubi-chat> streaming conversational chat widget.
 *
 * A drop-in chat UI that streams assistant turns from Nubi's chat API
 * (POST {endpoint}, Server-Sent Events). Conversation memory is SERVER-side:
 * the terminal `message` frame returns a `chat_id` which the element stores and
 * replays on the next turn, so hosts get multi-turn memory with one tag.
 *
 * Transport uses fetch() + a ReadableStream reader (NOT EventSource — SSE via
 * EventSource can only GET, and this endpoint requires a POST body).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ATTRIBUTES
 * ════════════════════════════════════════════════════════════════════════════
 *   endpoint       Chat stream URL. Default /api/v1/chat/stream. Overridable so a
 *                  host can point it at their own server-side proxy (a proxy that
 *                  injects auth server-side is the common deployment).
 *   token          Bearer token sent as `Authorization: Bearer <token>`. Optional
 *                  — omit when the host proxy injects auth.
 *   model          Chat model id. Optional; when omitted the element still sends a
 *                  sensible default (the direct Nubi API requires one). A proxy is
 *                  free to ignore / override it. Default: claude-opus-4-8.
 *   board-id       Optional board/dashboard id to scope the conversation.
 *   mcp-tools-url  Optional MCP server URL passed through as `mcp_servers`.
 *   placeholder    Input placeholder text. Default "Ask a question…".
 *   height         CSS height of the widget. Default 480px.
 *   theme          "dark" | "light" (applies the Nubi token preset).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * EVENTS  (CustomEvent on the element; bubbles + composed)
 * ════════════════════════════════════════════════════════════════════════════
 *   nubi-chat:message  { chat_id, message_id, text, spec? } — a turn finalized.
 *   nubi-chat:spec     { spec, chat_id }                    — a dashboard spec
 *                                                             was proposed.
 *
 * CSS CUSTOM PROPERTIES
 * ---------------------
 * --nubi-bg, --nubi-fg, --nubi-fg-muted, --nubi-primary, --nubi-border,
 * --nubi-error  (standard Nubi theme vars)
 */

import { BASE_STYLES, escapeHtml } from './shared.js'
import { applyTheme } from '../theme.js'

// Direct Nubi chat API requires a model; a proxy may override it. Kept in sync
// with backend/app/chat/models.py DEFAULT_MODEL_ID.
const DEFAULT_MODEL = 'claude-opus-4-8'
const DEFAULT_ENDPOINT = '/api/v1/chat/stream'

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const CHAT_STYLES = /* css */ `
  ${BASE_STYLES}

  :host { min-height: 240px; }

  .chat-wrap {
    display: flex; flex-direction: column;
    width: 100%; height: 100%; box-sizing: border-box;
  }

  .chat-log {
    flex: 1; overflow-y: auto;
    display: flex; flex-direction: column; gap: 10px;
    padding: 14px 14px 6px;
    scroll-behavior: smooth;
  }

  .chat-msg {
    max-width: 82%;
    padding: 8px 12px;
    border-radius: 12px;
    font-size: 13px; line-height: 1.5;
    white-space: pre-wrap; word-wrap: break-word;
  }
  .chat-msg.user {
    align-self: flex-end;
    background: var(--nubi-primary, #6366f1);
    color: #fff;
    border-bottom-right-radius: 3px;
  }
  .chat-msg.assistant {
    align-self: flex-start;
    background: var(--nubi-accent, #1e2433);
    color: var(--nubi-fg, #e2e8f0);
    border-bottom-left-radius: 3px;
  }
  .chat-msg.assistant.streaming::after {
    content: '▍';
    opacity: 0.55;
    animation: nubi-blink 1s step-start infinite;
  }
  @keyframes nubi-blink { 50% { opacity: 0; } }

  .chat-tools {
    align-self: flex-start;
    display: flex; flex-wrap: wrap; gap: 6px;
    max-width: 82%;
  }
  .chat-tool-chip {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 10px; font-weight: 600; letter-spacing: 0.03em;
    padding: 3px 8px; border-radius: 10px;
    background: color-mix(in srgb, var(--nubi-primary, #6366f1) 14%, var(--nubi-bg, #0f1117));
    color: var(--nubi-primary, #6366f1);
    white-space: nowrap;
  }
  .chat-tool-chip::before { content: '⚙'; font-size: 10px; opacity: 0.85; }

  .chat-error {
    display: none;
    margin: 0 14px 8px;
    padding: 7px 11px;
    border-radius: 6px;
    font-size: 12px;
    color: var(--nubi-error, #ef4444);
    background: color-mix(in srgb, var(--nubi-error, #ef4444) 12%, var(--nubi-bg, #0f1117));
    border: 1px solid color-mix(in srgb, var(--nubi-error, #ef4444) 30%, var(--nubi-bg, #0f1117));
  }
  .chat-error.visible { display: block; }
  .chat-error::before { content: '⚠ '; }

  .chat-input-row {
    display: flex; gap: 8px; align-items: flex-end;
    padding: 10px 12px;
    border-top: 1px solid var(--nubi-border, #2d3748);
  }
  .chat-input {
    flex: 1;
    resize: none;
    max-height: 120px;
    padding: 8px 11px;
    font: inherit; font-size: 13px; line-height: 1.4;
    color: var(--nubi-fg, #e2e8f0);
    background: var(--nubi-bg, #0f1117);
    border: 1px solid var(--nubi-border, #2d3748);
    border-radius: 8px;
    outline: none;
  }
  .chat-input:focus { border-color: var(--nubi-primary, #6366f1); }
  .chat-input:disabled { opacity: 0.55; cursor: not-allowed; }

  .chat-send {
    flex-shrink: 0;
    padding: 8px 16px;
    font: inherit; font-size: 13px; font-weight: 600;
    color: #fff;
    background: var(--nubi-primary, #6366f1);
    border: none; border-radius: 8px;
    cursor: pointer;
  }
  .chat-send:disabled { opacity: 0.5; cursor: not-allowed; }

  .chat-empty {
    margin: auto;
    font-size: 12px; opacity: 0.4; text-align: center;
  }
`

// ---------------------------------------------------------------------------
// SSE frame parsing
// ---------------------------------------------------------------------------

/**
 * Parse SSE `data:` lines out of a raw text buffer.
 *
 * Frames are separated by a blank line. Each frame may carry one or more
 * `data:` lines; we concatenate them and JSON-parse the result (the backend
 * emits exactly one JSON object per frame). Returns the parsed objects plus the
 * unparsed tail (an incomplete trailing frame) for the next read.
 *
 * @param {string} buffer
 * @returns {{ events: object[], rest: string }}
 */
function parseSseBuffer(buffer) {
  const events = []
  // Normalise CRLF so frame splitting is transport-agnostic.
  const normalized = buffer.replace(/\r\n/g, '\n')
  const frames = normalized.split('\n\n')
  // The final chunk is an incomplete frame — keep it for the next read.
  const rest = frames.pop() ?? ''

  for (const frame of frames) {
    const data = frame
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim())
      .join('')
    if (!data) continue
    try {
      events.push(JSON.parse(data))
    } catch {
      // Skip a malformed frame rather than aborting the whole stream.
    }
  }
  return { events, rest }
}

// ---------------------------------------------------------------------------
// NubiChat — custom element
// ---------------------------------------------------------------------------

class NubiChat extends HTMLElement {
  static get observedAttributes() {
    return [
      'endpoint', 'token', 'model', 'board-id', 'mcp-tools-url',
      'placeholder', 'height', 'theme',
    ]
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })
    this._ac = null          // AbortController for the in-flight stream
    this._chatId = null      // server-side conversation id (memory across turns)
    this._streaming = false
  }

  connectedCallback() {
    applyTheme(this, this.getAttribute('theme') || 'dark')
    this._ensureScaffold()
  }

  disconnectedCallback() { this._abort() }

  attributeChangedCallback(name, old, val) {
    if (old === val) return
    if (name === 'theme') applyTheme(this, val || 'dark')
    if (name === 'height') this.style.height = val || '480px'
    if (name === 'placeholder' && this.isConnected) {
      const input = this._shadow.querySelector('.chat-input')
      if (input) input.placeholder = val || 'Ask a question…'
    }
  }

  _abort() { if (this._ac) { this._ac.abort(); this._ac = null } }

  _endpoint() { return this.getAttribute('endpoint') || DEFAULT_ENDPOINT }

  // --- Scaffold / chrome ---------------------------------------------------

  _ensureScaffold() {
    if (this._shadow.querySelector('.chat-wrap')) return

    this.style.height = this.getAttribute('height') || '480px'

    const styleEl = document.createElement('style')
    styleEl.textContent = CHAT_STYLES
    this._shadow.innerHTML = ''
    this._shadow.appendChild(styleEl)

    const placeholder = this.getAttribute('placeholder') || 'Ask a question…'

    const wrap = document.createElement('div')
    wrap.className = 'chat-wrap'
    wrap.innerHTML = /* html */ `
      <div class="chat-log" role="log" aria-live="polite" aria-label="Chat conversation">
        <div class="chat-empty">Ask a question to get started.</div>
      </div>
      <div class="chat-error" role="alert"></div>
      <form class="chat-input-row">
        <label class="nubi-visually-hidden" for="nubi-chat-input" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">Message</label>
        <textarea id="nubi-chat-input" class="chat-input" rows="1"
          placeholder="${escapeHtml(placeholder)}" aria-label="Message"></textarea>
        <button type="submit" class="chat-send">Send</button>
      </form>
    `
    this._shadow.appendChild(wrap)

    const form = this._shadow.querySelector('.chat-input-row')
    const input = this._shadow.querySelector('.chat-input')

    form.addEventListener('submit', (e) => {
      e.preventDefault()
      this._send()
    })
    // Enter sends; Shift+Enter inserts a newline.
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        this._send()
      }
    })
  }

  _log() { return this._shadow.querySelector('.chat-log') }

  _scrollToBottom() {
    const log = this._log()
    if (log) log.scrollTop = log.scrollHeight
  }

  _appendMessage(role, text) {
    const empty = this._shadow.querySelector('.chat-empty')
    if (empty) empty.remove()
    const msg = document.createElement('div')
    msg.className = `chat-msg ${role}`
    msg.textContent = text
    this._log().appendChild(msg)
    this._scrollToBottom()
    return msg
  }

  _appendToolChip(label) {
    let tools = this._log().querySelector('.chat-tools:last-of-type')
    // Start a fresh tool row for each streaming turn (after the assistant bubble).
    if (!tools || tools !== this._log().lastElementChild) {
      tools = document.createElement('div')
      tools.className = 'chat-tools'
      this._log().appendChild(tools)
    }
    const chip = document.createElement('span')
    chip.className = 'chat-tool-chip'
    chip.textContent = label
    tools.appendChild(chip)
    this._scrollToBottom()
  }

  _showError(message) {
    const banner = this._shadow.querySelector('.chat-error')
    if (!banner) return
    banner.textContent = message || 'Something went wrong.'
    banner.classList.add('visible')
  }

  _clearError() {
    const banner = this._shadow.querySelector('.chat-error')
    if (banner) { banner.classList.remove('visible'); banner.textContent = '' }
  }

  _setStreaming(on) {
    this._streaming = on
    const input = this._shadow.querySelector('.chat-input')
    const send = this._shadow.querySelector('.chat-send')
    if (input) input.disabled = on
    if (send) { send.disabled = on; send.textContent = on ? '…' : 'Send' }
  }

  // --- Send / stream -------------------------------------------------------

  _requestBody(message) {
    const body = {
      model: this.getAttribute('model') || DEFAULT_MODEL,
      message,
    }
    if (this._chatId) body.chat_id = this._chatId
    const boardId = this.getAttribute('board-id')
    if (boardId) body.board_id = boardId
    const mcpUrl = this.getAttribute('mcp-tools-url')
    if (mcpUrl) body.mcp_servers = [mcpUrl]
    return body
  }

  async _send() {
    if (this._streaming) return
    const input = this._shadow.querySelector('.chat-input')
    const message = (input.value || '').trim()
    if (!message) return

    this._clearError()
    input.value = ''
    this._appendMessage('user', message)

    this._abort()
    const ac = new AbortController()
    this._ac = ac
    this._setStreaming(true)

    // The assistant bubble grows as `token` frames arrive.
    const bubble = this._appendMessage('assistant', '')
    bubble.classList.add('streaming')

    try {
      const headers = {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      }
      const token = this.getAttribute('token')
      if (token) headers['Authorization'] = `Bearer ${token}`

      const resp = await fetch(this._endpoint(), {
        method: 'POST',
        headers,
        body: JSON.stringify(this._requestBody(message)),
        credentials: 'omit',
        signal: ac.signal,
      })

      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`)
      }

      await this._consumeStream(resp.body, bubble)
    } catch (err) {
      if (err.name !== 'AbortError') {
        this._showError(err.message || 'Request failed.')
      }
    } finally {
      bubble.classList.remove('streaming')
      // Drop an empty assistant bubble (e.g. a turn that only errored).
      if (!bubble.textContent) bubble.remove()
      this._setStreaming(false)
      if (this._ac === ac) this._ac = null
    }
  }

  /**
   * Read the SSE body stream, dispatching each parsed frame by `type`.
   *
   * @param {ReadableStream<Uint8Array>} stream
   * @param {HTMLElement} bubble — the streaming assistant bubble to append into.
   */
  async _consumeStream(stream, bubble) {
    const reader = stream.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const { events, rest } = parseSseBuffer(buffer)
      buffer = rest
      for (const evt of events) this._handleFrame(evt, bubble)
    }

    // Flush any complete frame left in the tail after the stream ends.
    const { events } = parseSseBuffer(buffer + '\n\n')
    for (const evt of events) this._handleFrame(evt, bubble)
  }

  /**
   * Dispatch a single parsed SSE frame.
   *
   *   token       {text}                  → append to the streaming bubble
   *   tool_use    {name, input}           → show a compact tool activity chip
   *   tool_result {output}                → show a tool activity chip
   *   message     {chat_id, message_id, spec?} → terminal: finalize the turn,
   *               store chat_id (server-side memory), emit nubi-chat:message
   *               (+ nubi-chat:spec when a spec is proposed)
   *   error       {message}               → surface an error banner
   */
  _handleFrame(evt, bubble) {
    if (!evt || typeof evt !== 'object') return
    switch (evt.type) {
      case 'token':
        if (typeof evt.text === 'string') {
          bubble.textContent += evt.text
          this._scrollToBottom()
        }
        break
      case 'tool_use':
        this._appendToolChip(evt.name || 'tool')
        break
      case 'tool_result':
        this._appendToolChip('result')
        break
      case 'message':
        this._finalizeTurn(evt, bubble)
        break
      case 'error':
        this._showError(evt.message || 'The assistant hit an error.')
        break
      default:
        // Unknown frame types are ignored for forward compatibility.
        break
    }
  }

  _finalizeTurn(evt, bubble) {
    // Store the server-side conversation id so the next turn continues it.
    if (evt.chat_id) this._chatId = evt.chat_id

    this.dispatchEvent(new CustomEvent('nubi-chat:message', {
      bubbles: true, composed: true,
      detail: {
        chat_id: evt.chat_id ?? this._chatId,
        message_id: evt.message_id,
        text: bubble.textContent,
        spec: evt.spec,
      },
    }))

    if (evt.spec) {
      this.dispatchEvent(new CustomEvent('nubi-chat:spec', {
        bubbles: true, composed: true,
        detail: { spec: evt.spec, chat_id: evt.chat_id ?? this._chatId },
      }))
    }
  }

  // --- Public API ----------------------------------------------------------

  /** The server-side conversation id, or null before the first turn. */
  get chatId() { return this._chatId }
}

export { NubiChat }
