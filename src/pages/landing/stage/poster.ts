/**
 * 2D poster renderer (storyboard F1.3): draws any Layout for any camera on a 2D canvas without WebGL.
 * Used for the dev harness (?poster=1), the no-WebGL / context-loss fallback, the frame shown while the
 * stage chunk loads, and the Login/Register art panel.
 *
 * It is a tiny software rasteriser so the look matches the GPU ink shader: per-face view-space tones,
 * height AO, fwidth-style per-tone edges (dashed ghost, outline-only x-ray), a ruled ground, an exact
 * group-id contour pass (2 CSS px), and surface text placed with an affine setTransform.
 */
import { makeView, project, type CamState, type View, type Viewport } from './camera'
import { DEFAULT_SIZE } from './assign'
import { EDGE_K, vecToCss, type Palette } from './palette'
import { CAP_RATIO, FONT_CSS, TEXT_SCALE, placeText, resolveText } from './surfaceText'
import { TONE } from './types'
import type { Cell, Layout, SurfaceText, Vec3 } from './types'

export interface PosterOptions {
  layout: Layout
  cam: CamState
  viewport: Viewport
  palette: Palette
  dpr?: number
  texts?: SurfaceText[]           // drawn on top (already filtered to the beat)
  state?: any                     // for text functions
  ground?: boolean
  groundMaskX?: number            // CSS px: ground fades in to the right of this x (under the caption plate)
  contour?: boolean
  hover?: string | null
  muted?: string                  // css colour for 'muted' text
  transparent?: boolean           // leave the background transparent (auth panel sits on its own sheet)
}

const KEY: Vec3 = (() => { const v: Vec3 = [-0.32, 0.62, 0.72]; const l = Math.hypot(...v); return [v[0] / l, v[1] / l, v[2] / l] })()
const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t) }

/** Six faces of the unit cube: normal and the (origin, u, v) of each face in local −.5..+.5 coords. */
const FACES: { n: Vec3; o: Vec3; u: Vec3; v: Vec3 }[] = [
  { n: [1, 0, 0], o: [0.5, -0.5, 0.5], u: [0, 0, -1], v: [0, 1, 0] },
  { n: [-1, 0, 0], o: [-0.5, -0.5, -0.5], u: [0, 0, 1], v: [0, 1, 0] },
  { n: [0, 1, 0], o: [-0.5, 0.5, 0.5], u: [1, 0, 0], v: [0, 0, -1] },
  { n: [0, -1, 0], o: [-0.5, -0.5, -0.5], u: [1, 0, 0], v: [0, 0, 1] },
  { n: [0, 0, 1], o: [-0.5, -0.5, 0.5], u: [1, 0, 0], v: [0, 1, 0] },
  { n: [0, 0, -1], o: [0.5, -0.5, -0.5], u: [-1, 0, 0], v: [0, 1, 0] },
]

/** Rotate local vector by roll (z) then yaw (y), the same order as the shader. */
function rot(v: Vec3, yaw: number, roll: number): Vec3 {
  let [x, y, z] = v
  if (roll) { const c = Math.cos(roll), s = Math.sin(roll); const nx = c * x - s * y; y = s * x + c * y; x = nx }
  if (yaw) { const c = Math.cos(yaw), s = Math.sin(yaw); const nx = c * x + s * z; z = -s * x + c * z; x = nx }
  return [x, y, z]
}

