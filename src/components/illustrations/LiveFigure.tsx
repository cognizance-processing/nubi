/**
 * LiveFigure — mounts one of the interactive figures (figures/*.js) and tears it
 * down on unmount. Marked `live` so the docs renderer gives it the full content
 * width instead of the illustration frame.
 *
 * Nothing about a figure (its markup, CSS, three.js) is imported statically. The
 * loader runs once the placeholder is within 400px of the viewport, so a page
 * that merely *contains* a live figure (docs, chat markdown, the gallery, auth)
 * pays for none of it until the figure is about to be seen. Each figure then
 * pauses itself offscreen and on hidden tabs, and idles when settled
 * (see figures/figureRuntime.js).
 */
import { useEffect, useRef, useState } from 'react'

type Loaded = { markup: string; mount: (root: HTMLElement) => () => void }

export function makeLiveFigure(load: () => Promise<Loaded>, label: string, reserve = 520) {
  function Figure() {
    const ref = useRef<HTMLDivElement>(null)
    const mountRef = useRef<Loaded['mount'] | null>(null)
    const [html, setHtml] = useState<string | null>(null)

    // 1. near the viewport -> load code, CSS and markup
    useEffect(() => {
      const el = ref.current
      if (!el) return
      let dead = false
      const start = () => {
        load()
          .then(m => { if (!dead) { mountRef.current = m.mount; setHtml(m.markup) } })
          .catch(e => console.warn(`[docs] ${label} figure failed`, e))
      }
      if (typeof IntersectionObserver === 'undefined') { start(); return () => { dead = true } }
      const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { io.disconnect(); start() } }, { rootMargin: '400px' })
      io.observe(el)
      return () => { dead = true; io.disconnect() }
    }, [])

    // 2. markup is in the DOM -> wire the figure to it
    useEffect(() => {
      const el = ref.current
      if (html == null || !el || !mountRef.current) return
      let stop: (() => void) | undefined
      try { stop = mountRef.current(el) } catch (e) { console.warn(`[docs] ${label} figure failed`, e) }
      return () => { stop?.() }
    }, [html])

    return (
      <div
        ref={ref}
        className="nfig"
        role="group"
        aria-label={label}
        style={html == null ? { minHeight: reserve } : undefined}
        dangerouslySetInnerHTML={html == null ? undefined : { __html: html }}
      />
    )
  }
  Figure.live = true
  Figure.displayName = label.replace(/\s+/g, '')
  return Figure
}
