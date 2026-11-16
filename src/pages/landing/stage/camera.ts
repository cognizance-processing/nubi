/**
 * Orthographic camera rig (storyboard §1.4, F2.8), pure TypeScript so the R3F stage, the DOM label layer
 * and the 2D poster all project with the same numbers.
 *
 * az/el: the camera sits at target + 200 · (cos el · sin az, sin el, cos el · cos az); az < 0 = front-left.
 * Frustum height = base / zoom, base = 50 (aspect ≥ 1.2) or max(50, 60 / aspect) below that.
 * Desktop zoom is multiplied by min(1, safeW / 820). The frustum is shifted so the target lands at the
 * centre of the stage safe rect (the area right of the caption plate).
 */
import type { CameraKey, Vec3 } from './types'

export interface CamState { az: number; el: number; target: Vec3; zoom: number }
export interface SafeRect { x0: number; y0: number; x1: number; y1: number }
export interface Viewport { w: number; h: number; safe: SafeRect; mobile: boolean }
export interface View {
  w: number; h: number
  eye: Vec3; X: Vec3; Y: Vec3; Z: Vec3                // camera basis (Z points from the target to the eye)
  left: number; right: number; top: number; bottom: number; near: number; far: number
  pxPerUnit: number
}

export const easeInOutCubic = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

export function camOf(key: CameraKey, mobile: boolean): CamState {
  const m = mobile && key.mobile ? key.mobile : {}
  return { az: m.az ?? key.az, el: m.el ?? key.el, target: m.target ?? key.target, zoom: m.zoom ?? key.zoom }
}

export function lerpCam(a: CamState, b: CamState, t: number): CamState {
  const L = (x: number, y: number) => x + (y - x) * t
  return { az: L(a.az, b.az), el: L(a.el, b.el), zoom: L(a.zoom, b.zoom), target: [L(a.target[0], b.target[0]), L(a.target[1], b.target[1]), L(a.target[2], b.target[2])] }
}

const norm = (v: Vec3): Vec3 => { const l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l] }
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

export const CAM_DIST = 200

export function makeView(cam: CamState, vp: Viewport): View {
  const { w, h, safe } = vp
  const aspect = w / Math.max(1, h)
  const safeW = Math.max(1, safe.x1 - safe.x0)
  const zoom = cam.zoom * (vp.mobile ? 1 : Math.min(1, safeW / 820))
  const F = (aspect < 1.2 ? Math.max(50, 60 / aspect) : 50) / zoom
  const halfH = F / 2, halfW = (F * aspect) / 2
  const Z = norm([Math.cos(cam.el) * Math.sin(cam.az), Math.sin(cam.el), Math.cos(cam.el) * Math.cos(cam.az)])
  const X = norm(cross([0, 1, 0], Z))
  const Y = cross(Z, X)
  const t = cam.target
  const eye: Vec3 = [t[0] + Z[0] * CAM_DIST, t[1] + Z[1] * CAM_DIST, t[2] + Z[2] * CAM_DIST]
  const cx = (safe.x0 + safe.x1) / 2, cy = (safe.y0 + safe.y1) / 2
  const left = -(cx / w) * 2 * halfW
  const top = (cy / h) * 2 * halfH
  return { w, h, eye, X, Y, Z, left, right: left + 2 * halfW, top, bottom: top - 2 * halfH, near: 1, far: 600, pxPerUnit: h / F }
}

/** World → CSS px (+ distance from the eye along the view direction). */
export function project(v: View, p: Vec3, out: [number, number, number] = [0, 0, 0]): [number, number, number] {
  const d: Vec3 = [p[0] - v.eye[0], p[1] - v.eye[1], p[2] - v.eye[2]]
  out[0] = ((dot(d, v.X) - v.left) / (v.right - v.left)) * v.w
  out[1] = ((v.top - dot(d, v.Y)) / (v.top - v.bottom)) * v.h
  out[2] = -dot(d, v.Z)
  return out
}

/** Screen-space image (px per world unit) of a world direction: the affine part of the projection. */
export function projectDir(v: View, d: Vec3): [number, number] {
  return [dot(d, v.X) * v.pxPerUnit, -dot(d, v.Y) * v.pxPerUnit]
}

/** A pick ray for a CSS px point: origin on the near plane, direction = -Z. */
export function rayAt(v: View, sx: number, sy: number): { o: Vec3; d: Vec3 } {
  const u = v.left + (sx / v.w) * (v.right - v.left)
  const w = v.top - (sy / v.h) * (v.top - v.bottom)
  const o: Vec3 = [v.eye[0] + v.X[0] * u + v.Y[0] * w, v.eye[1] + v.X[1] * u + v.Y[1] * w, v.eye[2] + v.X[2] * u + v.Y[2] * w]
  return { o, d: [-v.Z[0], -v.Z[1], -v.Z[2]] }
}

/** Ray vs axis-aligned box; returns the entry distance or -1. */
export function rayBox(o: Vec3, d: Vec3, min: Vec3, max: Vec3): number {
  let t0 = -Infinity, t1 = Infinity
  for (let i = 0; i < 3; i++) {
    if (Math.abs(d[i]) < 1e-9) { if (o[i] < min[i] || o[i] > max[i]) return -1; continue }
    let a = (min[i] - o[i]) / d[i], b = (max[i] - o[i]) / d[i]
    if (a > b) { const tmp = a; a = b; b = tmp }
    if (a > t0) t0 = a
    if (b < t1) t1 = b
    if (t0 > t1) return -1
  }
  return t1 < 0 ? -1 : Math.max(t0, 0)
}
