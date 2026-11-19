/**
 * boardThumbnailCapture.ts — turn the board a writer is looking at into its
 * gallery card picture.
 *
 * The server can draw a board itself (an SVG render, see boardThumbnail.ts),
 * but that is a second renderer and never quite matches the live page. The
 * live page is already on this user's screen, so we capture its first screen
 * with the same engine the PNG export uses (lib/domCapture) and upload it;
 * the card prefers it over the render.
 *
 * It is a background nicety, so every step is silent: a board that cannot be
 * captured simply keeps its rendered picture.
 *
 * What makes a capture worth uploading, and so what `canCapture` checks:
 *   - a writer (the server refuses anyone else),
 *   - the light theme (one picture serves both themes, as a paper preview),
 *   - the board as everyone first sees it: first tab, no filter changed,
 *   - the same version the gallery shows (not a live version that differs
 *     from the draft the picture would be filed under).
 * On top of that the board must have finished loading, with no failed tile in
 * the part being captured.
 */
import { authedFetch } from './api.js'
import { forgetBoardThumbnail } from './boardThumbnail.js'

/** Output size: the card's own 16:9 frame, at the width the render uses. */
const OUT_WIDTH = 1000
const ASPECT = 16 / 9
/** How long the board must show no loading state before we call it settled. */
const QUIET_MS = 1500
const SETTLE_TIMEOUT_MS = 45_000
/** Must stay under the server's cap (export_share.py `_SHOT_MAX_BYTES`). */
const MAX_BYTES = 500 * 1024

export type CaptureConditions = {
  canWrite: boolean
  theme: string
  isEditing: boolean
  isEmbed: boolean
  /** True when the first tab (or a tabless board) is showing. */
  onFirstTab: boolean
  /** True when no filter value differs from the board's defaults. */
  filtersUntouched: boolean
  /** True when the spec on screen is the one the gallery card represents. */
  showsCardVersion: boolean
}

/** Pure gate: is what is on screen the picture the card should carry? */
export function canCapture(c: CaptureConditions): boolean {
  return (
    c.canWrite &&
    c.theme === 'light' &&
    !c.isEditing &&
    !c.isEmbed &&
    c.onFirstTab &&
    c.filtersUntouched &&
    c.showsCardVersion
  )
}

// One attempt per board version per page session, success or not.
const _attempted = new Set<string>()

/** Test seam. */
export function _resetCaptureAttempts() {
  _attempted.clear()
}

function isBusy(root: HTMLElement): boolean {
  if (root.querySelector('[aria-busy="true"], .animate-pulse, .nubi-skeleton, .nubi-skeleton-text, .nubi-shimmer')) return true
  return Array.from(root.querySelectorAll('img')).some((img) => !img.complete)
}

/** True when a failed tile falls inside the part of the board being captured. */
function showsFailedTile(root: HTMLElement, cropHeight: number): boolean {
  const limit = root.getBoundingClientRect().top + cropHeight
  return Array.from(root.querySelectorAll('[data-testid="widget-error"]'))
    .some((el) => el.getBoundingClientRect().top < limit)
}

/** Resolve true once the board has shown no loading state for QUIET_MS. */
function waitUntilSettled(root: HTMLElement, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    const started = Date.now()
    let quietSince: number | null = null
    const tick = () => {
      if (signal.aborted || !root.isConnected) return resolve(false)
      if (Date.now() - started > SETTLE_TIMEOUT_MS) return resolve(false)
      if (document.visibilityState !== 'visible' || isBusy(root)) {
        quietSince = null
      } else {
        quietSince ??= Date.now()
        if (Date.now() - quietSince >= QUIET_MS) return resolve(true)
      }
      setTimeout(tick, 300)
    }
    tick()
  })
}

function whenIdle(): Promise<void> {
  return new Promise((resolve) => {
    const ric = (window as any).requestIdleCallback as undefined | ((cb: () => void, o?: any) => void)
    if (ric) ric(() => resolve(), { timeout: 2000 })
    else setTimeout(resolve, 200)
  })
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality))
}

