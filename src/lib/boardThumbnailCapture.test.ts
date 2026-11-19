/**
 * boardThumbnailCapture.test.ts — when a board's card picture is taken.
 *
 * Run with:
 *   node --import tsx --test src/lib/boardThumbnailCapture.test.ts
 */
import { describe, it, beforeEach, afterEach, mock } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/', pretendToBeVisual: true })
const g = globalThis as any
for (const k of ['window', 'document', 'localStorage', 'HTMLElement', 'HTMLCanvasElement', 'Element']) {
  g[k] = (dom.window as any)[k]
}

const { canCapture, captureBoardThumbnail, _resetCaptureAttempts, _setCaptureDeps } =
  await import('./boardThumbnailCapture.js')

const OK = {
  canWrite: true, theme: 'light', isEditing: false, isEmbed: false,
  onFirstTab: true, filtersUntouched: true, showsCardVersion: true,
}

describe('canCapture', () => {
  it('allows the board as everyone first sees it', () => {
    assert.equal(canCapture(OK), true)
  })

  for (const [name, patch] of [
    ['a viewer', { canWrite: false }],
    ['the dark theme', { theme: 'dark' }],
    ['the editor', { isEditing: true }],
    ['an embed', { isEmbed: true }],
    ['another tab', { onFirstTab: false }],
    ['a changed filter', { filtersUntouched: false }],
    ['a live version that differs from the draft', { showsCardVersion: false }],
  ] as const) {
    it(`refuses ${name}`, () => {
      assert.equal(canCapture({ ...OK, ...patch }), false)
    })
  }
})

describe('captureBoardThumbnail', () => {
  let root: HTMLElement
  let authedFetch: ReturnType<typeof mock.fn>
  let captureElement: ReturnType<typeof mock.fn>
  let forgetBoardThumbnail: ReturnType<typeof mock.fn>

  const response = (kind: string | null, ok = true) => ({
    ok,
    headers: { get: (h: string) => (h === 'X-Nubi-Thumb-Kind' ? kind : null) },
  })

  /** Advance the mocked clock in small steps, letting promises run between. */
  const advance = async (ms: number) => {
    for (let t = 0; t < ms; t += 100) {
      mock.timers.tick(100)
      await new Promise((r) => setImmediate(r))
    }
  }

  beforeEach(() => {
    mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() })
    _resetCaptureAttempts()
    localStorage.clear()
    authedFetch = mock.fn()
    captureElement = mock.fn(async () => ({ width: 1600, height: 900 }))
    forgetBoardThumbnail = mock.fn(async () => {})
    _setCaptureDeps({ authedFetch, captureElement, forgetBoardThumbnail } as any)
    root = document.createElement('div')
    document.body.appendChild(root)
    Object.defineProperty(root, 'clientWidth', { value: 1600 })
    Object.defineProperty(root, 'scrollHeight', { value: 4000 })
    const blob = { size: 1, type: 'image/webp' }
    HTMLCanvasElement.prototype.getContext = (() => ({ drawImage() {} })) as any
    HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) { cb(blob as Blob) }
  })

  afterEach(() => {
    root.remove()
    mock.timers.reset()
  })

  const run = async (signal = new AbortController().signal) => {
    const p = captureBoardThumbnail({ boardId: 'b1', updatedAt: 'v1', root, signal })
    await advance(5000)
    return p
  }

  it('uploads the first screen when the server has no screenshot', async () => {
    authedFetch.mock.mockImplementationOnce(async () => response(null))
    assert.equal(await run(), true)
    assert.deepEqual(captureElement.mock.calls[0].arguments, [root, { scale: 2, cropHeight: 900 }])
    const [path, init] = authedFetch.mock.calls[0].arguments as [string, RequestInit]
    assert.equal(path, '/boards/b1/thumbnail?updated_at=v1')
    assert.equal(init.method, 'PUT')
    assert.deepEqual(forgetBoardThumbnail.mock.calls[0].arguments, ['b1'])
  })

  it('replaces a screenshot this browser has not refreshed in a week', async () => {
    localStorage.setItem('nubi-thumb-shot:b1', String(Date.now() - 8 * 24 * 60 * 60 * 1000))
    authedFetch.mock.mockImplementationOnce(async () => response(null))
    assert.equal(await run(), true)
    assert.equal((authedFetch.mock.calls[0].arguments[1] as RequestInit).method, 'PUT')
  })

  it('does nothing when a recent screenshot already exists', async () => {
    localStorage.setItem('nubi-thumb-shot:b1', String(Date.now()))
    authedFetch.mock.mockImplementationOnce(async () => response('shot'))
    assert.equal(await run(), false)
    assert.equal(captureElement.mock.callCount(), 0)
  })

  it('tries a board version only once', async () => {
    localStorage.setItem('nubi-thumb-shot:b1', String(Date.now()))
    authedFetch.mock.mockImplementation(async () => response('shot'))
    await run()
    await run()
    assert.equal(authedFetch.mock.callCount(), 1)
  })

  it('waits while the board is still loading', async () => {
    root.innerHTML = '<div aria-busy="true"></div>'
    const ctl = new AbortController()
    const p = captureBoardThumbnail({ boardId: 'b1', updatedAt: 'v1', root, signal: ctl.signal })
    await advance(5000)
    assert.equal(authedFetch.mock.callCount(), 0)
    ctl.abort()
    await advance(500)
    assert.equal(await p, false)
  })

  it('ignores tiles still waiting below the captured slice', async () => {
    root.innerHTML = '<div aria-busy="true" data-lazy-widget=""></div>'
    const below = root.firstElementChild as HTMLElement
    below.getBoundingClientRect = () => ({ top: 2000 } as DOMRect)
    authedFetch.mock.mockImplementationOnce(async () => response(null))
    assert.equal(await run(), true)
    assert.equal(captureElement.mock.callCount(), 1)
  })

  it('does not photograph a board showing a failed tile', async () => {
    root.innerHTML = '<div data-testid="widget-error"></div>'
    assert.equal(await run(), false)
    assert.equal(captureElement.mock.callCount(), 0)
    assert.equal(authedFetch.mock.callCount(), 0)
  })

  it('keeps the rendered picture when the upload is refused', async () => {
    authedFetch.mock.mockImplementationOnce(async () => response(null, false))
    assert.equal(await run(), false)
    assert.equal(forgetBoardThumbnail.mock.callCount(), 0)
  })
})
