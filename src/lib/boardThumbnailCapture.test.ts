// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const authedFetch = vi.fn()
const captureElement = vi.fn()
const forgetBoardThumbnail = vi.fn()
vi.mock('./api.js', () => ({ authedFetch: (...a: unknown[]) => authedFetch(...a) }))
vi.mock('./domCapture.js', () => ({ captureElement: (...a: unknown[]) => captureElement(...a) }))
vi.mock('./boardThumbnail.js', () => ({ forgetBoardThumbnail: (...a: unknown[]) => forgetBoardThumbnail(...a) }))

import { canCapture, captureBoardThumbnail, _resetCaptureAttempts } from './boardThumbnailCapture.js'

const OK = {
  canWrite: true, theme: 'light', isEditing: false, isEmbed: false,
  onFirstTab: true, filtersUntouched: true, showsCardVersion: true,
}

describe('canCapture', () => {
  it('allows the board as everyone first sees it', () => {
    expect(canCapture(OK)).toBe(true)
  })

  it.each([
    ['a viewer', { canWrite: false }],
    ['the dark theme', { theme: 'dark' }],
    ['the editor', { isEditing: true }],
    ['an embed', { isEmbed: true }],
    ['another tab', { onFirstTab: false }],
    ['a changed filter', { filtersUntouched: false }],
    ['a live version that differs from the draft', { showsCardVersion: false }],
  ])('refuses %s', (_name, patch) => {
    expect(canCapture({ ...OK, ...patch })).toBe(false)
  })
})

describe('captureBoardThumbnail', () => {
  let root: HTMLElement

  const response = (kind: string | null, ok = true) => ({
    ok,
    headers: { get: (h: string) => (h === 'X-Nubi-Thumb-Kind' ? kind : null) },
  })

  beforeEach(() => {
    vi.useFakeTimers()
    _resetCaptureAttempts()
    localStorage.clear()
    authedFetch.mockReset()
    captureElement.mockReset()
    forgetBoardThumbnail.mockReset()
    root = document.createElement('div')
    document.body.appendChild(root)
    Object.defineProperty(root, 'clientWidth', { value: 1600 })
    Object.defineProperty(root, 'scrollHeight', { value: 4000 })
    const blob = new Blob(['x'], { type: 'image/webp' })
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as any
    HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) { cb(blob) }
    captureElement.mockResolvedValue({ width: 1600, height: 900 })
  })

  afterEach(() => {
    root.remove()
    vi.useRealTimers()
  })

  const run = async (signal = new AbortController().signal) => {
    const p = captureBoardThumbnail({ boardId: 'b1', updatedAt: 'v1', root, signal })
    await vi.advanceTimersByTimeAsync(5000)
    return p
  }

  it('uploads the first screen when the server has no screenshot', async () => {
    authedFetch.mockResolvedValueOnce(response(null))
    expect(await run()).toBe(true)
    expect(captureElement).toHaveBeenCalledWith(root, { scale: 2, cropHeight: 900 })
    const [path, init] = authedFetch.mock.calls[0]
    expect(path).toBe('/boards/b1/thumbnail?updated_at=v1')
    expect(init.method).toBe('PUT')
    expect(forgetBoardThumbnail).toHaveBeenCalledWith('b1')
  })

  it('replaces a screenshot this browser has not refreshed in a week', async () => {
    localStorage.setItem('nubi-thumb-shot:b1', String(Date.now() - 8 * 24 * 60 * 60 * 1000))
    authedFetch.mockResolvedValueOnce(response(null))
    expect(await run()).toBe(true)
    expect(authedFetch.mock.calls[0][1].method).toBe('PUT')
  })

  it('does nothing when a recent screenshot already exists', async () => {
    localStorage.setItem('nubi-thumb-shot:b1', String(Date.now()))
    authedFetch.mockResolvedValueOnce(response('shot'))
    expect(await run()).toBe(false)
    expect(captureElement).not.toHaveBeenCalled()
  })

  it('tries a board version only once', async () => {
    localStorage.setItem('nubi-thumb-shot:b1', String(Date.now()))
    authedFetch.mockResolvedValue(response('shot'))
    await run()
    await run()
    expect(authedFetch).toHaveBeenCalledTimes(1)
  })

  it('waits while the board is still loading', async () => {
    root.innerHTML = '<div aria-busy="true"></div>'
    const ctl = new AbortController()
    const p = captureBoardThumbnail({ boardId: 'b1', updatedAt: 'v1', root, signal: ctl.signal })
    await vi.advanceTimersByTimeAsync(5000)
    expect(authedFetch).not.toHaveBeenCalled()
    ctl.abort()
    await vi.advanceTimersByTimeAsync(500)
    expect(await p).toBe(false)
  })

  it('does not photograph a board showing a failed tile', async () => {
    root.innerHTML = '<div data-testid="widget-error"></div>'
    expect(await run()).toBe(false)
    expect(captureElement).not.toHaveBeenCalled()
    expect(authedFetch).not.toHaveBeenCalled()
  })

  it('keeps the rendered picture when the upload is refused', async () => {
    authedFetch.mockResolvedValueOnce(response(null, false))
    expect(await run()).toBe(false)
    expect(forgetBoardThumbnail).not.toHaveBeenCalled()
  })
})
