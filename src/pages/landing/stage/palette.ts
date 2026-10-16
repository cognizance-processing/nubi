/**
 * Stage palette (storyboard §3.2). Tokens live as CSS custom properties on `.nl`; the shader, the poster
 * and the label layer read them here. Hex is parsed straight to 0..1 floats (no THREE.Color, no colour
 * management) so a camera-facing unlit accent face reads exactly #1f4fd1 / #7d9bff.
 */
import type { Vec3 } from './types'

export interface Palette {
  dark: boolean
  bg: Vec3
  tones: Vec3[]            // 10 fills by TONE index (GHOST = bg, XRAY = bg, unused)
  edge: Vec3
  contour: Vec3
  contourA: number
  toneW: Vec3              // face weights: top-lit / side / shade
  grid: number
  accent: Vec3
  accentSoft: Vec3
  hex: Record<string, string>
}

/** Per-tone edge strength k (index = TONE). GHOST and XRAY are dashed. */
export const EDGE_K = [0.34, 0.5, 0.42, 0.55, 0.3, 0, 0.2, 0.08, 0.16, 0.6]

export const PALETTES = {
  light: { bg: '#ffffff', paper: '#e3e6eb', ink: '#15161a', acc: '#1f4fd1', mid: '#cfd4dc', floor: '#d5d9df', accSoft: '#c3d0f4', screen: '#ffffff', sheet: '#f2f3f5', edge: '#0b0c0e', contour: '#0b0c0e', contourA: 0.9, tone: '1, .88, .74', grid: 0.09 },
  dark: { bg: '#0d0e10', paper: '#3d424c', ink: '#eef0f3', acc: '#7d9bff', mid: '#2c3038', floor: '#25282e', accSoft: '#2a3a78', screen: '#15171b', sheet: '#1c1f24', edge: '#c9cdd4', contour: '#c9cdd4', contourA: 0.85, tone: '1, .80, .64', grid: 0.16 },
}

export function hexToVec(hex: string): Vec3 {
  let h = hex.trim().replace('#', '')
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  const n = parseInt(h.slice(0, 6), 16)
  if (!Number.isFinite(n)) return [0, 0, 0]
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}
export const vecToCss = (v: Vec3, a = 1) => `rgba(${Math.round(v[0] * 255)},${Math.round(v[1] * 255)},${Math.round(v[2] * 255)},${a})`

type Src = typeof PALETTES.light

function build(src: Src, dark: boolean): Palette {
  const bg = hexToVec(src.bg)
  const tones = [src.paper, src.ink, src.acc, src.bg, src.mid, src.floor, src.accSoft, src.screen, src.sheet, src.bg].map(hexToVec)
  const tw = String(src.tone).split(',').map(s => parseFloat(s)) as Vec3
  return {
    dark, bg, tones, edge: hexToVec(src.edge), contour: hexToVec(src.contour), contourA: +src.contourA,
    toneW: tw.length === 3 && tw.every(Number.isFinite) ? tw : [1, 0.88, 0.74], grid: +src.grid,
    accent: hexToVec(src.acc), accentSoft: hexToVec(src.accSoft),
    hex: { bg: src.bg, ink: src.ink, acc: src.acc, edge: src.edge, contour: src.contour, mid: src.mid, sheet: src.sheet, screen: src.screen, paper: src.paper },
  }
}

export const isDarkDoc = () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark')

/** Read the palette from CSS tokens on `el` (the .nl root); falls back to the built-in tables. */
export function readPalette(el?: Element | null, dark = isDarkDoc()): Palette {
  const base = dark ? PALETTES.dark : PALETTES.light
  if (!el || typeof getComputedStyle === 'undefined') return build(base, dark)
  const cs = getComputedStyle(el)
  const g = (name: string, fb: string | number) => { const v = cs.getPropertyValue(name).trim(); return v || String(fb) }
  return build({
    bg: g('--bg', base.bg), paper: g('--c-paper', base.paper), ink: g('--c-ink', base.ink), acc: g('--c-acc', base.acc),
    mid: g('--c-mid', base.mid), floor: g('--c-floor', base.floor), accSoft: g('--c-acc-soft', base.accSoft), screen: g('--c-screen', base.screen),
    sheet: g('--c-sheet', base.sheet), edge: g('--c-edge', base.edge), contour: g('--c-contour', base.contour),
    contourA: +g('--c-contour-a', base.contourA), tone: g('--c-tone', base.tone), grid: +g('--c-grid', base.grid),
  }, dark)
}

/** Palette for a theme without the DOM (auth panel before mount, tests). */
export const paletteFor = (dark: boolean) => build(dark ? PALETTES.dark : PALETTES.light, dark)