export function renderPoster(canvas: HTMLCanvasElement, opt: PosterOptions): View {
  const dpr = opt.dpr ?? Math.min(2, (typeof devicePixelRatio === 'number' ? devicePixelRatio : 1) || 1)
  const view = makeView(opt.cam, opt.viewport)
  const W = Math.max(1, Math.round(view.w * dpr)), H = Math.max(1, Math.round(view.h * dpr))
  if (canvas.width !== W) canvas.width = W
  if (canvas.height !== H) canvas.height = H
  const ctx = canvas.getContext('2d')
  if (!ctx) return view
  const P = opt.palette
  const img = ctx.createImageData(W, H)
  const px = img.data
  const depth = new Float32Array(W * H).fill(Infinity)
  const ids = new Int32Array(W * H)
  const bg = P.bg
  for (let i = 0; i < W * H; i++) { px[i * 4] = bg[0] * 255; px[i * 4 + 1] = bg[1] * 255; px[i * 4 + 2] = bg[2] * 255; px[i * 4 + 3] = opt.transparent ? 0 : 255 }

  // ── ground: 4-unit ruled grid + 2-unit dots, radial fade, masked under the caption plate
  if (opt.ground !== false) drawGround(px, W, H, dpr, view, P, opt)

  // ── cubes
  const gidOf = new Map<string, number>()
  const gid = (g: string) => { if (!g) return 0; let v = gidOf.get(g); if (v === undefined) { v = gidOf.size + 1; gidOf.set(g, v) } return v }
  const proj: [number, number, number] = [0, 0, 0]
  const Z = view.Z, X = view.X, Y = view.Y
  for (const c of opt.layout.cells) {
    const s = c.s ?? DEFAULT_SIZE
    if (s[0] <= 0 || s[1] <= 0 || s[2] <= 0) continue
    const tone = c.tone | 0
    const fill = P.tones[tone] ?? P.tones[0]
    const k0 = EDGE_K[tone] ?? 0.3
    const ghost = tone === TONE.GHOST ? 1 : tone === TONE.XRAY ? 2 : 0
    const id = tone === TONE.XRAY ? 0 : gid(c.group)
    const yaw = c.yaw ?? 0, roll = c.roll ?? 0
    for (const f of FACES) {
      const n = rot(f.n, yaw, roll)
      const facing = n[0] * Z[0] + n[1] * Z[1] + n[2] * Z[2]
      if (facing <= 1e-4) continue
      const lo = rot([f.o[0] * s[0], f.o[1] * s[1], f.o[2] * s[2]], yaw, roll)
      const lu = rot([f.u[0] * s[0], f.u[1] * s[1], f.u[2] * s[2]], yaw, roll)
      const lv = rot([f.v[0] * s[0], f.v[1] * s[1], f.v[2] * s[2]], yaw, roll)
      const wo: Vec3 = [c.p[0] + lo[0], c.p[1] + lo[1], c.p[2] + lo[2]]
      project(view, wo, proj); const ox = proj[0] * dpr, oy = proj[1] * dpr, od = proj[2]
      project(view, [wo[0] + lu[0], wo[1] + lu[1], wo[2] + lu[2]], proj); const ux = proj[0] * dpr - ox, uy = proj[1] * dpr - oy, ud = proj[2] - od
      project(view, [wo[0] + lv[0], wo[1] + lv[1], wo[2] + lv[2]], proj); const vx = proj[0] * dpr - ox, vy = proj[1] * dpr - oy, vd = proj[2] - od
      const det = ux * vy - uy * vx
      if (Math.abs(det) < 1e-6) continue
      const i00 = vy / det, i01 = -vx / det, i10 = -uy / det, i11 = ux / det
      const fwu = Math.abs(i00) + Math.abs(i01), fwv = Math.abs(i10) + Math.abs(i11)
      const fade = 1 - smooth(0.09, 0.26, Math.max(fwu, fwv))
      // tone + AO
      const nv: Vec3 = [n[0] * X[0] + n[1] * X[1] + n[2] * X[2], n[0] * Y[0] + n[1] * Y[1] + n[2] * Y[2], facing]
      const lit = smooth(-0.15, 0.7, nv[0] * KEY[0] + nv[1] * KEY[1] + nv[2] * KEY[2])
      const shade = P.toneW[2] + (P.toneW[0] - P.toneW[2]) * lit
      const up = Math.max(n[1], 0)
      const xs = [ox, ox + ux, ox + vx, ox + ux + vx], ys = [oy, oy + uy, oy + vy, oy + uy + vy]
      const x0 = Math.max(0, Math.floor(Math.min(...xs))), x1 = Math.min(W - 1, Math.ceil(Math.max(...xs)))
      const y0 = Math.max(0, Math.floor(Math.min(...ys))), y1 = Math.min(H - 1, Math.ceil(Math.max(...ys)))
      for (let y = y0; y <= y1; y++) {
        const dy = y + 0.5 - oy
        for (let x = x0; x <= x1; x++) {
          const dx = x + 0.5 - ox
          const u = i00 * dx + i01 * dy, v = i10 * dx + i11 * dy
          if (u < -1e-4 || u > 1.0001 || v < -1e-4 || v > 1.0001) continue
          const d = od + u * ud + v * vd - 1e-4 * facing
          const pi = y * W + x
          if (d >= depth[pi]) continue
          const au = Math.min(u, 1 - u) / fwu, av = Math.min(v, 1 - v) / fwv
          let e = 1 - smooth(0.45, 1.35, Math.min(au, av))
          let k = k0, fd = fade
          if (ghost) {
            const along = au < av ? v : u
            k *= ((along * 3 + 0.25) % 1) >= 0.5 ? 1 : 0
            fd = 1
            if (ghost === 2 && e * k < 0.05) continue
          }
          depth[pi] = d
          ids[pi] = id
          let r: number, g: number, b: number
          if (ghost) { r = bg[0]; g = bg[1]; b = bg[2] }
          else {
            const wy = wo[1] + u * lu[1] + v * lv[1]
            const ao = 0.86 + 0.14 * smooth(0, 2.5, wy)
            const m = shade * (ao + (1 - ao) * up)
            r = Math.min(1, fill[0] * m); g = Math.min(1, fill[1] * m); b = Math.min(1, fill[2] * m)
          }
          e *= fd * k
          const o4 = pi * 4
          px[o4] = (r + (P.edge[0] - r) * e) * 255
          px[o4 + 1] = (g + (P.edge[1] - g) * e) * 255
          px[o4 + 2] = (b + (P.edge[2] - b) * e) * 255
          px[o4 + 3] = 255
        }
      }
    }
  }

  // ── object contour: 2 CSS px where the group id differs among the 8 neighbours
  if (opt.contour !== false) {
    const off = Math.max(1, Math.round(dpr))
    const hv = opt.hover ? gidOf.get(opt.hover) ?? -1 : -1
    const out = new Uint8ClampedArray(px)
    const nb = [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]]
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const pi = y * W + x, c = ids[pi]
      let diff = 0, hot = c === hv && hv > 0
      for (const [ax, ay] of nb) {
        const xx = Math.min(W - 1, Math.max(0, x + ax * off)), yy = Math.min(H - 1, Math.max(0, y + ay * off))
        const n = ids[yy * W + xx]
        if (n !== c) { diff++; if (n === hv && hv > 0) hot = true }
      }
      if (!diff) continue
      const a = smooth(0, 0.25, diff / 8) * P.contourA
      const col = hot ? P.accent : P.contour
      const o4 = pi * 4
      out[o4] = px[o4] + (col[0] * 255 - px[o4]) * a
      out[o4 + 1] = px[o4 + 1] + (col[1] * 255 - px[o4 + 1]) * a
      out[o4 + 2] = px[o4 + 2] + (col[2] * 255 - px[o4 + 2]) * a
      out[o4 + 3] = Math.max(px[o4 + 3], a * 255)
    }
    img.data.set(out)
  }
  ctx.putImageData(img, 0, 0)

  // ── surface text
  if (opt.texts?.length) drawTexts(ctx, view, dpr, opt.texts, opt.state, P, opt.muted)
  return view
}

