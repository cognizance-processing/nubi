/**
 * MainLayout — app shell: Navbar + page content + Footer.
 *
 * Uses the Nubi design-system tokens (bg-bg, text-fg, etc.).
 * Footer is rendered ONLY on marketing / auth routes.
 * It is hidden on app and docs surfaces so as not to clutter the UI.
 *
 * Routes that show the footer : /  /login  /register  /compare  (and 404)
 * Routes that hide the footer : /docs  /editor  /playground  /dashboard  /d/
 */

import { Outlet, useLocation } from 'react-router-dom'
import Navbar from '../components/Navbar.jsx'
import Footer from '../components/Footer.jsx'

/** Prefixes whose pages should NOT display the marketing footer. */
const APP_PREFIXES = ['/docs', '/editor', '/playground', '/dashboard', '/d/']

function useHideFooter() {
  const { pathname } = useLocation()
  return APP_PREFIXES.some(
    (prefix) =>
      pathname === prefix ||
      pathname.startsWith(prefix + '/') ||
      pathname.startsWith(prefix),
  )
}

export default function MainLayout() {
  const hideFooter = useHideFooter()

  return (
    <div className="min-h-screen flex flex-col bg-bg text-fg">
      <Navbar />

      <main className="flex-1">
        <Outlet />
      </main>

      {!hideFooter && <Footer />}
    </div>
  )
}
