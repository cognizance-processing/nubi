/**
 * The R3F stage (storyboard §1.4–1.5, F2): one <Canvas orthographic legacy linear flat frameloop="demand">,
 * one instanced mesh of 4,096 cubes morphing between atlas rows on the GPU, a ground pass, an MRT
 * (colour + group id) target and the 2 CSS px object-contour composite. Lazy chunk: three + R3F live here.
 *
 * The frame is a pure function of the director's display(): uniforms and camera only, no per-frame work
 * proportional to N. Frames are on demand (scroll, state, hover, time-based morphs, ambient ≤ 30 fps).
 */
import { Suspense, createContext, useContext, useEffect, useMemo, useRef, useState, type MutableRefObject, type RefObject } from 'react'
import * as THREE from 'three'
import { Canvas, useFrame, useThree } from '@nubi/r3f'
import type { Director, Display } from './director'
import { isVariant } from './director'
import { Atlas } from './atlas'
import { N_INSTANCES } from './assign'
import { makeView, project, type View } from './camera'
import { readPalette, type Palette } from './palette'
import {
  COMPOSITE_FRAG, COMPOSITE_VERT, GROUND_FRAG, GROUND_VERT, applyPalette, createBoxMaterial, createInkMaterial, setMorph, type InkUniforms,
} from './inkMaterial'
import { resolve } from './story'
import type { Metrics } from '../debug'
import type { Beat, SceneModule } from './types'

// R3F 9.8.1 still constructs THREE.Clock, which three r183+ flags as deprecated on every Canvas. Route three's
// console through a filter that drops exactly that notice (everything else is forwarded unchanged).
if (!THREE.getConsoleFunction()) {
  THREE.setConsoleFunction((type: string, message: string, ...rest: unknown[]) => {
    if (type === 'warn' && message.startsWith('THREE.Clock: This module has been deprecated')) return
    ;(console as any)[type](message, ...rest)
  })
}

export interface StageProps {
  director: Director
  paletteRoot: RefObject<HTMLElement | null>
  onReady(): void
  onContextLost(lost: boolean): void
  metrics: MutableRefObject<Metrics>
  onFrame(): void
  /** debug: a camera-facing accent cube at the target for the colour probe */
  probe?: boolean
}

/* ───────────── context for scene extras ───────────── */

export interface StageCtx {
  director: Director
  palette: MutableRefObject<Palette>
  view(): View
  overlay(): HTMLElement | null
  gid(group: string, scene?: number): number
  dpr(): number
  invalidate(): void
  /** depth fade toward the background for receding crowds (world units from the eye); (0, 0) = off */
  setFade(near: number, far: number): void
}
const Ctx = createContext<StageCtx | null>(null)
export const useStage = () => { const c = useContext(Ctx); if (!c) throw new Error('useStage outside the stage'); return c }

/** Add a three object to the stage scene for the lifetime of the component (the stage uses no R3F JSX). */
export function useAdd(object: THREE.Object3D | null) {
  const { scene, invalidate } = useThree()
  useEffect(() => {
    if (!object) return
    scene.add(object); invalidate()
    return () => { scene.remove(object); invalidate() }
  }, [object, scene, invalidate])
}

/* ───────────── engine ───────────── */

