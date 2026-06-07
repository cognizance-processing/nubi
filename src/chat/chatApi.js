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

/**
 * Send the full conversation history to the AI chat endpoint.
 *
 * @param {{
 *   messages: Array<{ role: 'user' | 'assistant', content: string }>,
 *   model?: string,
 *   board_id?: string,
 * }} params
 *
 * @returns {Promise<{
 *   reply: string,
 *   actions: Array<{ tool: string, arguments: Record<string, any>, result: Record<string, any> }>,
 *   model: string | null,
 * }>}
 */
export async function sendChatMessage({ messages, model, board_id }) {
  const body = { messages }
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
 *
 * @param {{
 *   messages: Array<{ role: 'user' | 'assistant', content: string }>,
 *   model?: string,
 *   board_id?: string,
 *   onEvent: (ev: any) => void,
 *   signal?: AbortSignal,
 * }} params
 * @returns {Promise<void>} resolves when the stream closes
 */
export async function streamChatMessage({ messages, model, board_id, onEvent, signal }) {
  const body = { messages }
  if (model) body.model = model
  if (board_id) body.board_id = board_id
  return postStream('/ai/chat/stream', body, { onEvent, signal })
}
