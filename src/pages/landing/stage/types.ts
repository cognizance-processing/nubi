/**
 * The scene contract (storyboard §2). Foundation-owned; scene modules in
 * src/pages/landing/scenes/<id>/ implement SceneModule against it.
 *
 * World units: 1 unit = 1 voxel, y up, floor at y = 0, the camera looks from the
 * front-left (+z is toward the viewer, -x is to the viewer's left).
 */
import type { FC } from 'react'
import type { Kit } from './kit'

export type SceneId = 'hero' | 'columns' | 'cache' | 'security' | 'board' | 'agents'
export type Vec3 = [number, number, number]

/** Tone indices. Fills come from CSS tokens; see the palette table (3.2). */
export const TONE = { PAPER: 0, INK: 1, ACCENT: 2, GHOST: 3, MID: 4, FLOOR: 5, ACCENT_SOFT: 6, SCREEN: 7, SHEET: 8, XRAY: 9 } as const

/** One cube. World units: 1 unit = 1 voxel, y up, floor at y = 0. */
export interface Cell {
  p: Vec3                 // centre
  s?: Vec3                // size of the unit cube (default [.95,.95,.95])
  tone: number            // TONE.*
  group: string           // stable object name within the scene ('bar-9', 'kpi-revenue', 'badge-a'); '' = no contour/picking
  yaw?: number            // rotation about y (cylinders, turned people)
  roll?: number           // rotation about z (lanyards)
  order?: number          // 0..1 arrival order for the morph INTO this layout (overrides MorphSpec.order)
  kind?: MorphKind        // per-cell override of the morph kind INTO this layout (e.g. hero build: frame rises, screen tiles flip)
  flight?: number         // per-cell tone shown while .15 < e < .85 (a flash: warehouse ring, LEDs, arriving screens)
  wave?: boolean          // cell reacts to the engine's uWave ring (board propagation): z-bump .5 + ACCENT_SOFT tint inside the band
}
export interface Layout { cells: Cell[]; groups?: Record<string, GroupInfo> }
export interface GroupInfo { hover?: boolean; click?: boolean; liftOnHover?: Vec3; tooltip?: string }

export type MorphKind = 'rise' | 'sink' | 'flip' | 'slide' | 'lift' | 'arc' | 'helix' | 'scale' | 'cut'
export interface MorphSpec {
  kind: MorphKind
  len: number             // scroll length in beats (scroll-driven) or ms (time-driven: build-in, interactions)
  spread?: number         // 0..0.85 stagger spread (default .45; the hero build uses ~.8 so its phases follow `order`)
  order?: 'y-desc' | 'y-asc' | 'x-asc' | 'x-desc' | 'z-asc' | 'z-desc' | 'radial' | 'random' | ((c: Cell) => number)
  from?: Vec3             // origin for 'radial'
  assign?: 'group' | 'rank' | 'nearest' // instance matching from the previous layout (default 'group' then 'nearest')
  arc?: number            // height for 'arc' / 'lift'
  helix?: { centre: Vec3; radius: number; turns: number }
  flightTone?: number
  toneAt?: number         // e at which destination tone shows (default .6)
}

export interface CameraKey { az: number; el: number; target: Vec3; zoom: number; mobile?: Partial<Omit<CameraKey, 'mobile'>> }

export interface CalloutSpec {
  id: string
  anchor: Vec3 | { group: string; at?: 'top' | 'centre' | 'bottom' | 'front' }
  text: string | ((s: any) => string)         // first line (rendered uppercase)
  sub?: string | ((s: any) => string)         // second line
  short?: string                              // ≤ 12 chars, canvas < 480px
  tone?: 'ink' | 'accent'
  side?: 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'nw'
  lead?: number                               // leader length px (default 22)
  priority?: number
  beats: string[]                             // visible at these beats (after the morph settles)
}

export interface SurfaceText {
  id: string
  at: Vec3                                    // baseline-left on the plane
  plane?: 'front' | 'top' | { right: Vec3; up: Vec3 } // default 'front' (faces +z)
  text: string | ((s: any) => string)
  font: 'title' | 'ui' | 'mono' | 'mono-strong'
  size: number                                // cap height in world units
  tone: 'ink' | 'muted' | 'accent' | 'onInk'
  align?: 'left' | 'centre' | 'right'
  maxWidth?: number
  reveal?: 'fade' | 'type'                    // 'type' = characters revealed with the beat's morph m (chat prompt, query bar)
  beats: string[]
}

export interface Beat<S> {
  id: string
  caption: string                             // ≤ 6 words, beat list + aria
  len: number                                 // rest length in beats (≥ .4)
  morph?: MorphSpec                           // INTO this beat (omit on beats[0]; the scene's `in` covers it)
  state?: Partial<S>                          // preset applied when entered by scroll (auto-demo)
  layout(kit: Kit, s: S): Layout              // pure, deterministic
  camera: CameraKey
  ambient?: { kind: 'packets' | 'scan' | 'none'; params?: object } // ≤ 5 s after last input; off in reduced motion
  wave?: { centre: Vec3; radius: [number, number]; width?: number } // engine ring driven by this beat's morph m (board)
}

export interface Control<S> {                 // DOM twin, rendered by the foundation into the copy
  id: string
  kind: 'button' | 'chips' | 'range' | 'select'
  label: string
  options?: { value: string; label: string }[]
  min?: number; max?: number; step?: number
  get(s: S): string | number
  set(s: S, v: string | number): Partial<S>
  announce?(s: S): string                     // aria-live text after a change
}

/** Props the foundation passes to a scene's Extras (rendered inside the R3F canvas, in the MRT pass). */
export interface ExtrasProps<S = any> { T: number; local: number; beat: string; m: number; state: S; reduced: boolean; ambientT: number }

export interface SceneModule<S = Record<string, unknown>> {
  id: SceneId
  index: string; kicker: string               // '02', 'CACHE'
  copy: {
    title: string | ((s: S) => string); body: string             // title/body may contain inline <em>/<b>; a function title is live (re-rendered on state change)
    facts?: [string, string][]; code?: string | ((s: S) => string)
    try: string; spec: { href: string; text?: string }; hint?: Record<string, string> // hero only
  }
  summary: string                             // canvas aria-label while active (one sentence)
  initialState: S
  in?: MorphSpec                              // from the previous scene's last beat into beats[0]; required for every scene except hero
  build?: { morph: MorphSpec }                // hero only: time-based build-in from the empty floor (replaces `in`)
  beats: Beat<S>[]
  callouts: CalloutSpec[]
  surface: SurfaceText[]
  controls: Control<S>[]
  pick?: {
    hover?(group: string, s: S): { anchor: Vec3; text: string; sub?: string } | null
    click?(group: string, s: S): Partial<S> | null
  }
  onState?(prev: S, next: S): MorphSpec | null   // how an interaction animates (time-based, ms)
  Extras?: FC<ExtrasProps<S>>
  poster: string                              // beat id for poster / no-WebGL / default reduced frame
}
