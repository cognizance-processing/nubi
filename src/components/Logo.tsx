/**
 * Logo — the Nubi mark and the [mark]ubi lockup as inline SVG.
 *
 * The mark is the public/nubi.png silhouette redrawn as geometry: one ribbon of
 * constant width forms an n, flows through a bowl (the n's swash) and rises
 * into an arrow. The mark IS the "n" of nubi, so the lockup is the mark
 * followed by "ubi" (Instrument Sans SemiBold outlined to paths, same x-height
 * and baseline as the mark). Never set "nubi" after the mark. Everything is a
 * single colour, `currentColor`: ink in light, light ink in dark, white on an
 * accent tile. No request, no flash, crisp at any size.
 *
 * Props:
 *   size       number   mark width in px (default 32). The lockup is
 *                       size x 2.88 wide and size x 1.04 tall; x-height is
 *                       size x 0.69. Header 26 (75 x 27px), sidebar 24,
 *                       auth 22, footer 28.
 *   showName   boolean  true (default) renders the lockup, false the mark.
 *   variant    'lockup' | 'mark' | 'wordmark'  overrides showName. 'wordmark'
 *                       renders the lockup: the standalone "nubi" text is retired.
 *   duo        boolean  accent arrowhead. Large marketing only: it is ignored
 *                       below an x-height of 48px (size < 70).
 *   title      string   accessible name (default "Nubi"). Pass "" when the logo
 *                       is decorative next to visible text: it is then aria-hidden.
 *   className  string
 *
 * Minimums: lockup x-height 15px (size 22, about 63px wide); below that use the mark
 * alone, minimum 16px wide. Clear space: the x-height on all sides. Static files
 * for non-React contexts live in public/brand/. For string-built markup use
 * NUBI_MARK_SVG / NUBI_LOCKUP_SVG.
 */

import {
  NUBI_MARK_PATH,
  NUBI_MARK_VIEWBOX,
  NUBI_MARK_RIBBON_PATH,
  NUBI_MARK_HEAD_PATH,
  NUBI_LOCKUP_PATH,
  NUBI_LOCKUP_VIEWBOX,
} from './ui/logoPaths'

/* The path data is generated, not authored; re-export it for markup builders. */
// eslint-disable-next-line react-refresh/only-export-components
export {
  NUBI_MARK_PATH,
  NUBI_MARK_VIEWBOX,
  NUBI_MARK_RIBBON_PATH,
  NUBI_MARK_HEAD_PATH,
  NUBI_LOCKUP_PATH,
  NUBI_LOCKUP_VIEWBOX,
}

const ATTRS = 'xmlns="http://www.w3.org/2000/svg" fill="currentColor" aria-hidden="true" focusable="false"'

/** The mark as a complete SVG string (currentColor, aria-hidden, 362x286 viewBox). */
// eslint-disable-next-line react-refresh/only-export-components
export const NUBI_MARK_SVG =
  `<svg ${ATTRS} viewBox="${NUBI_MARK_VIEWBOX}" class="nubi-logo-svg"><path d="${NUBI_MARK_PATH}"/></svg>`

/** Mark with the arrowhead in the accent colour. Use only at 48px+ x-height (landing hero, OG). */
// eslint-disable-next-line react-refresh/only-export-components
export const NUBI_MARK_DUO_SVG =
  `<svg ${ATTRS} viewBox="${NUBI_MARK_VIEWBOX}" class="nubi-logo-svg"><path d="${NUBI_MARK_RIBBON_PATH}"/><path fill="var(--accent)" d="${NUBI_MARK_HEAD_PATH}"/></svg>`

/** The [mark]ubi lockup as one SVG string. */
// eslint-disable-next-line react-refresh/only-export-components
export const NUBI_LOCKUP_SVG =
  `<svg ${ATTRS} viewBox="${NUBI_LOCKUP_VIEWBOX}" class="nubi-logo-svg"><path d="${NUBI_MARK_PATH}"/><path d="${NUBI_LOCKUP_PATH}"/></svg>`

type LogoVariant = 'lockup' | 'mark' | 'wordmark'

export interface LogoProps {
  size?: number
  showName?: boolean
  variant?: LogoVariant
  duo?: boolean
  title?: string
  className?: string
}

/** Mark grid units: 362 wide, x-height 250. */
const MARK_W = 362
const MARK_RATIO = 286 / MARK_W
const [, , LOCKUP_W, LOCKUP_H] = NUBI_LOCKUP_VIEWBOX.split(' ').map(Number)
/** The duo arrowhead needs an x-height of 48px: size >= 48 * 362 / 250. */
const DUO_MIN_SIZE = (48 * MARK_W) / 250

function r(n: number) {
  return Math.round(n * 100) / 100
}

export default function Logo({
  size = 32,
  showName = true,
  variant,
  duo = false,
  title = 'Nubi',
  className = '',
}: LogoProps) {
  const v: LogoVariant = variant ?? (showName ? 'lockup' : 'mark')
  const lockup = v !== 'mark'
  const twoTone = duo && size >= DUO_MIN_SIZE

  const scale = size / MARK_W
  const width = lockup ? LOCKUP_W * scale : size
  const height = lockup ? LOCKUP_H * scale : size * MARK_RATIO
  const viewBox = lockup ? NUBI_LOCKUP_VIEWBOX : NUBI_MARK_VIEWBOX

  const decorative = !title
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={viewBox}
      width={r(width)}
      height={r(height)}
      fill="currentColor"
      className={`nubi-logo inline-block shrink-0 select-none ${className}`.trim()}
      role={decorative ? undefined : 'img'}
      aria-label={decorative ? undefined : title}
      aria-hidden={decorative ? true : undefined}
      focusable="false"
    >
      {twoTone ? (
        <>
          <path d={NUBI_MARK_RIBBON_PATH} />
          <path d={NUBI_MARK_HEAD_PATH} fill="var(--accent)" />
        </>
      ) : (
        <path d={NUBI_MARK_PATH} />
      )}
      {lockup && <path d={NUBI_LOCKUP_PATH} />}
    </svg>
  )
}
