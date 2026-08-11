/**
 * BrandLoader — Nubi's branded loading indicator.
 *
 * A slim brand-gradient ring (navy → blue → teal) spins around the Nubi
 * mark while it breathes gently in place — a small crafted brand moment for
 * full-page / route-level loading states, instead of a generic spinner.
 *
 * Respects `prefers-reduced-motion`: rotation and scale are dropped in favour
 * of a slow, gentle opacity pulse on the whole mark.
 *
 * Usage:
 *   <BrandLoader />
 *   <BrandLoader size="lg" label="Loading your workspace…" />
 *   <BrandLoader size={96} />
 *
 * Props:
 *   size       'sm' | 'md' (default) | 'lg' | number (px)
 *   label      string — optional caption rendered below the mark; also used
 *              as the accessible name (default: "Loading")
 *   className  string — extra classes on the wrapper
 */

import { useId } from 'react'

const SIZES = { sm: 28, md: 44, lg: 72 }

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

export default function BrandLoader({ size = 'md', label = undefined, className = '' }) {
  const px = typeof size === 'number' ? size : (SIZES[size] ?? SIZES.md)
  const gradId = useId()
  const strokeWidth = Math.max(2, Math.round(px * 0.055))
  const radius = px / 2 - strokeWidth
  const circumference = 2 * Math.PI * radius
  const arc = circumference * 0.62
  const markSize = Math.round(px * 0.52)

  return (
    <div
      className={cx('nubi-brand-loader inline-flex flex-col items-center justify-center gap-2', className)}
      role="status"
      aria-label={label || 'Loading'}
    >
      <span className="nubi-brand-loader-ring-wrap" style={{ width: px, height: px }}>
        <svg
          className="nubi-brand-loader-ring"
          width={px}
          height={px}
          viewBox={`0 0 ${px} ${px}`}
          aria-hidden="true"
        >
          <defs>
            <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#1b2363" />
              <stop offset="55%" stopColor="#2456a6" />
              <stop offset="100%" stopColor="#17b3a3" />
            </linearGradient>
          </defs>
          <circle
            cx={px / 2}
            cy={px / 2}
            r={radius}
            fill="none"
            stroke={`url(#${gradId})`}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={`${arc} ${circumference - arc}`}
          />
        </svg>
        <img
          src="/nubi.png"
          alt=""
          draggable={false}
          className="nubi-brand-loader-mark"
          style={{ width: markSize, height: markSize }}
        />
      </span>
      {label && <span className="text-sm text-muted font-sans">{label}</span>}
    </div>
  )
}
