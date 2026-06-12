/**
 * AppSidebar — primary navigation sidebar for the authenticated app shell.
 *
 * Behaviour:
 *  - Collapsible to icon-only mode (controlled by UiContext.sidebarCollapsed)
 *  - Active-route highlight via NavLink isActive
 *  - ≥ 44px tap targets on every nav item
 *  - On mobile: rendered as an off-canvas drawer controlled by `mobileOpen` prop
 *
 * Nav items:
 *   Home         /home
 *   Connectors   /connectors
 *   Queries      /queries
 *   Dashboards   /dashboards
 *
 * Props:
 *   mobileOpen   {boolean}   — whether the mobile drawer is visible
 *   onMobileClose {Function} — called to close the mobile drawer
 */

import { Link, NavLink, useMatch } from 'react-router-dom'
import {
  Home,
  Plug,
  FileCode2,
  LayoutDashboard,
  Workflow,
  BellRing,
  CalendarClock,
  Table2,
  ChevronLeft,
  ChevronRight,
  Settings,
  Shield,
  BookOpen,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import WorkspaceSwitcher from './WorkspaceSwitcher.jsx'
import Logo from '../Logo.jsx'

// ---------------------------------------------------------------------------
// Nav item definitions
// ---------------------------------------------------------------------------

const NAV_ITEMS = [
  { label: 'Home',        to: '/home',        Icon: Home },
  { label: 'Connectors',  to: '/connectors',  Icon: Plug },
  { label: 'Data',        to: '/data',        Icon: Table2 },
  { label: 'Queries',     to: '/queries',     Icon: FileCode2 },
  { label: 'Dashboards',  to: '/dashboards',  Icon: LayoutDashboard },
  { label: 'Flows',       to: '/flows',       Icon: Workflow },
  { label: 'Watches',     to: '/watches',     Icon: BellRing },
  { label: 'Automations', to: '/automations', Icon: CalendarClock },
  // Secrets are flow-scoped (referenced by flow tasks via {{ secrets.NAME }}),
  // so they live inside the Flows workspace (/flows/secrets), not the global nav.
]

// ---------------------------------------------------------------------------
// Single nav item
// ---------------------------------------------------------------------------

function SidebarNavItem({ to, label, Icon: IconComponent, collapsed }) {
  const isActive = !!useMatch({ path: to, end: false })
  // Alias to a locally declared const so ESLint recognises the JSX usage.
  const NavItemIcon = IconComponent
  return (
    <NavLink
      to={to}
      className={`
        group flex items-center gap-3 px-3 rounded-xl
        min-h-[44px] text-sm font-medium
        transition-all duration-150
        focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1
        ${collapsed ? 'justify-center w-11 mx-auto' : 'w-full'}
        ${
          isActive
            ? 'bg-primary/10 text-primary dark:bg-primary/15'
            : 'text-muted hover:text-fg hover:bg-surface-2'
        }
      `}
      title={collapsed ? label : undefined}
      aria-label={label}
    >
      <NavItemIcon
        size={18}
        strokeWidth={isActive ? 2.2 : 1.8}
        className={`shrink-0 transition-colors ${isActive ? 'text-primary' : 'text-muted group-hover:text-fg'}`}
      />
      {!collapsed && (
        <span className="truncate leading-none">{label}</span>
      )}
      {/* Active indicator bar */}
      {isActive && !collapsed && (
        <span className="ml-auto block w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
      )}
    </NavLink>
  )
}

// ---------------------------------------------------------------------------
// Sidebar inner content — shared by desktop + mobile
// ---------------------------------------------------------------------------

function SidebarContent({ collapsed, showToggle = true }) {
  const { toggleSidebar } = useUi()
  // Superadmin flag may not exist on older user payloads — code defensively.
  const { user } = useAuth()
  const isSuperadmin = Boolean(user?.is_superadmin)

  return (
    <div className="flex flex-col h-full py-3">
      {/* Logo area */}
      <div
        className={`flex items-center mb-5 px-3 ${
          collapsed ? 'justify-center' : 'justify-between'
        }`}
      >
        {/* Logo links back to the public landing page */}
        <Link
          to="/"
          aria-label="Nubi — back to landing page"
          className="rounded-lg focus:outline-none focus:ring-2 focus:ring-ring"
        >
          {collapsed ? <Logo size={26} showName={false} /> : <Logo size={26} showName={true} />}
        </Link>

        {showToggle && (
          <button
            onClick={toggleSidebar}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={`
              flex items-center justify-center rounded-lg
              w-8 h-8 shrink-0
              text-muted hover:text-fg hover:bg-surface-2
              border border-border
              transition-colors duration-150
              focus:outline-none focus:ring-2 focus:ring-ring
              ${collapsed ? 'mt-0' : ''}
            `}
          >
            {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
          </button>
        )}
      </div>

      {/* Workspace switcher — one integrated unit: org › project (rich popover)
          plus a secondary environment pill (its own popover). Replaces the
          three previously-stacked dropdowns. */}
      <div className="mb-3">
        <WorkspaceSwitcher collapsed={collapsed} />
      </div>

      {/* Nav items — scroll internally so a long list never clips the pinned
          Settings/Docs nav below (selectors stay pinned above). */}
      <nav
        className={`flex flex-col gap-0.5 flex-1 min-h-0 overflow-y-auto ${collapsed ? 'items-center px-1' : 'px-2'}`}
        aria-label="App navigation"
      >
        {NAV_ITEMS.map(({ label, to, Icon }) => (
          <SidebarNavItem
            key={to}
            to={to}
            label={label}
            Icon={Icon}
            collapsed={collapsed}
          />
        ))}
      </nav>

      {/* Footer / version — sits above the Settings divider */}
      {!collapsed && (
        <div className="px-4 pb-2">
          <p className="text-[10px] text-muted/50 font-mono tracking-wide">
            nubi · beta
          </p>
        </div>
      )}

      {/* Secondary nav — pinned below the primary nav */}
      <nav
        className={`flex flex-col gap-0.5 mt-1 pt-2 border-t border-border ${collapsed ? 'items-center px-1' : 'px-2'}`}
        aria-label="Settings navigation"
      >
        {/* Docs — public documentation, available to every user */}
        <SidebarNavItem to="/docs" label="Docs" Icon={BookOpen} collapsed={collapsed} />
        {/* Superadmin-only link to the admin console (/admin) */}
        {isSuperadmin && (
          <SidebarNavItem to="/admin" label="Admin" Icon={Shield} collapsed={collapsed} />
        )}
        <SidebarNavItem to="/settings" label="Settings" Icon={Settings} collapsed={collapsed} />
      </nav>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Desktop sidebar
// ---------------------------------------------------------------------------

export function AppSidebarDesktop() {
  const { sidebarCollapsed } = useUi()

  return (
    <aside
      className={`
        hidden md:flex flex-col shrink-0
        bg-surface border-r border-border
        transition-all duration-200 ease-in-out
        ${sidebarCollapsed ? 'w-[60px]' : 'w-[220px]'}
      `}
      aria-label="Main sidebar"
    >
      <SidebarContent collapsed={sidebarCollapsed} showToggle={true} />
    </aside>
  )
}

// ---------------------------------------------------------------------------
// Mobile drawer sidebar
// ---------------------------------------------------------------------------

export function AppSidebarMobile({ open, onClose }) {
  return (
    <>
      {/* Backdrop */}
      <div
        className={`
          md:hidden fixed inset-0 z-40
          bg-black/40 backdrop-blur-sm
          transition-opacity duration-200
          ${open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}
        `}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel */}
      <aside
        className={`
          md:hidden fixed inset-y-0 left-0 z-50
          w-[240px] bg-surface border-r border-border
          transition-transform duration-250 ease-in-out
          ${open ? 'translate-x-0' : '-translate-x-full'}
          shadow-xl
        `}
        aria-label="Mobile navigation"
      >
        <SidebarContent collapsed={false} showToggle={false} />
      </aside>
    </>
  )
}

export default AppSidebarDesktop
