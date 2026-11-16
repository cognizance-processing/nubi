/**
 * MainLayout — the public site shell: Navbar + page + Footer.
 *
 * Footer is rendered on marketing and auth-adjacent routes and hidden on the
 * docs (which carry their own sidebar and scroll). The landing page brings its
 * own header and renders the shared Footer itself.
 */

import { Outlet, useLocation } from 'react-router-dom'
import Navbar from '../components/Navbar.jsx'
import Footer from '../components/Footer.jsx'
import ScrollToTop from '../components/ScrollToTop.jsx'
import { CurrencyProvider } from '../contexts/CurrencyContext.jsx'

/** Prefixes whose pages should NOT display the marketing footer. */
const NO_FOOTER = ['/docs', '/editor', '/playground', '/dashboard', '/d/']

export default function MainLayout() {
  const { pathname } = useLocation()
  const isLanding = pathname === '/'
  const hideFooter = NO_FOOTER.some(p => pathname === p || pathname.startsWith(p + '/') || pathname.startsWith(p))

  return (
    <CurrencyProvider>
      <div className="min-h-screen flex flex-col bg-surface text-fg">
        {/* Reset window scroll on route change (public pages use window scroll) */}
        <ScrollToTop />
        {!isLanding && <Navbar />}

        <main id="main" className="flex-1" tabIndex={-1}>
          <Outlet />
        </main>

        {!hideFooter && !isLanding && <Footer />}
      </div>
    </CurrencyProvider>
  )
}
