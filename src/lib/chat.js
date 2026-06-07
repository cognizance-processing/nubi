/**
 * chat.js — Streaming chat client for the dashboard-editor ChatPanel.
 *
 * Talks to the `/chat/*` endpoints. Reuses the same base URL + auth token
 * conventions as src/lib/api.js (in-memory Bearer token, X-Org-Id header,
 * credentials:'include' so the HttpOnly refresh cookie rides along).
 *
 * The streaming endpoint returns Server-Sent Events; we read the response
 * body as a stream and parse each `data:` line into a JSON event, invoking
 * onEvent(evt) per event. Cancellation is supported via an AbortSignal.
 *
 * Event types (each one JSON object on a `data:` line):
 *   { type:'token', text }                      → assistant text delta
 *   { type:'tool_use', id, name, input }        → a tool call begins
 *   { type:'tool_result', id, output }          → result for that tool call
 *   { type:'message', chat_id, message_id }     → turn complete
 *   { type:'error', message }                   → error
 */

import { get, getAccessToken } from './api.js'

// Mirror api.js base URL resolution so dev (Vite proxy) and prod both work.
const _backendUrl = import.meta.env.VITE_BACKEND_URL ?? ''
const BASE = (import.meta.env.DEV || !_backendUrl) ? '/api/v1' : _backendUrl + '/api/v1'

// ---------------------------------------------------------------------------
// streamChat — POST /chat/stream, consume the SSE response
// ---------------------------------------------------------------------------

/**
 * Open a streaming chat turn. Resolves when the stream closes; rejects on a
 * transport/HTTP error. Aborting the signal closes the stream and resolves
 * (the AbortError is swallowed so the Stop button is a clean no-throw stop).
 *
 * @param {{
 *   chatId?: string | null,
 *   boardId?: string | null,
 *   model: string,
 *   message: string,
 *   signal?: AbortSignal,
 *   onEvent: (evt: any) => void,
 * }} args
 * @returns {Promise<void>}
 */
export async function streamChat({ chatId, boardId, model, message, signal, onEvent }) {
  const headers = new Headers({
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  })
  const token = getAccessToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const body = JSON.stringify({
    chat_id: chatId ?? undefined,
    board_id: boardId ?? undefined,
    model,
    message,
  })

  let response
  try {
    response = await fetch(`${BASE}/chat/stream`, {
      method: 'POST',
      headers,
      body,
      credentials: 'include',
      signal,
    })
  } catch (err) {
    if (err?.name === 'AbortError') return
    throw err
  }

  if (!response.ok || !response.body) {
    let payload
    try { payload = await response.json() } catch { payload = null }
    const err = new Error(
      payload?.error?.message ?? payload?.detail ??
      `Chat request failed: ${response.status} ${response.statusText}`,
    )
    err.status = response.status
    throw err
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE events are separated by a blank line.
      let sep
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const rawEvent = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        dispatch(rawEvent, onEvent)
      }
    }
    // Flush any trailing event that wasn't terminated by a blank line.
    if (buffer.trim()) dispatch(buffer, onEvent)
  } catch (err) {
    // A user-initiated abort surfaces here as an AbortError — treat it as a
    // clean stop rather than an error to surface in the UI.
    if (err?.name === 'AbortError') return
    throw err
  }
}

/**
 * Parse one raw SSE event block (possibly multiple `data:` lines) and, if it
 * carries JSON, invoke onEvent with the parsed object.
 */
function dispatch(rawEvent, onEvent) {
  const dataLines = rawEvent
    .split('\n')
    .filter(l => l.startsWith('data:'))
    .map(l => l.slice(5).trimStart())
  if (dataLines.length === 0) return
  const json = dataLines.join('\n')
  if (!json || json === '[DONE]') return
  let parsed
  try { parsed = JSON.parse(json) } catch { return /* ignore malformed event */ }
  onEvent?.(parsed)
}

// ---------------------------------------------------------------------------
// Thin GET helpers
// ---------------------------------------------------------------------------

/**
 * List the models available for the picker.
 * @returns {Promise<Array<{ id: string, label: string }>>}
 */
export async function listChatModels() {
  try {
    const data = await get('/chat/models')
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.models)) return data.models
    return []
  } catch (err) {
    console.warn('[chat] listChatModels failed; returning []:', err.message)
    return []
  }
}

/**
 * List past conversations for a board (board-scoped history).
 * @param {string | null} boardId
 * @returns {Promise<Array<{ id: string, title: string, updated_at: string }>>}
 */
export async function listConversations(boardId) {
  try {
    const qs = boardId ? `?board_id=${encodeURIComponent(boardId)}` : ''
    const data = await get(`/chat/conversations${qs}`)
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.conversations)) return data.conversations
    return []
  } catch (err) {
    console.warn('[chat] listConversations failed; returning []:', err.message)
    return []
  }
}

/**
 * Fetch a single conversation with its full message history.
 * @param {string} id
 * @returns {Promise<{ id: string, title: string, messages: Array<{ role: string, content: string, created_at: string }> }>}
 */
export function getConversation(id) {
  return get(`/chat/conversations/${encodeURIComponent(id)}`)
}