function Engine({ director, paletteRoot, onReady, onContextLost, metrics, onFrame, setCtx }: StageProps & { setCtx(c: StageCtx): void }) {
  const { gl, camera, size, invalidate, setDpr, viewport } = useThree()
  const atlas = useMemo(() => new Atlas(director), [director])
  const [built, setBuilt] = useState(false)
  const palette = useRef<Palette>(readPalette(paletteRoot.current))
  const tier = useRef<{ dpr: number; ambient: boolean; soft: boolean; name: string }>({ dpr: Math.min(2, window.devicePixelRatio || 1), ambient: true, soft: false, name: 'A' })
  const ema = useRef(16), lastT = useRef(0), calmSince = useRef(0)

  // software GL (SwiftShader / llvmpipe): DPR 1, no ambient
  useEffect(() => {
    try {
      const ctx = gl.getContext()
      const ext = ctx.getExtension('WEBGL_debug_renderer_info')
      const r = String(ext ? ctx.getParameter(ext.UNMASKED_RENDERER_WEBGL) : ctx.getParameter(ctx.RENDERER))
      if (/swiftshader|llvmpipe|software/i.test(r)) { tier.current = { dpr: 1, ambient: false, soft: true, name: 'soft' }; setDpr(1) }
    } catch { /* ignore */ }
    director.ambientEnabled = tier.current.ambient
  }, [gl, setDpr, director])

  // atlas (async, yields between rows)
  useEffect(() => {
    let dead = false
    atlas.build().then(() => { if (!dead) setBuilt(true) })
    return () => { dead = true; atlas.dispose() }
  }, [atlas])

  // cubes: one instanced draw, BoxGeometry with per-face UVs; poses come from the atlas by gl_InstanceID
  const geo = useMemo(() => {
    const box = new THREE.BoxGeometry(1, 1, 1)
    const g = new THREE.InstancedBufferGeometry()
    g.index = box.index
    g.setAttribute('position', box.getAttribute('position'))
    g.setAttribute('normal', box.getAttribute('normal'))
    g.setAttribute('uv', box.getAttribute('uv'))
    g.instanceCount = N_INSTANCES
    return g
  }, [])
  const ink = useMemo(() => (built ? createInkMaterial(atlas.texture, palette.current) : null), [built, atlas])
  const groundMat = useMemo(() => new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3, vertexShader: GROUND_VERT, fragmentShader: GROUND_FRAG, depthTest: false, depthWrite: true,
    uniforms: {
      uInvViewProj: { value: new THREE.Matrix4() }, uViewProj: { value: new THREE.Matrix4() }, uInk: { value: new THREE.Vector3() }, uBg: { value: new THREE.Vector3() },
      uGrid: { value: 0.09 }, uCentre: { value: new THREE.Vector3() }, uMaskX: { value: 0 }, uMaskW: { value: 0 },
    },
  }), [])
  const tri = useMemo(() => { const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute([-1, -1, 0, 3, -1, 0, -1, 3, 0], 3)); return g }, [])

  // MRT target + contour composite
  const dprNow = viewport.dpr
  const rt = useMemo(() => {
    const t = new THREE.WebGLRenderTarget(1, 1, { count: 2, samples: dprNow < 2 ? 4 : 0, type: THREE.UnsignedByteType, depthBuffer: true })
    t.textures.forEach(x => { x.minFilter = THREE.NearestFilter; x.magFilter = THREE.NearestFilter; x.generateMipmaps = false })
    return t
  }, [dprNow])
  useEffect(() => () => rt.dispose(), [rt])
  const comp = useMemo(() => {
    const mat = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3, vertexShader: COMPOSITE_VERT, fragmentShader: COMPOSITE_FRAG, depthTest: false, depthWrite: false,
      uniforms: { tColor: { value: null }, tId: { value: null }, uPx: { value: new THREE.Vector2() }, uOff: { value: 1 }, uContour: { value: new THREE.Vector3() }, uAccent: { value: new THREE.Vector3() }, uContourA: { value: 0.9 }, uHover: { value: 0 }, uSel: { value: 0 }, uOn: { value: 1 } },
    })
    const scene = new THREE.Scene()
    const mesh = new THREE.Mesh(tri, mat); mesh.frustumCulled = false; scene.add(mesh)
    return { mat, scene, cam: new THREE.Camera() }
  }, [tri])

  // palette: CSS tokens → vec3 uniforms; re-read on theme change
  useEffect(() => {
    const upd = () => {
      palette.current = readPalette(paletteRoot.current)
      if (ink) applyPalette(ink.uniforms as InkUniforms, palette.current)
      const p = palette.current
      groundMat.uniforms.uInk.value.set(...p.edge); groundMat.uniforms.uBg.value.set(...p.bg); groundMat.uniforms.uGrid.value = p.grid
      comp.mat.uniforms.uContour.value.set(...p.contour); comp.mat.uniforms.uAccent.value.set(...p.accent); comp.mat.uniforms.uContourA.value = p.contourA
      invalidate()
    }
    upd()
    const mo = new MutationObserver(upd)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] })
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    mq.addEventListener('change', upd)
    return () => { mo.disconnect(); mq.removeEventListener('change', upd) }
  }, [ink, groundMat, comp, paletteRoot, invalidate])

  // context loss → poster; restore → re-upload atlas + palette
  useEffect(() => {
    const c = gl.domElement
    const lost = (e: Event) => { e.preventDefault(); onContextLost(true) }
    const restored = () => { if (atlas.texture) atlas.texture.needsUpdate = true; onContextLost(false); invalidate() }
    c.addEventListener('webglcontextlost', lost)
    c.addEventListener('webglcontextrestored', restored)
    return () => { c.removeEventListener('webglcontextlost', lost); c.removeEventListener('webglcontextrestored', restored) }
  }, [gl, atlas, onContextLost, invalidate])

  // director → frames
  useEffect(() => {
    let timer = 0
    const kick = () => { if (!director.paused) invalidate() }
    director.invalidate = kick
    const off = director.on(kick)
    // keep animating while something time-based runs (ambient ≤ 30 fps)
    const loop = () => {
      timer = window.setTimeout(loop, director.ambientActive() && tier.current.ambient ? 33 : 250)
      if (!director.paused && (director.animating() || director.building)) invalidate()
    }
    loop()
    return () => { off(); clearTimeout(timer); director.invalidate = () => {} }
  }, [director, invalidate])

  const ctx: StageCtx = useMemo(() => ({
    director, palette,
    view: () => makeView(director.display().cam, director.viewport),
    overlay: () => paletteRoot.current?.querySelector('.labels') ?? null,
    gid: (g, s) => atlas.gid(s ?? director.display().scene, g),
    dpr: () => tier.current.dpr,
    invalidate,
    setFade: (near, far) => { if (ink) { ink.uniforms.uFadeNear.value = near; ink.uniforms.uFadeFar.value = far; invalidate() } },
  }), [director, palette, paletteRoot, atlas, invalidate, ink])
  useEffect(() => setCtx(ctx), [ctx, setCtx])

  const readyFired = useRef(false)
  const ortho = camera as THREE.OrthographicCamera
  const vpm = useMemo(() => ({ vp: new THREE.Matrix4(), inv: new THREE.Matrix4() }), [])

  useFrame(({ gl: r, scene }) => {
    const t0 = performance.now()
    if (!ink || !atlas.ready) return
    const d: Display = director.display()
    const u = ink.uniforms as InkUniforms
    // morph uniforms
    const fromRow = atlas.rowOf(d.from, [d.to])
    const toRow = atlas.rowOf(d.to, [d.from])
    u.uFrom.value = fromRow; u.uTo.value = toRow
    setMorph(u, d.spec, d.kind === 'rest' ? 1 : d.m)
    // hover: lift + accent contour (140 ms)
    const h = director.hover
    const hg = h ? atlas.gid(h.scene, h.group) : 0
    u.uHover.value = d.atRest ? hg : 0
    if (h) {
      const gi = director.layoutOf(d.to).groups?.[h.group]
      u.uHoverLift.value.set(...(gi?.liftOnHover ?? [0, 0, 0.6]))
      u.uHoverT.value = Math.min(1, (performance.now() - h.t0) / 140)
    }
    comp.mat.uniforms.uHover.value = d.atRest ? hg : 0
    // propagation ring
    if (d.wave) { u.uWave.value.set(d.wave.centre[0], d.wave.centre[1], d.wave.centre[2], d.wave.radius); u.uWaveW.value = d.wave.width } else u.uWaveW.value = 0
    // camera
    const view = makeView(d.cam, director.viewport)
    ortho.position.set(...view.eye)
    ortho.up.set(0, 1, 0)
    ortho.lookAt(view.eye[0] - view.Z[0], view.eye[1] - view.Z[1], view.eye[2] - view.Z[2])
    ortho.left = view.left; ortho.right = view.right; ortho.top = view.top; ortho.bottom = view.bottom
    ortho.near = view.near; ortho.far = view.far
    ortho.updateProjectionMatrix(); ortho.updateMatrixWorld()
    // ground
    vpm.vp.multiplyMatrices(ortho.projectionMatrix, ortho.matrixWorldInverse); vpm.inv.copy(vpm.vp).invert()
    groundMat.uniforms.uViewProj.value.copy(vpm.vp); groundMat.uniforms.uInvViewProj.value.copy(vpm.inv)
    groundMat.uniforms.uCentre.value.set(...d.cam.target)
    const dpr = r.getPixelRatio()
    const vp = director.viewport
    groundMat.uniforms.uMaskX.value = vp.mobile ? 0 : (vp.safe.x0 - 48) * dpr
    groundMat.uniforms.uMaskW.value = vp.mobile ? 0 : 120 * dpr
    // passes: MRT (colour + id) → contour composite → screen
    const w = Math.max(1, Math.round(size.width * dpr)), hgt = Math.max(1, Math.round(size.height * dpr))
    if (rt.width !== w || rt.height !== hgt) rt.setSize(w, hgt)
    const bg = palette.current.bg
    r.setClearColor(new THREE.Color(bg[0], bg[1], bg[2]), 1)
    r.setRenderTarget(rt)
    r.clear(true, true, true)
    r.render(scene, ortho)
    r.setRenderTarget(null)
    comp.mat.uniforms.tColor.value = rt.textures[0]
    comp.mat.uniforms.tId.value = rt.textures[1]
    comp.mat.uniforms.uPx.value.set(1 / w, 1 / hgt)
    comp.mat.uniforms.uOff.value = Math.max(1, Math.round(dpr))
    r.render(comp.scene, comp.cam)
    director.markFrame()
    onFrame()
    // governor: frame-time EMA over consecutive frames → DPR 2 / 1.5 / 1, ambient off at the low tier
    const now = performance.now()
    const dt = now - lastT.current
    lastT.current = now
    if (dt < 100) {
      ema.current += (dt - ema.current) / 30
      const T = tier.current
      if (!T.soft) {
        if (ema.current > 28 && T.dpr > 1) { T.dpr = 1; T.ambient = false; T.name = 'C'; setDpr(1); calmSince.current = now }
        else if (ema.current > 20 && T.dpr > 1.5) { T.dpr = 1.5; T.name = 'B'; setDpr(1.5); calmSince.current = now }
        else if (ema.current < 17) {
          if (!calmSince.current) calmSince.current = now
          if (now - calmSince.current > 5000 && T.dpr < Math.min(2, window.devicePixelRatio || 1)) {
            T.dpr = Math.min(2, window.devicePixelRatio || 1, T.dpr + 0.5); T.ambient = true; T.name = T.dpr >= 2 ? 'A' : 'B'; setDpr(T.dpr); calmSince.current = now
          }
        } else calmSince.current = 0
        director.ambientEnabled = T.ambient
      }
    }
    const info = r.info.render
    metrics.current = {
      fps: dt > 0 && dt < 1000 ? 1000 / ema.current : 0, cpuMs: performance.now() - t0, calls: info.calls + 1, tris: info.triangles + 1,
      cells: Math.max(director.layoutOf(d.to).cells.length, director.layoutOf(d.from).cells.length), dpr, tier: tier.current.name, probe: metrics.current.probe,
    }
    if (!readyFired.current && d.atRest) { readyFired.current = true; onReady() }
  }, 1)

  useEffect(() => { if (built) invalidate() }, [built, invalidate])
  const ground = useMemo(() => { const m = new THREE.Mesh(tri, groundMat); m.frustumCulled = false; m.renderOrder = -10; return m }, [tri, groundMat])
  const cubes = useMemo(() => { if (!ink) return null; const m = new THREE.Mesh(geo, ink); m.frustumCulled = false; return m }, [geo, ink])
  useAdd(ground)
  useAdd(cubes)
  return null
}

