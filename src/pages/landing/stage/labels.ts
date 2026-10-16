/**
 * LabelLayer (storyboard §1.6, F2.9): DOM callouts (screen-aligned, mono caps, leader + square dot) and
 * surface text (SVG text on an object's plane through an exact affine matrix). aria-hidden: everything it
 * says is also in the copy or the beat list. Driven by the director; works over WebGL or the poster.
 */
import { makeView, project, type View } from './camera'
import { boxesOf, type AABB } from './assign'
import type { Director, Display } from './director'
import { CAP_RATIO, FONT_CSS, TEXT_SCALE, placeText, resolveText } from './surfaceText'
import type { Beat, CalloutSpec, Layout, SurfaceText, Vec3 } from './types'

const SVGNS = 'http://www.w3.org/2000/svg'
const DIRS: Record<NonNullable<CalloutSpec['side']>, [number, number]> = {
  n: [0, -1], ne: [0.7071, -0.7071], e: [1, 0], se: [0.7071, 0.7071], s: [0, 1], sw: [-0.7071, 0.7071], w: [-1, 0], nw: [-0.7071, -0.7071],
}
const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t) }

interface CalloutEl { el: HTMLDivElement; chip: HTMLDivElement; b: HTMLElement; sub: HTMLElement; lead: HTMLElement; text: string; subText: string; w: number; h: number; on: boolean }
interface TextEl { svg: SVGSVGElement; t: SVGTextElement; text: string }

let measureCtx: CanvasRenderingContext2D | null = null
function measure(font: string, text: string): number {
  if (!measureCtx) measureCtx = document.createElement('canvas').getContext('2d')
  if (!measureCtx) return text.length * 8
  measureCtx.font = font
  return measureCtx.measureText(text).width
}

export class LabelLayer {
  root: HTMLElement
  private director: Director
  private callouts = new Map<string, CalloutEl>()
  private texts = new Map<string, TextEl>()
  private boxCache = new WeakMap<Layout, Map<string, AABB>>()
  showCallouts = true
  plateRight = 0                 // CSS px, callouts keep 24 px clear of the caption plate

  constructor(root: HTMLElement, director: Director) {
    this.root = root
    this.director = director
    root.setAttribute('aria-hidden', 'true')
  }

  destroy() { this.root.replaceChildren(); this.callouts.clear(); this.texts.clear() }

  private boxes(l: Layout) {
    let b = this.boxCache.get(l)
    if (!b) { b = boxesOf(l.cells); this.boxCache.set(l, b) }
    return b
  }

  anchorOf(a: CalloutSpec['anchor'], layout: Layout): Vec3 | null {
    if (Array.isArray(a)) return a
    const b = this.boxes(layout).get(a.group)
    if (!b) return null
    const cx = (b.min[0] + b.max[0]) / 2, cy = (b.min[1] + b.max[1]) / 2, cz = (b.min[2] + b.max[2]) / 2
    switch (a.at) {
      case 'top': return [cx, b.max[1], cz]
      case 'bottom': return [cx, b.min[1], cz]
      case 'front': return [cx, cy, b.max[2]]
      default: return [cx, cy, cz]
    }
  }

  update(d: Display = this.director.display()) {
    const vp = this.director.viewport
    const view = makeView(d.cam, vp)
    this.updateTexts(d, view)
    this.updateCallouts(d, view)
  }

