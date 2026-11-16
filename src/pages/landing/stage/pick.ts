/**
 * Picking (F2.10): a ray against the AABBs of the interactive groups of the displayed layout, at rest only.
 * Hover → director.setHover (uHover + the scene's pick.hover callout); click → scene.pick.click → state patch.
 * Works over WebGL and the poster alike.
 */
import { makeView, rayAt, rayBox } from './camera'
import { groupBoxes, type AABB } from './assign'
import type { Director } from './director'
import type { Layout } from './types'

export function attachPicking(el: HTMLElement, director: Director): () => void {
  const cache = new WeakMap<Layout, Map<string, AABB>>()
  const hit = (cx: number, cy: number): { scene: number; group: string } | null => {
    const d = director.display()
    if (!d.atRest) return null
    const layout = director.layoutOf(d.to)
    let boxes = cache.get(layout)
    if (!boxes) { boxes = groupBoxes(layout); cache.set(layout, boxes) }
    if (!boxes.size) return null
    const r = el.getBoundingClientRect()
    const view = makeView(d.cam, director.viewport)
    const ray = rayAt(view, cx - r.left, cy - r.top)
    let best: string | null = null, bt = Infinity
    for (const [g, b] of boxes) { const t = rayBox(ray.o, ray.d, b.min, b.max); if (t >= 0 && t < bt) { bt = t; best = g } }
    return best ? { scene: d.scene, group: best } : null
  }
  let raf = 0, lx = 0, ly = 0
  const move = (e: PointerEvent) => {
    if (e.pointerType === 'touch') return
    lx = e.clientX; ly = e.clientY
    if (raf) return
    raf = requestAnimationFrame(() => {
      raf = 0
      const h = hit(lx, ly)
      const d = director.display()
      director.setHover(h?.scene ?? d.scene, h?.group ?? null)
      const layout = director.layoutOf(d.to)
      el.style.cursor = h && layout.groups?.[h.group]?.click ? 'pointer' : ''
      director.touch()
    })
  }
  const leave = () => { director.setHover(director.display().scene, null); el.style.cursor = '' }
  const click = (e: MouseEvent) => {
    const h = hit(e.clientX, e.clientY)
    if (!h) return
    const mod = director.scenes[h.scene]
    const patch = mod.pick?.click?.(h.group, director.sceneState(h.scene))
    if (patch) director.setSceneState(h.scene, patch)
  }
  el.addEventListener('pointermove', move)
  el.addEventListener('pointerleave', leave)
  el.addEventListener('click', click)
  return () => { el.removeEventListener('pointermove', move); el.removeEventListener('pointerleave', leave); el.removeEventListener('click', click); cancelAnimationFrame(raf) }
}
