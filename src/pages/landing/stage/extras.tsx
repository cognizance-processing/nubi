/**
 * Stage extras for scene modules (F2.10). Import ONLY from a scene's Extras.tsx (lazy), never from index.ts:
 *
 *   <Trace points={[[x,y,z], …]} dashed active />    2 CSS px screen-space line (orthogonal PCB routes: chamfer())
 *   <Packets path={pts} t={[0.2, 0.7]} label="QUERY" /> instanced accent cubes along a path (+ optional DOM chip)
 *   <Cursor path={pts} t={0.6} click={0.9} />          DOM pointer arrow + click ring
 *   <InkBoxes boxes={[{ p, s, tone, group }]} />        static ink cubes that take the contour
 *
 * All of them write the MRT id buffer, so the contour pass stays correct. Positions should be pure functions
 * of the Extras props (T, m, ambientT, state): no timers.
 */
import { useEffect, useMemo } from 'react'
import * as THREE from 'three'
import { useFrame } from '@nubi/r3f'
import { useAdd, useStage } from './Stage'
import { project } from './camera'
import { LINE_FRAG, LINE_VERT, createBoxMaterial, applyPalette, type InkUniforms } from './inkMaterial'
import { TONE, type Vec3 } from './types'

export { useStage, useAdd }

/* ───────────── path helpers ───────────── */

/** Orthogonal route on a plane at height y with 45° chamfers of size c at each corner (PCB style). */
export function chamfer(points: Vec3[], c = 1.2): Vec3[] {
  if (points.length < 3) return points
  const out: Vec3[] = [points[0]]
  for (let i = 1; i < points.length - 1; i++) {
    const a = points[i - 1], b = points[i], n = points[i + 1]
    const la = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]), ln = Math.hypot(n[0] - b[0], n[1] - b[1], n[2] - b[2])
    const k1 = Math.min(c, la / 2) / (la || 1), k2 = Math.min(c, ln / 2) / (ln || 1)
    out.push([b[0] + (a[0] - b[0]) * k1, b[1] + (a[1] - b[1]) * k1, b[2] + (a[2] - b[2]) * k1])
    out.push([b[0] + (n[0] - b[0]) * k2, b[1] + (n[1] - b[1]) * k2, b[2] + (n[2] - b[2]) * k2])
  }
  out.push(points[points.length - 1])
  return out
}

/** Point at parameter t ∈ [0,1] along a polyline (arc length). */
export function along(points: Vec3[], t: number): Vec3 {
  if (points.length === 1) return points[0]
  const L: number[] = [0]
  for (let i = 1; i < points.length; i++) L.push(L[i - 1] + Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1], points[i][2] - points[i - 1][2]))
  const target = Math.min(1, Math.max(0, t)) * L[L.length - 1]
  let i = 1
  while (i < L.length - 1 && L[i] < target) i++
  const u = (target - L[i - 1]) / Math.max(1e-6, L[i] - L[i - 1])
  const a = points[i - 1], b = points[i]
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u]
}

/* ───────────── Trace ───────────── */

export function Trace({ points, dashed = false, active = false, width = 2, tone }: { points: Vec3[]; dashed?: boolean; active?: boolean; width?: number; tone?: 'ink' | 'accent' }) {
  const { palette, dpr, invalidate } = useStage()
  const geo = useMemo(() => {
    const quad = new THREE.InstancedBufferGeometry()
    quad.setAttribute('position', new THREE.Float32BufferAttribute([0, -1, 0, 1, -1, 0, 1, 1, 0, 0, 1, 0], 3))
    quad.setIndex([0, 1, 2, 0, 2, 3])
    const n = Math.max(1, points.length - 1)
    const A = new Float32Array(n * 3), B = new Float32Array(n * 3), D = new Float32Array(n * 2)
    let dist = 0
    for (let i = 0; i < n; i++) {
      const a = points[i], b = points[i + 1] ?? points[i]
      A.set(a, i * 3); B.set(b, i * 3)
      const l = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2])
      D[i * 2] = dist; D[i * 2 + 1] = dist + l; dist += l
    }
    quad.setAttribute('iA', new THREE.InstancedBufferAttribute(A, 3))
    quad.setAttribute('iB', new THREE.InstancedBufferAttribute(B, 3))
    quad.setAttribute('iD', new THREE.InstancedBufferAttribute(D, 2))
    quad.instanceCount = n
    return quad
  }, [points])
  const mat = useMemo(() => new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3, vertexShader: LINE_VERT, fragmentShader: LINE_FRAG,
    uniforms: { uRes: { value: new THREE.Vector2(1, 1) }, uWidth: { value: 2 }, uColor: { value: new THREE.Vector3() }, uDash: { value: 0 } },
  }), [])
  useEffect(() => () => { geo.dispose() }, [geo])
  useEffect(() => () => { mat.dispose() }, [mat])
  useEffect(() => { invalidate() }, [active, dashed, invalidate])
  useFrame(({ size }) => {
    const p = palette.current, d = dpr()
    const col = (tone ?? (active ? 'accent' : 'ink')) === 'accent' ? p.accent : p.edge
    mat.uniforms.uColor.value.set(...col)
    mat.uniforms.uRes.value.set(size.width * d, size.height * d)
    mat.uniforms.uWidth.value = width * d
    mat.uniforms.uDash.value = dashed && !active ? 0.9 : 0
  })
  const mesh = useMemo(() => { const m = new THREE.Mesh(geo, mat); m.frustumCulled = false; m.renderOrder = 1; return m }, [geo, mat])
  useAdd(mesh)
  return null
}

