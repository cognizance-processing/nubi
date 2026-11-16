/**
 * BrandLoader — Nubi's branded loading indicator.
 *
 * The mark draws itself: the ribbon (n, through the u bowl, up the arrow stem)
 * is stroked in over 900ms with `stroke-dashoffset`, the arrowhead settles in
 * with a 2px upward nudge, the finished mark rests for 400ms, fades, and loops.
 * One 1700ms cycle, ink only: no ring, no gradient.
 *
 * Reduced motion: the static mark plus "Loading…" text (the keyframes are
 * removed in index.css; this component only supplies the markup).
 *
 * Usage:
 *   <BrandLoader />
 *   <BrandLoader size="lg" label="Loading your workspace…" />
 *   <BrandLoader size={96} />
 *
 * Props:
 *   size       'sm' | 'md' (default) | 'lg' | number (px, the mark width)
 *   label      string, caption below the mark; also the accessible name
 *   className  string
 */

import {
  NUBI_MARK_CENTERLINE,
  NUBI_MARK_CENTERLINE_LENGTH,
  NUBI_MARK_HEAD_PATH,
  NUBI_MARK_VIEWBOX,
} from './logoPaths'
import type { CSSProperties } from 'react'

const SIZES = { sm: 28, md: 44, lg: 72 }
const MARK_RATIO = 286 / 362

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

export default function BrandLoader({
  size = 'md',
  label = undefined,
  className = '',
}: {
  size?: 'sm' | 'md' | 'lg' | number
  label?: string
  className?: string
}) {
  const px = typeof size === 'number' ? size : (SIZES[size] ?? SIZES.md)
  // 2px on screen, expressed in viewBox units (362 units across px pixels)
  const nudge = Math.round((2 * 362) / px)
  const style = {
    '--len': NUBI_MARK_CENTERLINE_LENGTH,
    '--nudge': `${nudge}px`,
  } as CSSProperties

  return (
    <div
      className={cx('nubi-brand-loader inline-flex flex-col items-center justify-center gap-3', className)}
      role="status"
      aria-label={label || 'Loading'}
    >
      <svg
        className="nubi-brand-loader-svg"
        width={px}
        height={Math.round(px * MARK_RATIO * 100) / 100}
        viewBox={NUBI_MARK_VIEWBOX}
        aria-hidden="true"
        focusable="false"
        style={style}
      >
        <path
          className="nubi-brand-loader-ribbon"
          d={NUBI_MARK_CENTERLINE}
          fill="none"
          stroke="currentColor"
          strokeWidth="60"
          strokeLinecap="butt"
          strokeLinejoin="miter"
          strokeDasharray={NUBI_MARK_CENTERLINE_LENGTH}
          strokeDashoffset="0"
        />
        <path className="nubi-brand-loader-head" d={NUBI_MARK_HEAD_PATH} fill="currentColor" />
      </svg>
      {label ? (
        <span className="text-sm text-muted font-sans">{label}</span>
      ) : (
        <span className="nubi-brand-loader-rm t-label" aria-hidden="true">Loading…</span>
      )}
    </div>
  )
}
