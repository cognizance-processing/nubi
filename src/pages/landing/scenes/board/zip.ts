/**
 * The "zip" transition INTO the board (storyboard §4.5): the two viewers' windows slide together and merge
 * into one wide board; their table rows re-flow into KPI plates and bars; the two people step back and sink
 * into the board's base. Pure: computed once at module load from the security scene's last layout (the exact
 * row the engine morphs from), so it stays correct when that scene changes.
 *
 * The engine's `rank` assignment pairs the k-th smallest key of the previous layout with the k-th smallest key
 * of ours. We hand it a key function that encodes an explicit pairing:
 *   [0, M)            matched pairs (frame → frame, screen → screen, table → plates and bars), nearest in normalised
 *                     window space: each window stretches onto its half of the board
 *   [M, M + P)        people → the outermost cells of the board's bottom bezel ("step back and sink")
 *   [M + P, …)        any extra source cells (only if the previous scene ever outgrows ours)
 *   1e6 + …           our cells nobody travels to: they appear in place (screens, numerals, plates)
 * and every cell of ours gets an explicit arrival `order`.
 */
import { kit } from '../../stage/kit'
import { presetState } from '../../stage/story'
import { TONE, type Cell, type Layout } from '../../stage/types'
import security from '../security'
import { APPEAR } from './layout'

const visible = (c: Cell) => { const s = c.s ?? [0.95, 0.95, 0.95]; return s[0] > 0 && s[1] > 0 && s[2] > 0 }
const contentKey = (c: Cell) => `${c.group}|${c.p.map(v => v.toFixed(3)).join(',')}|${c.tone}`
const byX = (a: Cell, b: Cell) => a.p[0] - b.p[0] || a.p[1] - b.p[1] || a.p[2] - b.p[2]

export interface ZipPlan { key: (c: Cell) => number; matched: number; people: number; source: number }

function previousLayout(): Cell[] {
  try {
    const last = security.beats.length - 1
    return security.beats[last].layout(kit, presetState(security as any, last)).cells
  } catch { return [] }
}

/**
 * Apply the zip to OUR board layout (mutates `dest`'s cells: order + kind for the morph INTO it) and return the
 * key function for MorphSpec.order. `dest` must be the exact object the beat returns (the key matches by identity).
 */
