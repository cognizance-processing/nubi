/**
 * lazyCapture.ts — bookkeeping for LazyMount, and the helper a whole-board
 * PNG/PDF export calls so deferred widgets are drawn with real data first.
 */

import { MOUNT_ALL_WIDGETS_EVENT } from './lazyWidgets.js'
import { waitForQueriesIdle } from '../lib/wasmRuntime.js'

let _pending = 0

/** A deferred widget placeholder appeared. */
export function lazyWidgetWaiting(): void {
  _pending += 1
}

/** A deferred widget mounted (or its placeholder went away). */
export function lazyWidgetMounted(): void {
  _pending = Math.max(0, _pending - 1)
}

/** Number of deferred widgets on screen that have not mounted yet. */
export function pendingLazyWidgetCount(): number {
  return _pending
}

function nextFrame(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve())
    else setTimeout(resolve, 16)
  })
}

/**
 * Make every deferred widget mount and wait (up to timeoutMs) for their queries,
 * so a whole-board capture shows real data instead of empty placeholders.
 * A no-op when nothing is deferred.
 */
export async function prepareBoardForCapture(timeoutMs = 60000): Promise<void> {
  if (_pending === 0 || typeof window === 'undefined') return
  window.dispatchEvent(new Event(MOUNT_ALL_WIDGETS_EVENT))
  // Let React commit the newly mounted widgets so their fetches start.
  await nextFrame()
  await nextFrame()
  await waitForQueriesIdle(timeoutMs)
  // Let the widgets render the data (and charts finish their entry animation).
  await nextFrame()
  await new Promise((resolve) => setTimeout(resolve, 600))
}
