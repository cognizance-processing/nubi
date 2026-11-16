/**
 * Surface text math shared by the DOM label layer and the 2D poster. For an orthographic camera the image
 * of a plane is affine, so a CSS matrix() / canvas setTransform() places text on an object's face exactly.
 */
import { project, projectDir, type View } from './camera'
import type { SurfaceText, Vec3 } from './types'

/** Local text space: 1 world unit = TEXT_SCALE px before the transform (fonts stay crisp). */
export const TEXT_SCALE = 32
/** Cap height / font size for Instrument Sans and Geist Mono. */
export const CAP_RATIO = 0.7

export const FONT_CSS: Record<SurfaceText['font'], { family: string; weight: number }> = {
  title: { family: "'Instrument Sans', 'Helvetica Neue', Arial, sans-serif", weight: 600 },
  ui: { family: "'Instrument Sans', 'Helvetica Neue', Arial, sans-serif", weight: 500 },
  mono: { family: "'Geist Mono', ui-monospace, Menlo, monospace", weight: 400 },
  'mono-strong': { family: "'Geist Mono', ui-monospace, Menlo, monospace", weight: 600 },
}

export function planeBasis(plane: SurfaceText['plane']): { right: Vec3; up: Vec3 } {
  if (!plane || plane === 'front') return { right: [1, 0, 0], up: [0, 1, 0] }
  if (plane === 'top') return { right: [1, 0, 0], up: [0, 0, -1] }
  return plane
}

export interface TextPlacement { a: number; b: number; c: number; d: number; e: number; f: number; fontPx: number; capPx: number }

/** The affine transform mapping local text px (x right, y down, origin = baseline-left `at`) to CSS px. */
export function placeText(view: View, st: { at: Vec3; plane?: SurfaceText['plane']; size: number }): TextPlacement {
  const { right, up } = planeBasis(st.plane)
  const o = project(view, st.at)
  const ex = projectDir(view, right), ey = projectDir(view, up)
  const k = 1 / TEXT_SCALE
  // local +y (down) = world -up
  return { a: ex[0] * k, b: ex[1] * k, c: -ey[0] * k, d: -ey[1] * k, e: o[0], f: o[1], fontPx: (st.size / CAP_RATIO) * TEXT_SCALE, capPx: st.size * Math.hypot(ey[0], ey[1]) }
}

export const resolveText = (t: string | ((s: any) => string), s: any) => (typeof t === 'function' ? t(s) : t)