function drawGround(px: Uint8ClampedArray, W: number, H: number, dpr: number, view: View, P: Palette, opt: PosterOptions) {
  // ray per pixel to the floor (orthographic: affine in screen space)
  const dirY = -view.Z[1]
  if (Math.abs(dirY) < 1e-4) return
  const at = (sx: number, sy: number): [number, number] => {
    const u = view.left + (sx / view.w) * (view.right - view.left)
    const w = view.top - (sy / view.h) * (view.top - view.bottom)
    const o = [view.eye[0] + view.X[0] * u + view.Y[0] * w, view.eye[1] + view.X[1] * u + view.Y[1] * w, view.eye[2] + view.X[2] * u + view.Y[2] * w]
    const t = -o[1] / dirY
    return [o[0] - view.Z[0] * t, o[2] - view.Z[2] * t]
  }
  const p00 = at(0, 0), p10 = at(1 / dpr, 0), p01 = at(0, 1 / dpr)
  const dxx = p10[0] - p00[0], dxz = p10[1] - p00[1], dyx = p01[0] - p00[0], dyz = p01[1] - p00[1]
  const fwx = (Math.abs(dxx) + Math.abs(dyx)) / 4, fwz = (Math.abs(dxz) + Math.abs(dyz)) / 4
  const ink = P.edge, mask = (opt.groundMaskX ?? -1e9) * dpr
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const m = mask > -1e8 ? smooth(mask, mask + 120 * dpr, x) : 1
    if (m <= 0) continue
    const wx = p00[0] + dxx * (x + 0.5) + dyx * (y + 0.5), wz = p00[1] + dxz * (x + 0.5) + dyz * (y + 0.5)
    const gx = Math.abs(((wx / 4 - 0.5) % 1 + 1) % 1 - 0.5) / Math.max(fwx, 1e-5)
    const gz = Math.abs(((wz / 4 - 0.5) % 1 + 1) % 1 - 0.5) / Math.max(fwz, 1e-5)
    let l = 1 - Math.min(Math.min(gx, gz), 1)
    const dd = Math.hypot((wx - 2 * Math.round(wx / 2)) / Math.max(fwx, 1e-5), (wz - 2 * Math.round(wz / 2)) / Math.max(fwz, 1e-5))
    const dot = (1 - smooth(0.6, 1.6, dd)) * 0.7
    l = Math.max(l, dot)
    const r = 1 - smooth(30, 70, Math.hypot(wx - (opt.cam.target[0]), wz - opt.cam.target[2]))
    const a = l * P.grid * r * m
    if (a <= 0.002) continue
    const o4 = (y * W + x) * 4
    px[o4] += (ink[0] * 255 - px[o4]) * a; px[o4 + 1] += (ink[1] * 255 - px[o4 + 1]) * a; px[o4 + 2] += (ink[2] * 255 - px[o4 + 2]) * a
    if (opt.transparent) px[o4 + 3] = Math.max(px[o4 + 3], a * 255)
  }
}