/* ───────────── InkBoxes / Packets ───────────── */

export interface BoxSpec { p: Vec3; s?: Vec3; tone?: number; group?: string }

export function InkBoxes({ boxes, max = 64 }: { boxes: BoxSpec[]; max?: number }) {
  const { palette, gid } = useStage()
  const mat = useMemo(() => createBoxMaterial(palette.current), [palette])
  const geo = useMemo(() => {
    const g = new THREE.BoxGeometry(1, 1, 1)
    g.setAttribute('aTone', new THREE.InstancedBufferAttribute(new Float32Array(max), 1))
    g.setAttribute('aGid', new THREE.InstancedBufferAttribute(new Float32Array(max), 1))
    return g
  }, [max])
  const im = useMemo(() => { const m = new THREE.InstancedMesh(geo, mat, max); m.frustumCulled = false; return m }, [geo, mat, max])
  useAdd(im)
  const m4 = useMemo(() => new THREE.Matrix4(), [])
  useEffect(() => () => { geo.dispose(); mat.dispose() }, [geo, mat])
  useFrame(() => {
    applyPalette(mat.uniforms as InkUniforms, palette.current)
    const tone = geo.getAttribute('aTone') as THREE.InstancedBufferAttribute, ids = geo.getAttribute('aGid') as THREE.InstancedBufferAttribute
    const n = Math.min(max, boxes.length)
    for (let i = 0; i < n; i++) {
      const b = boxes[i], s = b.s ?? [0.7, 0.7, 0.7]
      m4.makeScale(s[0], s[1], s[2]).setPosition(b.p[0], b.p[1], b.p[2])
      im.setMatrixAt(i, m4)
      tone.setX(i, b.tone ?? TONE.ACCENT); ids.setX(i, b.group ? gid(b.group) : 0)
    }
    im.count = n
    im.instanceMatrix.needsUpdate = true; tone.needsUpdate = true; ids.needsUpdate = true
  })
  return null
}

/** DOM chip pinned to a world point (packet labels, cursor). Lives in the label layer. */
function useOverlayEl(className: string, html?: string) {
  const { overlay } = useStage()
  const el = useMemo(() => { const e = document.createElement('div'); e.className = className; if (html) e.innerHTML = html; return e }, [className, html])
  useEffect(() => { const o = overlay(); o?.appendChild(el); return () => el.remove() }, [el, overlay])
  return el
}

export function Packets({ path, t, label, short, size = 0.7, group }: { path: Vec3[]; t: number | number[]; label?: string; short?: string; size?: number; group?: string }) {
  const ts = Array.isArray(t) ? t : [t]
  const boxes = ts.filter(v => v >= 0 && v <= 1).map(v => ({ p: along(path, v), s: [size, size, size] as Vec3, tone: TONE.ACCENT, group }))
  const chip = useOverlayEl('co acc pk', '<i class="dot"></i><div class="chip"><b></b></div>')
  const { view } = useStage()
  useFrame(() => {
    if (!label || !boxes.length) { chip.classList.remove('on'); return }
    const v = view()
    const q = project(v, boxes[0].p)
    ;(chip.querySelector('b') as HTMLElement).textContent = v.w < 480 && short ? short : label
    chip.style.transform = `translate(${Math.round(q[0])}px, ${Math.round(q[1])}px)`
    ;(chip.querySelector('.chip') as HTMLElement).style.transform = 'translate(8px, -26px)'
    chip.classList.add('on')
  })
  return <InkBoxes boxes={boxes} />
}

export function Cursor({ path, t, click }: { path: Vec3[]; t: number; click?: number }) {
  const el = useOverlayEl('cursor', '<svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 2l15 9-6.5 1.5L9 19z" fill="var(--ink)" stroke="var(--bg)" stroke-width="1"/></svg><i class="ring"></i>')
  const { view } = useStage()
  useFrame(() => {
    if (t <= 0) { el.style.opacity = '0'; return }
    const q = project(view(), along(path, t))
    el.style.opacity = '1'
    el.style.transform = `translate(${Math.round(q[0])}px, ${Math.round(q[1])}px)`
    const ring = el.querySelector('.ring') as HTMLElement
    const k = click != null && t >= click ? Math.min(1, (t - click) / Math.max(1e-3, 1 - click)) : -1
    ring.style.opacity = k < 0 ? '0' : String(1 - k)
    ring.style.transform = `translate(-50%, -50%) scale(${k < 0 ? 0.4 : 0.4 + k * 0.6})`
  })
  return null
}