export function planZip(dest: Layout): ZipPlan {
  const src = previousLayout().filter(visible)
  const srcKey = new Map<string, number>()
  const dstKey = new Map<Cell, number>()

  // people (and their badges, lanyards, tokens) stand in front of the windows; names are a hint, depth decides
  const isPerson = (c: Cell) => /person|viewer|badge|lanyard|card|token/.test(c.group) || c.p[2] > 2.5
  const isScreen = (c: Cell) => /screen/.test(c.group) || c.tone === TONE.SCREEN
  const srcPeople = src.filter(isPerson).sort(byX)
  const srcScreen = src.filter(c => !isPerson(c) && isScreen(c)).sort(byX)
  const srcChrome = src.filter(c => !isPerson(c) && !isScreen(c) && (/^win|^station/.test(c.group) || c.tone === TONE.MID)).sort(byX)
  const srcContent = src.filter(c => !isPerson(c) && !isScreen(c) && !srcChrome.includes(c)).sort(byX)

  const backer = (c: Cell) => (c.s?.[0] ?? 1) > 2.5 && /^win$|^win-screen$|^card-|^kpi-[a-z]+$/.test(c.group) && !/^win-(tab|url)$/.test(c.group)
  const mine = dest.cells.filter(c => visible(c) && !backer(c))
  const isChrome = (c: Cell) => c.group === 'win' || c.group === 'win-tab' || c.group === 'win-url'
  const isContent = (c: Cell) => /^kpi-[a-z]+$/.test(c.group) || /^bar-\d+$/.test(c.group) || /^region-\d+$/.test(c.group)
  // catchers: the outermost bottom-bezel cells, half on each side (people step back and sink into the base)
  const bottom = mine.filter(c => c.group === 'win' && c.p[1] < 1).sort(byX)
  const nLeft = srcPeople.filter(c => c.p[0] < 0).length
  const nRight = srcPeople.length - nLeft
  const catchers = [...bottom.slice(0, nLeft), ...bottom.slice(bottom.length - nRight)].slice(0, srcPeople.length)
  const caught = new Set(catchers)
  const dChrome = mine.filter(c => isChrome(c) && !caught.has(c)).sort(byX)
  const dContent = mine.filter(isContent).sort(byX)
  const dScreen = mine.filter(c => c.group === 'win-screen').sort(byX)

  // normalised placement: each window maps onto its half of the board ((u, v) inside the window → inside the half)
  const body = src.filter(c => !isPerson(c))
  const bbox = (cs: Cell[]) => cs.reduce((b, c) => [Math.min(b[0], c.p[0]), Math.min(b[1], c.p[1]), Math.max(b[2], c.p[0]), Math.max(b[3], c.p[1])], [Infinity, Infinity, -Infinity, -Infinity])
  const boxL = bbox(body.filter(c => c.p[0] < 0)), boxR = bbox(body.filter(c => c.p[0] >= 0))
  const uvSrc = (c: Cell): [number, number] => {
    const b = c.p[0] < 0 ? boxL : boxR
    const u = (c.p[0] - b[0]) / Math.max(1e-6, b[2] - b[0]), v = (c.p[1] - b[1]) / Math.max(1e-6, b[3] - b[1])
    return [c.p[0] < 0 ? u * 0.5 : 0.5 + u * 0.5, v]
  }
  const uvDst = (c: Cell): [number, number] => [(c.p[0] + 28) / 56, c.p[1] / 34]

  let k = 0
  /** Greedy nearest pairing in (u, v); sources in x order so both windows fill evenly. */
  const pair = (S: Cell[], D: Cell[]) => {
    const usedS = new Set<Cell>(), usedD = new Set<Cell>()
    const du = D.map(uvDst)
    const free = new Uint8Array(D.length).fill(1)
    for (const c of S) {
      if (usedD.size >= D.length) break
      const [u, v] = uvSrc(c)
      let best = -1, bd = Infinity
      for (let j = 0; j < D.length; j++) { if (!free[j]) continue; const d = (du[j][0] - u) ** 2 * 4 + (du[j][1] - v) ** 2; if (d < bd) { bd = d; best = j } }
      if (best < 0) break
      free[best] = 0
      srcKey.set(contentKey(c), k); dstKey.set(D[best], k); k++
      usedS.add(c); usedD.add(D[best])
    }
    return { usedS, usedD }
  }
  const a = pair(srcChrome, dChrome), sc = pair(srcScreen, dScreen), b = pair(srcContent, dContent)
  const M = k
  srcPeople.forEach((c, j) => srcKey.set(contentKey(c), M + j))
  catchers.forEach((c, j) => dstKey.set(c, M + j))
  const extra = [...srcChrome.filter(c => !a.usedS.has(c)), ...srcScreen.filter(c => !sc.usedS.has(c)), ...srcContent.filter(c => !b.usedS.has(c))].sort(byX)
  extra.forEach((c, j) => srcKey.set(contentKey(c), M + srcPeople.length + j))

  // arrival orders (spread .6 → each cell travels for .4 of the morph). A zipper running left → right:
  // people step back and sink first, the frames slide together, the screen opens from the seam outward,
  // then table cells slide into plates and bars column by column, then the values arrive.
  const matchedD = new Set([...a.usedD, ...b.usedD])
  const sweep = (c: Cell) => Math.min(1, Math.max(0, (c.p[0] + 28) / 56))
  let r = 0
  for (const c of dest.cells) {
    if (caught.has(c)) { c.order = 0; continue }
    if (a.usedD.has(c)) { c.order = 0.04 + 0.12 * (1 - Math.abs(c.p[0]) / 28); continue }
    if (sc.usedD.has(c)) { c.order = 0.1 + 0.15 * (1 - Math.abs(c.p[0]) / 28); continue }
    if (matchedD.has(c)) { c.order = 0.3 + 0.4 * sweep(c); continue }
    if (!dstKey.has(c)) dstKey.set(c, 1e6 + r++)
    c.kind = visible(c) ? APPEAR : c.kind
    const seam = Math.min(1, Math.hypot(c.p[0] / 28, (c.p[1] - 17) / 17) / 1.2)
    if (backer(c)) c.order = c.group === 'win' ? 0.2 : c.group === 'win-screen' ? 0.3 : 0.36 + 0.4 * sweep(c)
    else if (c.group === 'win-screen') c.order = 0.4 + 0.3 * seam
    else if (c.group.startsWith('win')) c.order = 0.3 + 0.25 * Math.abs(c.p[0]) / 28
    else if (/^card-|^kpi-[a-z]+$|^pill$|axis/.test(c.group)) c.order = 0.36 + 0.4 * sweep(c)
    else c.order = 0.6 + 0.4 * sweep(c)
  }

  const key = (c: Cell) => dstKey.get(c) ?? srcKey.get(contentKey(c)) ?? 5e5 + c.p[0]
  return { key, matched: M, people: srcPeople.length, source: src.length }
}