  /* ───────── surface text ───────── */
  private updateTexts(d: Display, view: View) {
    const dir = this.director
    const seen = new Set<string>()
    const scenes = d.kind === 'scroll' && d.fromScene !== d.scene ? [d.fromScene, d.scene] : [d.scene]
    for (const si of scenes) {
      const mod = dir.scenes[si]
      const state = dir.sceneState(si)
      const toBeat = (mod.beats[d.beat] as Beat<any>)?.id
      const fromBeat = (dir.scenes[d.fromScene].beats[d.fromBeat] as Beat<any>)?.id
      for (const st of mod.surface as SurfaceText[]) {
        let alpha = 0, chars = -1
        const inTo = si === d.scene && st.beats.includes(toBeat)
        const inFrom = si === d.fromScene && st.beats.includes(fromBeat)
        if (d.kind === 'build') alpha = inTo ? smooth(0.88, 1, d.m) : 0
        else if (d.kind === 'rest') alpha = inTo ? 1 : 0
        else if (d.kind === 'interaction') alpha = inTo ? 1 : 0
        else {
          if (inTo && st.reveal === 'type' && st.beats[0] === toBeat) { alpha = 1; chars = Math.floor(d.m * 1.15 * resolveText(st.text, state).length) }
          else alpha = Math.max(inTo ? smooth(0.9, 1, d.m) : 0, inFrom ? 1 - smooth(0, 0.08, d.m) : 0)
        }
        if (dir.reduced && alpha > 0) { alpha = 1; chars = -1 }
        const key = `${mod.id}/${st.id}`
        if (alpha <= 0.01) continue
        const pl = placeText(view, st)
        if (pl.capPx / CAP_RATIO < 9) continue           // hidden below 9 px rendered
        seen.add(key)
        let te = this.texts.get(key)
        if (!te) {
          const svg = document.createElementNS(SVGNS, 'svg') as SVGSVGElement
          svg.setAttribute('class', 'st')
          svg.setAttribute('width', '1'); svg.setAttribute('height', '1')
          const t = document.createElementNS(SVGNS, 'text') as SVGTextElement
          svg.appendChild(t)
          this.root.appendChild(svg)
          te = { svg, t, text: '' }
          this.texts.set(key, te)
        }
        let text = resolveText(st.text, state)
        const f = FONT_CSS[st.font]
        if (st.maxWidth) {
          const font = `${f.weight} ${pl.fontPx}px ${f.family}`
          const max = st.maxWidth * TEXT_SCALE
          if (measure(font, text) > max) { while (text.length > 1 && measure(font, text + '…') > max) text = text.slice(0, -1); text += '…' }
        }
        if (chars >= 0) text = text.slice(0, chars)
        if (te.text !== text) { te.t.textContent = text; te.text = text }
        te.t.setAttribute('class', `t-${st.tone} f-${st.font}`)
        te.t.setAttribute('font-size', pl.fontPx.toFixed(2))
        te.t.setAttribute('text-anchor', st.align === 'centre' ? 'middle' : st.align === 'right' ? 'end' : 'start')
        te.svg.style.transform = `matrix(${pl.a},${pl.b},${pl.c},${pl.d},${pl.e},${pl.f})`
        te.svg.style.opacity = alpha >= 0.99 ? '' : alpha.toFixed(3)
        te.svg.style.display = ''
      }
    }
    for (const [k, te] of this.texts) if (!seen.has(k)) te.svg.style.display = 'none'
  }