/* ───────────── scene extras (Trace, Packets, Cursor …) ───────────── */

function ExtrasHost({ director }: { director: Director }) {
  const [, setTick] = useState(0)
  useEffect(() => {
    let raf = 0
    const off = director.on(() => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; setTick(t => t + 1) }) })
    const id = setInterval(() => { if (director.ambientActive()) setTick(t => t + 1) }, 33)
    return () => { off(); cancelAnimationFrame(raf); clearInterval(id) }
  }, [director])
  const d = director.display()
  const story = director.story
  const r = resolve(story, director.T)
  const shown = new Set([d.scene, d.fromScene])
  return (
    <>
      {[...shown].map(si => {
        const mod = director.scenes[si] as SceneModule<any>
        const X = mod.Extras
        if (!X) return null
        const beat = (mod.beats[si === d.scene ? d.beat : d.fromBeat] as Beat<any>).id
        const m = si === d.scene ? (d.kind === 'rest' ? 1 : d.m) : 1 - d.m
        return (
          <Suspense key={mod.id} fallback={null}>
            <X T={director.T} local={director.T - story.spans[si].t0} beat={beat} m={m} state={director.sceneState(si)} reduced={director.reduced} ambientT={director.ambientActive() ? director.ambientT() : 0} />
          </Suspense>
        )
      })}
      {void r}
    </>
  )
}

