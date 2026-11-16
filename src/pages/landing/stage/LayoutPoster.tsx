/**
 * LayoutPoster — a static, theme-aware 2D render of any stage layout (no three.js, no WebGL). Used by the
 * Login/Register art panel. Fits the layout into the canvas, redraws on resize and on theme change.
 */
import { useEffect, useRef } from 'react'
import { makeView, project, type CamState } from './camera'
import { paletteFor, isDarkDoc } from './palette'
import { centredViewport, renderPoster } from './poster'
import type { CameraKey, Layout, SurfaceText } from './types'

export interface LayoutPosterProps {
  layout: Layout
  camera: CameraKey
  texts?: SurfaceText[]
  state?: any
  className?: string
  label: string
  /** fraction of the canvas the layout should fill */
  fill?: number
}

export default function LayoutPoster({ layout, camera, texts, state, className, label, fill = 0.92 }: LayoutPosterProps) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    let raf = 0
    const draw = () => {
      raf = 0
      const r = c.getBoundingClientRect()
      const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height))
      if (w < 8 || h < 8) return
      const vp = centredViewport(w, h, 0)
      // fit: project every cell at zoom 1, then scale the zoom so the layout fills `fill` of the canvas
      const base: CamState = { az: camera.az, el: camera.el, target: camera.target, zoom: 1 }
      const v = makeView(base, vp)
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
      const q: [number, number, number] = [0, 0, 0]
      for (const cell of layout.cells) {
        project(v, cell.p, q)
        x0 = Math.min(x0, q[0]); x1 = Math.max(x1, q[0]); y0 = Math.min(y0, q[1]); y1 = Math.max(y1, q[1])
      }
      const k = Math.min((w * fill) / Math.max(1, x1 - x0), (h * fill) / Math.max(1, y1 - y0))
      // centre on the projected box
      const cx = (x0 + x1) / 2 - w / 2, cy = (y0 + y1) / 2 - h / 2
      const u = cx / v.pxPerUnit, t = -cy / v.pxPerUnit
      const tgt: [number, number, number] = [
        camera.target[0] + v.X[0] * u + v.Y[0] * t, camera.target[1] + v.X[1] * u + v.Y[1] * t, camera.target[2] + v.X[2] * u + v.Y[2] * t,
      ]
      renderPoster(c, {
        layout, cam: { ...base, target: tgt, zoom: k }, viewport: vp, palette: paletteFor(isDarkDoc()),
        texts, state, ground: true, transparent: true, dpr: Math.min(2, window.devicePixelRatio || 1),
      })
    }
    const kick = () => { if (!raf) raf = requestAnimationFrame(draw) }
    const ro = new ResizeObserver(kick)
    ro.observe(c)
    const mo = new MutationObserver(kick)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    const fonts = (document as any).fonts
    if (fonts?.ready) fonts.ready.then(kick)
    kick()
    return () => { ro.disconnect(); mo.disconnect(); cancelAnimationFrame(raf) }
  }, [layout, camera, texts, state, fill])
  return <canvas ref={ref} className={className} role="img" aria-label={label} />
}