  /* ───────── callouts ───────── */
  private updateCallouts(d: Display, view: View) {
    const dir = this.director
    const mod = dir.scenes[d.scene]
    const beatId = (mod.beats[d.beat] as Beat<any>)?.id
    const show = this.showCallouts && d.kind !== 'build' && (d.kind !== 'scroll' || d.m >= 0.85)
    const layout = dir.layoutOf(d.to)
    const state = dir.sceneState(d.scene)
    const narrow = view.w < 480
    const vp = dir.viewport
    const safe = { x0: Math.max(vp.safe.x0, this.plateRight + 24), y0: vp.safe.y0, x1: vp.safe.x1, y1: vp.safe.y1 }
    const want: { spec: CalloutSpec; key: string; p: [number, number, number]; text: string; sub: string }[] = []
    if (show) {
      for (const c of mod.callouts) {
        if (!c.beats.includes(beatId)) continue
        const a = this.anchorOf(c.anchor, layout)
        if (!a) continue
        const text = narrow && c.short ? c.short : resolveText(c.text, state)
        const sub = narrow ? '' : c.sub ? resolveText(c.sub, state) : ''
        want.push({ spec: c, key: `${mod.id}/${c.id}`, p: project(view, a), text, sub })
      }
    }
    if (dir.pickTip && d.atRest && dir.hover?.scene === d.scene) {
      const a = dir.pickTip.anchor
      want.push({ spec: { id: 'pick', anchor: a, text: dir.pickTip.text, sub: dir.pickTip.sub, tone: 'accent', side: 'ne', priority: 10, beats: [] }, key: 'pick', p: project(view, a), text: dir.pickTip.text, sub: narrow ? '' : dir.pickTip.sub ?? '' })
    }
    want.sort((a, b) => (b.spec.priority ?? 0) - (a.spec.priority ?? 0))
    const placed: { x0: number; y0: number; x1: number; y1: number }[] = []
    const seen = new Set<string>()
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const rnd = (v: number) => Math.round(v * dpr) / dpr
    for (const w of want) {
      let ce = this.callouts.get(w.key)
      if (!ce) { ce = this.makeCallout(); this.callouts.set(w.key, ce) }
      if (ce.text !== w.text || ce.subText !== w.sub) {
        ce.b.textContent = w.text; ce.sub.textContent = w.sub; ce.sub.style.display = w.sub ? '' : 'none'
        ce.text = w.text; ce.subText = w.sub
        ce.w = ce.chip.offsetWidth || w.text.length * 7.5 + 16; ce.h = ce.chip.offsetHeight || (w.sub ? 34 : 20)
      }
      const side = w.spec.side ?? 'ne'
      const [dx, dy] = DIRS[side]
      const lead = Math.min(40, Math.max(14, w.spec.lead ?? 22))
      let ax = w.p[0], ay = w.p[1]
      let ex = ax + dx * lead, ey = ay + dy * lead
      // chip box relative to the leader end
      let cx = dx < -0.1 ? ex - ce.w : dx > 0.1 ? ex : ex - 1
      let cy = dy < -0.1 ? ey - ce.h : dy > 0.1 ? ey : ey - ce.h / 2
      // clamp into the safe rect
      const sx = Math.min(Math.max(cx, safe.x0), safe.x1 - ce.w) - cx
      const sy = Math.min(Math.max(cy, safe.y0), safe.y1 - ce.h) - cy
      cx += sx; cy += sy
      // collisions: nudge in 4 px steps (≤ 60 px), else hide
      let ok = false
      for (let k = 0; k <= 30 && !ok; k++) {
        const off = k === 0 ? 0 : (k % 2 ? -1 : 1) * Math.ceil(k / 2) * 4
        const box = { x0: cx, y0: cy + off, x1: cx + ce.w, y1: cy + off + ce.h }
        if (!placed.some(p => box.x0 < p.x1 && box.x1 > p.x0 && box.y0 < p.y1 && box.y1 > p.y0)) { ok = true; cy += off; placed.push(box) }
      }
      if (!ok || ax < -50 || ay < -50 || ax > view.w + 50 || ay > view.h + 50) continue
      seen.add(w.key)
      // leader from the anchor to the chip's rule
      const lx = (dx < -0.1 ? cx + ce.w : cx + 1) - ax, ly = (dy < -0.1 ? cy + ce.h : dy > 0.1 ? cy : cy + ce.h / 2) - ay
      const len = Math.hypot(lx, ly), ang = Math.atan2(ly, lx)
      ce.el.className = `co${w.spec.tone === 'accent' ? ' acc' : ''} on`
      ce.el.style.transform = `translate(${rnd(ax)}px, ${rnd(ay)}px)`
      ce.lead.style.width = `${len.toFixed(1)}px`
      ce.lead.style.transform = `rotate(${ang}rad)`
      ce.chip.style.transform = `translate(${rnd(cx - ax)}px, ${rnd(cy - ay)}px)`
      ce.on = true
    }
    for (const [k, ce] of this.callouts) if (!seen.has(k) && ce.on) { ce.el.classList.remove('on'); ce.on = false }
  }

  private makeCallout(): CalloutEl {
    const el = document.createElement('div'); el.className = 'co'
    const dot = document.createElement('i'); dot.className = 'dot'
    const lead = document.createElement('i'); lead.className = 'lead'
    const chip = document.createElement('div'); chip.className = 'chip'
    const b = document.createElement('b'); const sub = document.createElement('span')
    chip.append(b, sub); el.append(lead, dot, chip)
    this.root.appendChild(el)
    return { el, chip, b, sub, lead, text: '', subText: '', w: 0, h: 0, on: false }
  }
}
