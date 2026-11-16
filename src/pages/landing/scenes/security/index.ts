/**
 * 03 SECURITY — "Each viewer sees only their rows" (storyboard §4.4).
 * Two viewers open the same cached table. Each badge is a signed claim; the planner turns it into a predicate,
 * so rows outside it never reach that tab (they are drawn as an engineer's hidden lines). Swap the badges and
 * the rows follow. Pure data + pure functions: geometry lives in build.ts.
 */
import { lazy } from 'react'
import { kit } from '../../stage/kit'
import type { Beat, CalloutSpec, CameraKey, Control, MorphSpec, SceneModule, SurfaceText } from '../../stage/types'
import { REGIONS } from '../../stage/data'
import { ANCHOR, INITIAL, ROWS, enterOrderKey, orderKey, rowsFor, securityLayout, texts, type Claim, type SecurityState, type Side } from './build'

export type { SecurityState, Claim } from './build'

const ALL = ['same', 'claim', 'filter', 'swap']

/* camera: frontal, low, a touch from the left so the raised rows show their depth */
const cam = (o: Partial<CameraKey> = {}): CameraKey => ({
  az: -0.1, el: 0.2, target: [0, 9.4, 2], zoom: 0.87, mobile: { zoom: 1.1, target: [0, 9.2, 2] }, ...o,
})

const layout = (_k: unknown, s: SecurityState) => securityLayout(s)
/** The token's flight on `claim` (shared with Extras, which draws the same parabola). */
export const CLAIM_ARC = 5, CLAIM_SPREAD = 0.2

const preset = (p: Partial<SecurityState>): SecurityState => ({ ...INITIAL, claimed: true, filtered: true, ...p })

const beats: Beat<SecurityState>[] = [
  { id: 'same', caption: 'Two viewers, one cached table', len: 0.4, camera: cam(), layout },
  {
    id: 'claim', caption: 'Each badge is a signed claim', len: 0.4, camera: cam({ az: -0.12, zoom: 0.89 }), layout,
    state: { claimed: true, motion: 'token' },
    morph: { kind: 'scale', len: 0.8, arc: CLAIM_ARC, spread: CLAIM_SPREAD, assign: 'group', toneAt: 0.5 },
  },
  {
    id: 'filter', caption: 'Rows outside the claim never arrive', len: 0.5, camera: cam({ zoom: 0.89 }), layout,
    state: { filtered: true, motion: 'rows' },
    morph: { kind: 'scale', len: 0.7, arc: 0, spread: 0.62, assign: 'group', toneAt: 0.45, order: orderKey(preset({ motion: 'rows' })) },
  },
  {
    id: 'swap', caption: 'Swap badges: the rows follow', len: 0.5, camera: cam({ az: -0.08 }), layout,
    state: { a: 'EMEA', b: 'AMER', swapped: true, motion: 'cards' },
    morph: { kind: 'scale', len: 0.8, arc: 6, spread: 0.55, assign: 'group', toneAt: 0.45, order: orderKey(preset({ a: 'EMEA', b: 'AMER', swapped: true, motion: 'cards' })) },
  },
]

/* ───────────── interactions ───────────── */

const swap = (s: SecurityState): Partial<SecurityState> => ({ a: s.b, b: s.a, swapped: !s.swapped, claimed: true, filtered: true, motion: 'cards' })
const setClaim = (side: Side, v: Claim): Partial<SecurityState> => ({ [side]: v, claimed: true, filtered: true, motion: 'rows' })
const sees = (who: string, c: Claim) => (c === 'None' ? `${who} has no claim: 0 of ${ROWS} rows` : `${who} sees ${c} rows: ${rowsFor(c)} of ${ROWS}`)
const OPTIONS = [...REGIONS, 'None'].map(r => ({ value: r, label: r }))

const controls: Control<SecurityState>[] = [
  { id: 'swap', kind: 'button', label: 'Swap badges', get: s => s.a, set: s => swap(s), announce: s => `${sees('Viewer A', s.a)}. ${sees('Viewer B', s.b)}.` },
  { id: 'a', kind: 'chips', label: 'Viewer A claim', options: OPTIONS, get: s => s.a, set: (_s, v) => setClaim('a', v as Claim), announce: s => sees('Viewer A', s.a) },
  { id: 'b', kind: 'chips', label: 'Viewer B claim', options: OPTIONS, get: s => s.b, set: (_s, v) => setClaim('b', v as Claim), announce: s => sees('Viewer B', s.b) },
]

const onState = (_p: SecurityState, n: SecurityState): MorphSpec =>
  n.motion === 'cards'
    ? { kind: 'scale', len: 900, arc: 6, spread: 0.55, assign: 'group', toneAt: 0.45, order: orderKey(n) }
    : { kind: 'scale', len: 650, arc: 0, spread: 0.6, assign: 'group', toneAt: 0.45, order: orderKey(n) }