/** Colour probe (§1.5.6): an unlit accent cube whose +z face points straight at the camera, 6 units above the floor. */
function ProbeCube({ metrics }: { metrics: MutableRefObject<Metrics> }) {
  const { director, palette } = useStage()
  const mesh = useMemo(() => {
    const mat = createBoxMaterial(palette.current)
    const g = new THREE.BoxGeometry(1, 1, 1)
    g.setAttribute('aTone', new THREE.InstancedBufferAttribute(new Float32Array([2]), 1))
    g.setAttribute('aGid', new THREE.InstancedBufferAttribute(new Float32Array([9999]), 1))
    const m = new THREE.InstancedMesh(g, mat, 1); m.frustumCulled = false
    return m
  }, [palette])
  useAdd(mesh)
  useFrame(() => {
    applyPalette((mesh.material as THREE.ShaderMaterial).uniforms as InkUniforms, palette.current)
    const d = director.display(), v = makeView(d.cam, director.viewport)
    const c = new THREE.Vector3(d.cam.target[0], 6, d.cam.target[2])
    const m4 = new THREE.Matrix4().makeBasis(new THREE.Vector3(...v.X), new THREE.Vector3(...v.Y), new THREE.Vector3(...v.Z)).scale(new THREE.Vector3(6, 6, 6)).setPosition(c)
    mesh.setMatrixAt(0, m4); mesh.instanceMatrix.needsUpdate = true
    const q = project(v, [c.x, c.y, c.z])
    metrics.current = { ...metrics.current, probe: [q[0], q[1]] }
  })
  return null
}

export default function Stage(props: StageProps) {
  const [ctx, setCtx] = useState<StageCtx | null>(null)
  return (
    <Canvas
      orthographic legacy linear flat frameloop="demand" dpr={[1, 2]}
      camera={{ manual: true, near: 1, far: 600, position: [0, 0, 200] } as any}
      gl={{ antialias: false, alpha: false, powerPreference: 'high-performance', stencil: false }}
      style={{ position: 'absolute', inset: 0 }}
      aria-hidden="true"
    >
      <Engine {...props} setCtx={setCtx} />
      {ctx && <Ctx.Provider value={ctx}><ExtrasHost director={props.director} />{props.probe && <ProbeCube metrics={props.metrics} />}</Ctx.Provider>}
    </Canvas>
  )
}

export { isVariant }
