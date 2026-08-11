/**
 * chatApi — thin client for POST /api/v1/ai/chat.
 *
 * Delegates auth (Bearer token + silent refresh) to the shared api.js wrapper.
 *
 * Usage:
 *   import { sendChatMessage } from './chatApi'
 *
 *   const { reply, actions, model } = await sendChatMessage({
 *     messages: [{ role: 'user', content: 'Hello' }],
 *     model: 'claude',         // optional
 *     board_id: 'abc123',      // optional
 *   })
 *
 * Shape of each `action` in the returned array:
 *   { tool: string, arguments: object, result: object }
 */

import { post, postStream } from '../lib/api.js'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatAction {
  tool: string
  arguments: Record<string, any>
  result: Record<string, any>
}

export interface ChatReply {
  reply: string
  actions: ChatAction[]
  model: string | null
}

/** Send the full conversation history to the AI chat endpoint. */
export async function sendChatMessage({ messages, model, board_id }: {
  messages: ChatMessage[]
  model?: string
  board_id?: string
}): Promise<ChatReply> {
  const body: Record<string, any> = { messages }
  if (model) body.model = model
  if (board_id) body.board_id = board_id
  return post('/ai/chat', body)
}

/**
 * Streaming variant — POST /api/v1/ai/chat/stream and invoke `onEvent` with
 * each live event as it arrives (Claude-Code-style tool streaming).
 *
 * Event types (see backend agent.run_agent_stream):
 *   { type: 'status',      text }
 *   { type: 'tool_start',  id, tool, arguments }
 *   { type: 'tool_result', id, tool, ok, result }
 *   { type: 'text',        delta }
 *   { type: 'done',        reply, actions }
 *   { type: 'error',       message }
 */
export async function streamChatMessage({ messages, model, board_id, onEvent, signal }: {
  messages: ChatMessage[]
  model?: string
  board_id?: string
  onEvent: (ev: any) => void
  signal?: AbortSignal
}): Promise<void> {
  const body: Record<string, any> = { messages }
  if (model) body.model = model
  if (board_id) body.board_id = board_id
  return postStream('/ai/chat/stream', body, { onEvent, signal })
}

export interface PinAnswerBody {
  title: string
  source: { query_id?: string; metric_id?: string }
  viz: { type: 'kpi' | 'table' | 'chart'; chart_type?: string; encoding?: object }
  board_id?: string
}

/**
 * Pin a chat answer (a query result or generated spec) to a dashboard.
 *
 * POST /api/v1/ai/pin
 *
 * Reuses the shared `post()` helper (Bearer token + X-Org-Id + silent 401
 * refresh). On a 400 the thrown error carries `.status === 400` and
 * `.payload` (structured validation errors) so the caller can surface them
 * inline.
 */
export async function pinAnswer(body: PinAnswerBody): Promise<{
  board_id: string
  widget_id: string
  spec: object
  valid: boolean
}> {
  return post('/ai/pin', body)
}
