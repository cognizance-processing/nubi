/**
 * ScrollToTop — standard SPA scroll restoration for client-side navigation.
 *
 * React Router preserves window scroll across route changes, so navigating
 * e.g. from the bottom of the landing page to /pricing would land mid-page.
 * This component resets window scroll to the top whenever the pathname
 * changes — EXCEPT when the new location has a hash, in which case the
 * in-page anchor (e.g. /#pricing, /pricing#faq) is left to scroll itself.
 *
 * Mount once inside a layout that uses window scroll (MainLayout for the
 * public/marketing routes). The authenticated AppShell scrolls inside its
 * own container and is not affected. DocsPage keeps its own scroll handling
 * (it already scrolls to top on slug change); a redundant scroll-to-top on
 * the same tick is a no-op.
 *
 * Renders nothing.
 */

import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

export default function ScrollToTop() {
  const { pathname, hash } = useLocation()

  useEffect(() => {
    // A hash means the navigation targets an in-page anchor — let the
    // browser / page scroll to it instead of forcing the top.
    if (hash) return
    // 'instant' so the landing page's `html { scroll-behavior: smooth }`
    // can't turn the reset into a visible animated scroll.
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
  }, [pathname, hash])

  return null
}
