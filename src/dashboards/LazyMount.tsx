/**
 * LazyMount.tsx — mount a board widget only once it is near the screen.
 *
 * A tab can hold many tables and charts, and before this every one of them
 * started its query the moment the tab opened, including ones far below the
 * fold. LazyMount renders an empty placeholder of the same grid size and swaps
 * in the real widget (which then fetches) when it comes within
 * LAZY_ROOT_MARGIN of the screen. Once mounted it stays mounted.
 *
 * Whole-board PNG/PDF exports and printing need every widget drawn, so:
 *   - prepareBoardForCapture() (lazyCapture.ts) mounts everything and waits;
 *   - a `beforeprint` event mounts everything (best effort).
 * Browsers without IntersectionObserver mount immediately (old behaviour).
 */

import { useEffect, useRef, useState } from 'react'
import { LAZY_ROOT_MARGIN, MOUNT_ALL_WIDGETS_EVENT } from './lazyWidgets.js'
import { lazyWidgetMounted, lazyWidgetWaiting } from './lazyCapture.js'

export default function LazyMount({ children }: { children: any }) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [shown, setShown] = useState(
    () => typeof window === 'undefined' || typeof IntersectionObserver === 'undefined',
  )

  useEffect(() => {
    if (shown) return
    lazyWidgetWaiting()
    let done = false
    const show = () => {
      if (done) return
      done = true
      lazyWidgetMounted()
      setShown(true)
    }
    const io = new IntersectionObserver(
      (entries) => { if (entries.some((e) => e.isIntersecting)) show() },
      { rootMargin: LAZY_ROOT_MARGIN },
    )
    if (ref.current) io.observe(ref.current)
    window.addEventListener(MOUNT_ALL_WIDGETS_EVENT, show)
    window.addEventListener('beforeprint', show)
    return () => {
      io.disconnect()
      window.removeEventListener(MOUNT_ALL_WIDGETS_EVENT, show)
      window.removeEventListener('beforeprint', show)
      if (!done) {
        done = true
        lazyWidgetMounted()
      }
    }
  }, [shown])

  if (shown) return children
  return <div ref={ref} className="w-full h-full" aria-busy="true" data-lazy-widget="" />
}
