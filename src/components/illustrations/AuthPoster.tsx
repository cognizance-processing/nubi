/**
 * AuthPoster — the Login / Register art panel: the landing hero's store-performance board in a browser tab, drawn
 * once on a 2D canvas by the shared poster renderer from the hero scene's own layout data (storyboard §4.8).
 * Static and theme-aware (redrawn on theme change). No three.js, R3F, GSAP or Lenis: the hero layouts, the scene
 * module and the poster are pure TypeScript, so nothing heavier than ~10 kB of drawing code reaches the auth routes.
 */
import { useMemo } from 'react'
import LayoutPoster from '../../pages/landing/stage/LayoutPoster'
import hero from '../../pages/landing/scenes/hero/index'
import { HERO_INITIAL, heroBoardCamera, heroBoardLayout } from '../../pages/landing/scenes/hero/layouts'

export default function AuthPoster({ className, title }: { className?: string; title?: string; compact?: boolean }) {
  const { layout, texts } = useMemo(() => ({
    layout: heroBoardLayout(HERO_INITIAL),
    texts: hero.surface.filter(t => t.beats.includes(hero.poster)),
  }), [])
  return (
    <LayoutPoster
      className={className}
      layout={layout} camera={heroBoardCamera} texts={texts} state={HERO_INITIAL} fill={0.96}
      label={title ?? 'A browser tab showing the Acme store-performance dashboard: revenue $5.05M, 79.5k orders, a $63.46 average ticket and 3:44 fulfilment, with revenue bars and an orders line for 14 days, computed in the tab.'}
    />
  )
}
