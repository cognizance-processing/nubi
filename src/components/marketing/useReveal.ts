/**
 * useReveal — one-shot scroll-reveal for marketing sections. Pair with the
 * .lp-reveal / .lp-in classes from MarketingStyles.
 *   const [ref, seen] = useReveal()
 *   <div ref={ref} className={`lp-reveal ${seen ? 'lp-in' : ''}`}>
 *
 * Content is never hidden from people who asked for less motion: under
 * `prefers-reduced-motion: reduce` (checked live), or without IntersectionObserver,
 * `seen` is true from the first render and nothing waits for a scroll.
 */
import { useState, useEffect, useRef, type RefObject } from 'react'

const RM_QUERY = '(prefers-reduced-motion: reduce)'
const reducedNow = () => typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(RM_QUERY).matches

export default function useReveal<T extends HTMLElement = HTMLDivElement>(): [RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null)
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined' || reducedNow())
  useEffect(() => {
    if (seen) return
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setSeen(true)
          obs.disconnect()
        }
      },
      { threshold: 0.12 }
    )
    obs.observe(el)
    // The preference can change while the page is open: reveal everything at once.
    const mq = window.matchMedia(RM_QUERY)
    const onChange = () => { if (mq.matches) { setSeen(true); obs.disconnect() } }
    mq.addEventListener('change', onChange)
    return () => { obs.disconnect(); mq.removeEventListener('change', onChange) }
  }, [seen])
  return [ref, seen]
}
