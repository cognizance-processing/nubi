/**
 * nubi-chat.test.js — Unit tests for <nubi-chat> (streaming chat widget).
 *
 * Mocks fetch() with a streamed SSE ReadableStream body and asserts the element
 * (a) renders a user message on send, (b) appends streamed `token` text to the
 * assistant bubble, (c) stores `chat_id` from the terminal `message` frame and
 * replays it on the next turn, and (d) surfaces an `error` frame.
 *
 * Run with:
 *   npm run test:embed
 *   # or: npx vitest run embed/__tests__/nubi-chat.test.js
 */

import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, unmount, nextTick } from './helpers.js'

// Register the widget under test directly (avoid pulling echarts via the barrel).
import { NubiChat } from '../widgets/nubi-chat.js'
if (!customElements.get('nubi-chat')) customElements.define('nubi-chat', NubiChat)

// ---------------------------------------------------------------------------
// SSE stream mock helpers
// ---------------------------------------------------------------------------

/** Serialise a list of frame objects into an SSE wire string. */
function sseWire(frames) {
  return frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('')
}

/** A fetch Response stub whose body is a ReadableStream of the given SSE text. */
function streamResponse(text, { ok = true, status = 200 } = {}): any {
  const encoder = new TextEncoder()
  const body = new ReadableStream({
    start(controller) {
      // Emit in two chunks to exercise the buffering across reads.
      const mid = Math.floor(text.length / 2)
      controller.enqueue(encoder.encode(text.slice(0, mid)))
      controller.enqueue(encoder.encode(text.slice(mid)))
      controller.close()
    },
  })
  return { ok, status, body }
}

function makeChat(attrs: Record<string, string> = {}): any {
  const el = document.createElement('nubi-chat')
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
  return el
}

function typeAndSend(el, text) {
  const input = el.shadowRoot.querySelector('.chat-input')
  input.value = text
  el.shadowRoot.querySelector('.chat-input-row').dispatchEvent(
    new Event('submit', { bubbles: true, cancelable: true })
  )
}

// ---------------------------------------------------------------------------
// Scaffold
// ---------------------------------------------------------------------------

describe('<nubi-chat> — scaffold', () => {
  let el
  beforeEach(() => { el = makeChat({ theme: 'dark' }); mount(el) })
  afterEach(() => unmount(el))

  test('renders the chat UI into shadow DOM', () => {
    expect(el.shadowRoot).toBeTruthy()
    expect(el.shadowRoot.querySelector('.chat-wrap')).toBeTruthy()
    expect(el.shadowRoot.querySelector('.chat-log')).toBeTruthy()
    expect(el.shadowRoot.querySelector('.chat-input')).toBeTruthy()
    expect(el.shadowRoot.querySelector('.chat-send')).toBeTruthy()
  })

  test('message log is an aria-live region for streamed text', () => {
    const log = el.shadowRoot.querySelector('.chat-log')
    expect(log.getAttribute('aria-live')).toBe('polite')
    expect(log.getAttribute('role')).toBe('log')
  })

  test('observedAttributes exposes the documented attributes', () => {
    const attrs = (customElements.get('nubi-chat') as any).observedAttributes
    for (const a of ['endpoint', 'token', 'model', 'board-id', 'mcp-tools-url', 'placeholder', 'height']) {
      expect(attrs).toContain(a)
    }
  })
})

// ---------------------------------------------------------------------------
// Streaming a turn
// ---------------------------------------------------------------------------