export function textColor(tone: SurfaceText['tone'], P: Palette, muted?: string): string {
  if (tone === 'accent') return P.hex.acc
  if (tone === 'onInk') return P.hex.bg
  if (tone === 'muted') return muted ?? (P.dark ? '#a7abb4' : '#4f545e')
  return P.hex.ink
}

function drawTexts(ctx: CanvasRenderingContext2D, view: View, dpr: number, texts: SurfaceText[], state: any, P: Palette, muted?: string) {
  ctx.save()
  for (const st of texts) {
    const pl = placeText(view, st)
    if (pl.capPx / CAP_RATIO < 9) continue
    const f = FONT_CSS[st.font]
    ctx.setTransform(pl.a * dpr, pl.b * dpr, pl.c * dpr, pl.d * dpr, pl.e * dpr, pl.f * dpr)
    ctx.font = `${f.weight} ${pl.fontPx}px ${f.family}`
    ctx.fillStyle = textColor(st.tone, P, muted)
    ctx.textBaseline = 'alphabetic'
    ctx.textAlign = st.align === 'centre' ? 'center' : st.align === 'right' ? 'right' : 'left'
    let text = resolveText(st.text, state)
    if (st.maxWidth) {
      const max = st.maxWidth * TEXT_SCALE
      if (ctx.measureText(text).width > max) { while (text.length > 1 && ctx.measureText(text + '…').width > max) text = text.slice(0, -1); text += '…' }
    }
    ctx.fillText(text, 0, 0)
  }
  ctx.restore()
}

/** Convenience for small static posters (auth panel): a viewport of w×h with the target centred. */
export function centredViewport(w: number, h: number, pad = 16): Viewport {
  return { w, h, mobile: false, safe: { x0: pad, y0: pad, x1: w - pad, y1: h - pad } }
}

export { vecToCss }
export type { Cell }
