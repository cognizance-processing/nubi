/**
 * ToolLogo - the one component that draws a third-party mark (database vendor,
 * AI provider, the open-source stack). The marks themselves are the vendors'
 * own SVGs in public/logos; see registry.ts and public/logos/SOURCES.md.
 *
 *   <ToolLogo slug="duckdb" />                          mono, follows currentColor
 *   <ToolLogo slug="snowflake" mode="brand" size={32} /> official colour (light/dark aware)
 *   <ToolLogo slug="github" title="Open on GitHub" />    named, otherwise decorative=false uses the vendor name
 *   <ToolLogo slug="postgres" decorative />              next to a visible text label
 *
 * - mono   fills with currentColor, so parents control it (ink at 60% in a
 *          proof band, full ink on hover or focus).
 * - brand  fills with the registry's hex on light surfaces and darkHex on dark
 *          ones (dark marks such as GitHub or Anthropic flip to a light variant).
 *          Colour comes from props and CSS variables, never from the file.
 * - Optical sizing: `size` is the square slot; the mark is drawn in it with its
 *   aspect ratio kept and its size adjusted by ink area (see opticalBox), so
 *   solid tiles and open line marks read at the same weight. Wide marks may
 *   overhang the slot sideways by up to 30%. Pass
 *   optical={false} to fill the slot's longest side instead.
 *
 * Flat marks are drawn as a CSS mask, so there is one request per file (cached)
 * and no inline-SVG bloat. Multi-colour service icons (AWS, Azure) are drawn as
 * an image, unmodified; in mono mode they are greyed.
 */

import type { CSSProperties } from 'react'
import { getToolLogo, opticalBox, type ToolSlug } from './registry'

export interface ToolLogoProps {
  /** Registry slug or alias (e.g. "duckdb", "postgres", "bigquery"). */
  slug: ToolSlug | (string & {})
  mode?: 'mono' | 'brand'
  /** Side of the square slot in px. Default 24. */
  size?: number
  /** Accessible name. Defaults to the vendor name. */
  title?: string
  /** Hide from assistive tech (use when the name is visible next to the mark). */
  decorative?: boolean
  /** Adjust the drawn size by ink area. Default true. */
  optical?: boolean
  className?: string
  style?: CSSProperties
}

export default function ToolLogo({
  slug,
  mode = 'mono',
  size = 24,
  title,
  decorative = false,
  optical = true,
  className = '',
  style,
}: ToolLogoProps) {
  const logo = getToolLogo(slug)
  const a11y = decorative
    ? ({ 'aria-hidden': true } as const)
    : ({ role: 'img', 'aria-label': title ?? logo?.name ?? slug } as const)

  // Unknown slug: say the name in plain mono text. We never draw a stand-in mark.
  if (!logo) {
    return (
      <span
        {...a11y}
        className={`inline-flex shrink-0 items-center justify-center font-mono uppercase tracking-[0.06em] text-[11px] leading-none ${className}`}
        style={{ minWidth: size, height: size, ...style }}
      >
        {title ?? slug}
      </span>
    )
  }

  const { w, h } = optical ? opticalBox(logo, size) : fitBox(logo.vb, size)
  const slot: CSSProperties = { width: size, height: size, ...style }
  const wrap = `inline-flex shrink-0 items-center justify-center ${className}`

  if (logo.kind === 'art') {
    return (
      <span {...a11y} className={wrap} style={slot}>
        <img
          src={logo.file}
          alt=""
          width={w}
          height={h}
          draggable={false}
          loading="lazy"
          decoding="async"
          style={{ width: w, height: h, flexShrink: 0, filter: mode === 'mono' ? 'grayscale(1)' : undefined }}
        />
      </span>
    )
  }

  // glyph + icon: flat artwork drawn through a mask.
  const brand = mode === 'brand' && logo.kind === 'glyph' && logo.hex
  const mask = `url(${logo.file})`
  const markStyle: CSSProperties & Record<string, string | number | undefined> = {
    display: 'block',
    flexShrink: 0,
    width: w,
    height: h,
    WebkitMaskImage: mask,
    maskImage: mask,
    WebkitMaskRepeat: 'no-repeat',
    maskRepeat: 'no-repeat',
    WebkitMaskPosition: 'center',
    maskPosition: 'center',
    WebkitMaskSize: '100% 100%',
    maskSize: '100% 100%',
    forcedColorAdjust: 'none',
  }
  if (brand) {
    markStyle['--tl-c'] = logo.hex as string
    markStyle['--tl-cd'] = (logo.darkHex ?? logo.hex) as string
  } else {
    markStyle.backgroundColor = 'currentColor'
  }

  return (
    <span {...a11y} className={wrap} style={slot}>
      <span
        aria-hidden="true"
        className={`${brand ? 'bg-[var(--tl-c)] dark:bg-[var(--tl-cd)] ' : ''}forced-colors:!bg-[CanvasText]`}
        style={markStyle}
      />
    </span>
  )
}

/** Longest side of the viewBox fills the slot. */
function fitBox(vb: readonly [number, number], size: number): { w: number; h: number } {
  const [vw, vh] = vb
  const s = size / Math.max(vw, vh)
  return { w: Math.round(vw * s * 100) / 100, h: Math.round(vh * s * 100) / 100 }
}