describe('<nubi-chat> — streaming', () => {
  let el
  afterEach(() => { if (el) unmount(el); vi.restoreAllMocks() })

  test('renders a user message on send and appends streamed token text', async () => {
    const wire = sseWire([
      { type: 'token', text: 'Hello' },
      { type: 'token', text: ', world' },
      { type: 'message', chat_id: 'c-1', message_id: 'm-1' },
    ])
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamResponse(wire))

    el = makeChat()
    mount(el)
    typeAndSend(el, 'hi there')
    await nextTick(6)

    const user = el.shadowRoot.querySelector('.chat-msg.user')
    expect(user).toBeTruthy()
    expect(user.textContent).toBe('hi there')

    const assistant = el.shadowRoot.querySelector('.chat-msg.assistant')
    expect(assistant).toBeTruthy()
    expect(assistant.textContent).toBe('Hello, world')
  })

  test('shows a tool-activity chip for tool_use frames', async () => {
    const wire = sseWire([
      { type: 'tool_use', name: 'run_query', input: { q: 'x' } },
      { type: 'token', text: 'done' },
      { type: 'message', chat_id: 'c-2', message_id: 'm-2' },
    ])
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamResponse(wire))

    el = makeChat()
    mount(el)
    typeAndSend(el, 'query please')
    await nextTick(6)

    const chip = el.shadowRoot.querySelector('.chat-tool-chip')
    expect(chip).toBeTruthy()
    expect(chip.textContent).toBe('run_query')
  })

  test('stores chat_id from the terminal message and replays it on the next turn', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(streamResponse(sseWire([
        { type: 'token', text: 'first' },
        { type: 'message', chat_id: 'c-42', message_id: 'm-1' },
      ])))
      .mockResolvedValueOnce(streamResponse(sseWire([
        { type: 'token', text: 'second' },
        { type: 'message', chat_id: 'c-42', message_id: 'm-2' },
      ])))

    el = makeChat()
    mount(el)

    typeAndSend(el, 'turn one')
    await nextTick(6)
    expect(el.chatId).toBe('c-42')

    // First request carries no chat_id.
    const body1 = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body1.chat_id).toBeUndefined()
    expect(body1.message).toBe('turn one')

    typeAndSend(el, 'turn two')
    await nextTick(6)

    // Second request replays the stored chat_id (server-side memory).
    const body2 = JSON.parse(fetchMock.mock.calls[1][1].body as string)
    expect(body2.chat_id).toBe('c-42')
    expect(body2.message).toBe('turn two')
  })

  test('emits nubi-chat:message on a finalized turn', async () => {
    const wire = sseWire([
      { type: 'token', text: 'answer' },
      { type: 'message', chat_id: 'c-9', message_id: 'm-9' },
    ])
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamResponse(wire))

    el = makeChat()
    const events = []
    el.addEventListener('nubi-chat:message', (e) => events.push(e.detail))
    mount(el)
    typeAndSend(el, 'q')
    await nextTick(6)

    expect(events.length).toBe(1)
    expect(events[0].chat_id).toBe('c-9')
    expect(events[0].message_id).toBe('m-9')
    expect(events[0].text).toBe('answer')
  })

  test('emits nubi-chat:spec when a dashboard spec is proposed', async () => {
    const spec = { charts: [{ type: 'bar' }] }
    const wire = sseWire([
      { type: 'token', text: 'here is a dashboard' },
      { type: 'message', chat_id: 'c-s', message_id: 'm-s', spec },
    ])
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamResponse(wire))

    el = makeChat()
    const specs = []
    el.addEventListener('nubi-chat:spec', (e) => specs.push(e.detail))
    mount(el)
    typeAndSend(el, 'build me a dashboard')
    await nextTick(6)

    expect(specs.length).toBe(1)
    expect(specs[0].spec).toEqual(spec)
    expect(specs[0].chat_id).toBe('c-s')
  })

  test('sends the default model and honours an overridden endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(streamResponse(sseWire([
        { type: 'message', chat_id: 'c', message_id: 'm' },
      ])))

    el = makeChat({ endpoint: '/proxy/chat', model: 'claude-opus-4-8' })
    mount(el)
    typeAndSend(el, 'ping')
    await nextTick(6)

    expect(fetchMock.mock.calls[0][0]).toBe('/proxy/chat')
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body.model).toBe('claude-opus-4-8')
    const headers = fetchMock.mock.calls[0][1].headers
    expect(headers['Accept']).toBe('text/event-stream')
  })
})

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

describe('<nubi-chat> — errors', () => {
  let el
  afterEach(() => { if (el) unmount(el); vi.restoreAllMocks() })

  test('surfaces an error frame in the error banner', async () => {
    const wire = sseWire([
      { type: 'error', message: 'model unavailable' },
    ])
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamResponse(wire))

    el = makeChat()
    mount(el)
    typeAndSend(el, 'trigger error')
    await nextTick(6)

    const banner = el.shadowRoot.querySelector('.chat-error')
    expect(banner.classList.contains('visible')).toBe(true)
    expect(banner.textContent).toContain('model unavailable')
  })

  test('surfaces a transport (HTTP) failure in the error banner', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamResponse('', { ok: false, status: 500 }))

    el = makeChat()
    mount(el)
    typeAndSend(el, 'boom')
    await nextTick(6)

    const banner = el.shadowRoot.querySelector('.chat-error')
    expect(banner.classList.contains('visible')).toBe(true)
    expect(banner.textContent).toContain('500')
    // The empty assistant bubble is dropped on failure.
    expect(el.shadowRoot.querySelector('.chat-msg.assistant')).toBeFalsy()
  })
})