/* ───────────── labels ───────────── */

const claimJson = (c: Claim) => (c === 'None' ? '{ }' : `{ "region": "${c}" }`)
const callouts: CalloutSpec[] = [
  { id: 'same', anchor: ANCHOR.between, text: 'Same cached result', sub: `${ROWS} rows · one copy, two tabs`, short: 'SAME RESULT', side: 'n', lead: 30, priority: 3, beats: ['same'] },
  { id: 'va', anchor: ANCHOR.feet('a'), text: s => (s.a === 'None' ? 'No badge' : s.a), sub: 'Viewer A', side: 's', lead: 18, priority: 2, beats: ['same', 'filter', 'swap'] },
  { id: 'vb', anchor: ANCHOR.feet('b'), text: s => (s.b === 'None' ? 'No badge' : s.b), sub: 'Viewer B', side: 's', lead: 18, priority: 2, beats: ['same', 'filter', 'swap'] },
  { id: 'claim-a', anchor: ANCHOR.claimApex('a', CLAIM_ARC), text: 'Signed claim', sub: s => claimJson(s.a), short: 'CLAIM', tone: 'accent', side: 'n', lead: 24, priority: 3, beats: ['claim'] },
  { id: 'claim-b', anchor: ANCHOR.claimApex('b', CLAIM_ARC), text: 'Signed claim', sub: s => claimJson(s.b), short: 'CLAIM', side: 'n', lead: 24, priority: 2, beats: ['claim'] },
  { id: 'rows', anchor: { group: 'token-a', at: 'top' }, text: s => `${rowsFor(s.a)} of ${ROWS} rows`, sub: 'the rest never reach this tab', short: `${rowsFor('AMER')}/${ROWS} ROWS`, side: 'n', lead: 26, priority: 1, beats: ['filter', 'swap'] },
  { id: 'none-a', anchor: { group: 'void-a', at: 'top' }, text: 'No claim', sub: 'the query returns nothing', short: 'NO CLAIM', tone: 'accent', side: 'n', lead: 26, priority: 5, beats: ALL },
  { id: 'none-b', anchor: { group: 'void-b', at: 'top' }, text: 'No claim', sub: 'the query returns nothing', short: 'NO CLAIM', tone: 'accent', side: 'n', lead: 26, priority: 5, beats: ALL },
]

const beatsOf = { all: ALL, same: ['same'], claimed: ['claim', 'filter', 'swap'] }
const surface: SurfaceText[] = texts().map(({ when, ...t }) => ({ ...t, beats: beatsOf[when] }))

const security: SceneModule<SecurityState> = {
  id: 'security', index: '03', kicker: 'SECURITY',
  copy: {
    title: 'Each viewer sees only their rows.',
    body: 'Policies are claims in a token your backend signs. The planner adds them to the query plan as predicates, never by pasting text into SQL. If a claim is missing, the query returns nothing.',
    facts: [[`${Math.round(100 / REGIONS.length)}%`, 'of rows visible to each'], ['0', 'strings spliced into SQL'], ['Deny', 'when a claim is missing']],
    code: s => claimJson(s.a),
    try: 'Click a badge to swap them, or set each viewer’s claim:',
    spec: { href: '/docs/governance' },
  },
  summary: 'Two viewers with different badges see different rows of the same table.',
  initialState: INITIAL,
  // "step forward": the crowd and the warehouse sink, farthest first, while two stations grow into the big windows
  in: { kind: 'slide', len: 1.2, assign: 'group', spread: 0.6, order: enterOrderKey, arc: 0 },
  beats,
  callouts,
  surface,
  controls,
  pick: {
    hover: (g, s) => {
      const side: Side | null = /-a$/.test(g) || g === 'viewer-a' ? 'a' : /-b$/.test(g) || g === 'viewer-b' ? 'b' : g === 'card-1' || g === 'card-2' ? (g === (s.swapped ? 'card-2' : 'card-1') ? 'a' : 'b') : null
      if (!side) return null
      const c = s[side]
      return { anchor: ANCHOR.badge(side), text: c === 'None' ? 'No badge' : `Badge · ${c}`, sub: 'click to swap badges' }
    },
    click: (g, s) => (/^(viewer-[ab]|card-[12])$/.test(g) ? swap(s) : null),
  },
  onState,
  Extras: lazy(() => import('./Extras')),
  poster: 'filter',
}
export default security

/** Exposed for tests. */
export const _layoutFor = (beat: string, patch: Partial<SecurityState> = {}) => {
  const bi = beats.findIndex(b => b.id === beat)
  let s: SecurityState = { ...INITIAL }
  for (let i = 0; i <= bi; i++) s = { ...s, ...beats[i].state }
  return beats[bi].layout(kit, { ...s, ...patch })
}
