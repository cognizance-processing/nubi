/**
 * useReveal — one-shot scroll-reveal for marketing sections. Pair with the
 * .lp-reveal / .lp-in classes from MarketingStyles.
 *   const [ref, seen] = useReveal()
 *   <div ref={ref} className={`lp-reveal ${seen ? 'lp-in' : ''}`}>
 */
import { useState, useEffect, useRef } from 'react'

export default function useReveal() {
  const ref = useRef(null)
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined')
  useEffect(() => {
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
    return () => obs.disconnect()
  }, [])
  return [ref, seen]
}

