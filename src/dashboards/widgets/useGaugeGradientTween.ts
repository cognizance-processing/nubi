/**
 * Sweep a gradient gauge to a new value.
 *
 * The gradient arc is painted as `axisLine` colour stops, which ECharts does
 * not tween — on a new value the fill would jump in one frame. This hook eases
 * a display value over GAUGE_TWEEN_MS and the host rebuilds the option each
 * frame, mirroring ECharts' native gauge animation:
 *
 * - Reset (first value, `resetKey` change e.g. needle ↔ ring, or the tween
 *   becoming enabled): intro sweep from `introFrom` (the gauge min) with
 *   'cubicOut' — ECharts' initial animation.
 * - New target: sweep from the value currently drawn with 'cubicInOut' —
 *   ECharts' update animation. A new target mid-tween restarts from there.
 * - Disabled (no gradient / animation off) or reduced motion: snap, no tween.
 * - Display values are rounded to the target's precision so a '{value}'
 *   readout doesn't print float noise.
 *
 * State lives here only — never in widget.config.
 */
import { useEffect, useRef, useState } from 'react'

/** ECharts' default animationDuration / animationDurationUpdate. */
export const GAUGE_TWEEN_MS = 1000

type Ease = (k: number) => number
// ECharts' 'cubicOut' (initial animation) and 'cubicInOut' (update animation).
const cubicOut: Ease = (k) => --k * k * k + 1
const cubicInOut: Ease = (k) => {
  if ((k *= 2) < 1) return 0.5 * k * k * k
  return 0.5 * ((k -= 2) * k * k + 2)
}

function decimals(n: number): number {
  for (let d = 0; d < 6; d++) {
    const x = n * 10 ** d
    if (Math.abs(x - Math.round(x)) < 1e-9 * Math.max(1, Math.abs(x))) return d
  }
  return 6
}

const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n)

function usePrefersReducedMotion(): boolean {
  const query = '(prefers-reduced-motion: reduce)'
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && !!window.matchMedia?.(query).matches,
  )
  useEffect(() => {
    const mq = typeof window !== 'undefined' ? window.matchMedia?.(query) : null
    if (!mq) return
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])
  return reduced
}

export function useGaugeGradientTween(
  target: number | null,
  { enabled, resetKey, introFrom = 0 }: { enabled: boolean; resetKey?: string; introFrom?: number },
): { value: number | null; tweening: boolean } {
  const reduced = usePrefersReducedMotion()
  const active = enabled && !reduced
  const [state, setState] = useState<{ value: number | null; tweening: boolean }>({ value: target, tweening: false })
  // What the last effect run handled, plus the value currently drawn.
  const displayRef = useRef<number | null>(null)
  const aimRef = useRef<number | null | undefined>(undefined)
  const keyRef = useRef<string | undefined>(resetKey)
  const activeRef = useRef(false)
  const introRef = useRef(introFrom)
  const planRef = useRef<{ from: number; ease: Ease } | null>(null)
  introRef.current = introFrom

  // Start point + easing for a change to (target, resetKey, active), or null
  // to snap. Shared by the effect and the render that sees the change first.
  const plan = (): { from: number; ease: Ease } | null => {
    if (!active || !finite(target)) return null
    const reset = keyRef.current !== resetKey || !activeRef.current || !finite(displayRef.current)
    const from = reset ? Number(introRef.current) : displayRef.current
    if (!finite(from) || from === target) return null
    return { from, ease: reset ? cubicOut : cubicInOut }
  }

  useEffect(() => {
    // Nothing changed since the last run (StrictMode's mount re-run): replay
    // that run's plan rather than re-deriving it from refs it already moved.
    const unchanged = aimRef.current === target && keyRef.current === resetKey && activeRef.current === active
    const p = unchanged ? planRef.current : plan()
    planRef.current = p
    keyRef.current = resetKey
    aimRef.current = target
    activeRef.current = active
    const snap = () => {
      displayRef.current = target
      setState((s) => (s.value === target && !s.tweening ? s : { value: target, tweening: false }))
    }
    if (!p) { snap(); return }

    const { from, ease } = p
    const to = target as number
    const scale = 10 ** Math.max(decimals(from), decimals(to))
    displayRef.current = from
    let t0: number | null = null
    let raf = requestAnimationFrame(function step(now) {
      if (t0 == null) t0 = now
      const k = Math.min(1, Math.max(0, (now - t0) / GAUGE_TWEEN_MS))
      if (k >= 1) { snap(); return }
      const v = Math.round((from + (to - from) * ease(k)) * scale) / scale
      displayRef.current = v
      setState({ value: v, tweening: true })
      raf = requestAnimationFrame(step)
    })
    return () => cancelAnimationFrame(raf)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, resetKey, active])

  // The render a change arrives on runs before the effect above starts the
  // tween; without this it would draw the final arc for a frame, then snap back.
  if (target !== aimRef.current || resetKey !== keyRef.current || active !== activeRef.current) {
    const p = plan()
    return p ? { value: p.from, tweening: true } : { value: target, tweening: false }
  }
  return state
}
