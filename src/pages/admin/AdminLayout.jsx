/**
 * AdminLayout — chrome for the /admin portal (inside the authenticated
 * AppShell, gated by RequireSuperadmin).
 *
 * Renders:
 *   - a small "Admin" heading portaled into the shell topbar slot
 *     (same pattern as FlowsPage/DashboardEditor toolbars);
 *   - a page header + tab nav (Overview / Users / Organizations);
 *   - <Outlet /> for the child pages.
 */

import { createPortal } from 'react-dom'
import { NavLink, Outlet } from 'react-router-dom'
import { ShieldCheck } from 'lucide-react'
import { useUi } from '../../contexts/UiContext.jsx'
import RequireSuperadmin from './RequireSuperadmin.jsx'

const TABS = [
  { to: '/admin', label: 'Overview', end: true },
  { to: '/admin/users', label: 'Users' },
  { to: '/admin/orgs', label: 'Organizations' },
]

function AdminChrome() {
  const { topbarSlot } = useUi()

  const topbarHeading = (
    <div className="flex items-center gap-1.5 text-sm font-medium font-display text-fg">
      <ShieldCheck size={15} className="text-primary" />
      Admin
    </div>
  )

  return (
    <div className="min-h-full bg-bg">
      {topbarSlot && createPortal(topbarHeading, topbarSlot)}

      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        <header>
          <h1 className="font-display font-semibold text-2xl text-fg">Admin</h1>
          <p className="text-sm text-muted mt-1">
            Instance-wide overview of users, organizations and activity.
          </p>
        </header>

        <nav aria-label="Admin sections" className="flex items-center gap-1 border-b border-border">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                [
                  'px-3.5 py-2.5 -mb-px text-sm font-medium font-display border-b-2 transition-colors',
                  isActive
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted hover:text-fg',
                ].join(' ')
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>

        <Outlet />
      </div>
    </div>
  )
}

export default function AdminLayout() {
  return (
    <RequireSuperadmin>
      <AdminChrome />
    </RequireSuperadmin>
  )
}