/** How long a screenshot stands before a writer's visit replaces it. */
const REFRESH_MS = 7 * 24 * 60 * 60 * 1000
const stampKey = (boardId: string) => `nubi-thumb-shot:${boardId}`

/** Did this browser upload a screenshot of the board within the last week? */
function recentlyCaptured(boardId: string): boolean {
  try {
    const at = Number(localStorage.getItem(stampKey(boardId)))
    return Number.isFinite(at) && at > 0 && Date.now() - at < REFRESH_MS
  } catch {
    return true   // no storage: never refresh, rather than refresh on every visit
  }
}

function markCaptured(boardId: string) {
  try { localStorage.setItem(stampKey(boardId), String(Date.now())) } catch { /* best effort */ }
}

/** Does the server already hold a screenshot of this version of the board? */
async function hasScreenshot(boardId: string): Promise<boolean> {
  const res = await authedFetch(`/boards/${boardId}/thumbnail?cached_only=1&theme=light`)
  return res.ok && res.headers.get('X-Nubi-Thumb-Kind') === 'shot'
}

/**
 * Capture the board's first screen and upload it, unless the server already
 * has a screenshot of this version that this browser refreshed within the last
 * week. Resolves true when a picture was uploaded.
 *
 * @param root      the board's root element (`[data-dashboard-root]`).
 * @param updatedAt the board row's `updated_at`; the server refuses the
 *   picture if the board has been saved since.
 */
export async function captureBoardThumbnail(
  { boardId, updatedAt, root, signal }:
  { boardId: string; updatedAt?: string | null; root: HTMLElement; signal: AbortSignal },
): Promise<boolean> {
  const attempt = `${boardId}|${updatedAt ?? ''}`
  if (_attempted.has(attempt)) return false
  try {
    if (!(await waitUntilSettled(root, signal))) return false
    // Claimed only now, so an attempt abandoned while the board was still
    // loading (navigating away, switching tab) can run again later.
    if (_attempted.has(attempt)) return false
    _attempted.add(attempt)

    // An existing screenshot is kept unless this browser last refreshed it
    // over a week ago (or never has): the picture shows data, and data ages.
    if (recentlyCaptured(boardId) && (await hasScreenshot(boardId))) return false
    await whenIdle()
    if (signal.aborted || !root.isConnected) return false

    const width = root.clientWidth
    if (width < 320) return false
    const cropHeight = Math.min(root.scrollHeight, Math.round(width / ASPECT))

    // A tile that failed to load is a fact about right now (a source that is
    // down, an expired credential), not about the board. Photographing it would
    // pin that on the card until the next edit; the rendered picture stays.
    if (showsFailedTile(root, cropHeight)) return false

    const { captureElement } = await import('./domCapture.js')
    // Scale 2, the same setting the PNG export uses: at scale 1 html2canvas
    // paints a card's box-shadow across the card's own interior. The extra
    // pixels also make for a cleaner downscale.
    const full = await captureElement(root, { scale: 2, cropHeight })
    if (signal.aborted) return false

    const out = document.createElement('canvas')
    out.width = Math.min(OUT_WIDTH, full.width)
    out.height = Math.round((out.width * full.height) / full.width)
    const ctx = out.getContext('2d')
    if (!ctx) return false
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(full, 0, 0, out.width, out.height)

    // WebP where the browser can encode it (Safari hands back a PNG instead).
    let blob = await toBlob(out, 'image/webp', 0.82)
    if (blob && blob.size > MAX_BYTES) blob = await toBlob(out, 'image/webp', 0.6)
    if (!blob || blob.size > MAX_BYTES || !/^image\/(webp|png)$/.test(blob.type)) return false

    const query = updatedAt ? `?updated_at=${encodeURIComponent(updatedAt)}` : ''
    const res = await authedFetch(`/boards/${boardId}/thumbnail${query}`, {
      method: 'PUT',
      headers: { 'Content-Type': blob.type },
      body: blob,
      signal,
    })
    if (!res.ok) return false
    markCaptured(boardId)
    await forgetBoardThumbnail(boardId)
    return true
  } catch {
    return false
  }
}
