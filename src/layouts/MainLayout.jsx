/**
 * MainLayout — app shell: Navbar + page content + Footer.
 *
 * Uses the Nubi design-system tokens (bg-bg, text-fg, etc.).
 * Footer is rendered site-wide on every page inside this layout.
 */

import { Outlet } from 'react-router-dom'
import Navbar from '../components/Navbar.jsx'
import Footer from '../components/Footer.jsx'

export default function MainLayout() {
  return (
    <div className="min-h-screen flex flex-col bg-bg text-fg">
      <Navbar />

      <main className="flex-1">
        <Outlet />
      </main>

      <Footer />
    </div>
  )
}
