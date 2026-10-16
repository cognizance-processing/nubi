/**
 * The poster as a live stage: draws the director's current frame with the 2D renderer. Used for ?poster=1
 * (builders iterate on layouts under SwiftShader), while the WebGL chunk loads, when WebGL is unavailable,
 * and after a context loss. Morphs show as cuts (the destination once m ≥ .5).
 */
import { renderPoster } from './poster'
import { readPalette } from './palette'
import type { Director } from './director'
import { isVariant } from './director'
import type { Beat, SurfaceText } from './types'

export function mountPosterStage(canvas: HTMLCanvasElement, director: Director, opts: { paletteRoot: Element; dpr?: number; drawTexts?: boolean }) {
  let raf = 0, last = '', alive = true
  const draw = () => {
    raf = 0
    if (!alive) return
    const d = director.display()
    const key = d.m >= 0.5 ? d.to : d.from
    const vp = director.viewport
    const sig = `${isVariant(key) ? 'v' + key.variant : 'r' + key.row}|${JSON.stringify(d.cam)}|${vp.w}x${vp.h}|${vp.safe.x0}|${director.hover?.group ?? ''}|${document.documentElement.className}`
    if (sig === last) { director.markFrame(); return }
    last = sig
    const pal = readPalette(opts.paletteRoot)
    const mod = director.scenes[d.scene]
    const beatId = (mod.beats[d.beat] as Beat<any>).id
    const texts: SurfaceText[] = opts.drawTexts ? (mod.surface as SurfaceText[]).filter(t => t.beats.includes(beatId)) : []
    renderPoster(canvas, {
      layout: director.layoutOf(key), cam: d.cam, viewport: vp, palette: pal, dpr: opts.dpr ?? Math.min(1.5, window.devicePixelRatio || 1),
      texts, state: d.state, groundMaskX: vp.mobile ? undefined : vp.safe.x0 - 48, hover: director.hover?.group ?? null,
      muted: getComputedStyle(opts.paletteRoot).getPropertyValue('--muted').trim() || undefined,
    })
    canvas.style.width = `${vp.w}px`; canvas.style.height = `${vp.h}px`
    director.markFrame()
  }
  const kick = () => { if (!raf && alive) raf = requestAnimationFrame(draw) }
  const off = director.on(kick)
  const prevInvalidate = director.invalidate
  director.invalidate = kick
  kick()
  return {
    redraw() { last = ''; kick() },
    destroy() { alive = false; off(); cancelAnimationFrame(raf); if (director.invalidate === kick) director.invalidate = prevInvalidate },
  }
}
