/**
 * AppSidebar — primary navigation sidebar for the authenticated app shell.
 *
 * Behaviour:
 *  - Collapsible to icon-only mode (controlled by UiContext.sidebarCollapsed)
 *  - Active-route highlight via NavLink isActive + a left-rail indicator
 *  - ≥ 44px tap targets on every nav item
 *  - On mobile: rendered as an off-canvas drawer controlled by `mobileOpen` prop
 *
 * Nav items:
 *   Overview     /overview
 *   Workqueue    /workqueue
 *   Home         /home
 *   Connectors   /connectors
 *   Data         /data
 *   Queries      /queries
 *   Dashboards   /dashboards
 *   Canvases     /canvases
 *   Flows        /flows
 *   Watches      /watches
 *   Automations  /automations
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
  PanelTop,
  Compass,
  LayoutGrid,
  ListChecks,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import WorkspaceSwitcher from './WorkspaceSwitcher.jsx'
import Logo from '../Logo.jsx'

// ---------------------------------------------------------------------------
// Nav item definitions
// ---------------------------------------------------------------------------

const NAV_ITEMS = [
  { label: 'Overview',    to: '/overview',    Icon: LayoutGrid },
  { label: 'Workqueue',   to: '/workqueue',   Icon: ListChecks },
  { label: 'Home',        to: '/home',        Icon: Home },
  { label: 'Connectors',  to: '/connectors',  Icon: Plug },
  { label: 'Data',        to: '/data',        Icon: Table2 },
  { label: 'Queries',     to: '/queries',     Icon: FileCode2 },
  { label: 'Explore',     to: '/explore',     Icon: Compass },
  { label: 'Dashboards',  to: '/dashboards',  Icon: LayoutDashboard },
  { label: 'Canvases',    to: '/canvases',    Icon: PanelTop },
  { label: 'Flows',       to: '/flows',       Icon: Workflow },
  { label: 'Watches',     to: '/watches',     Icon: BellRing },
  { label: 'Automations', to: '/automations', Icon: CalendarClock },
]

// ---------------------------------------------------------------------------
// Single nav item
// ---------------------------------------------------------------------------

function SidebarNavItem({ to, label, Icon: IconComponent, collapsed }) {
  const isActive = !!useMatch({ path: to, end: false })
  const NavItemIcon = IconComponent
  return (
    <NavLink
      to={to}
      aria-current={isActive ? 'page' : undefined}
      aria-label={collapsed ? label : undefined}
      title={collapsed ? label : undefined}
      className={[
        // Base: relative for the rail indicator, flex layout, inset focus ring
        'group relative flex items-center gap-2.5 rounded-lg',
        'min-h-[2.25rem] text-[0.8125rem] font-medium leading-none',
        'outline-none select-none',
        'transition-[color,background-color] duration-120',
        'focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
        // Collapsed: icon-only square centred
        collapsed ? 'justify-center w-9 h-9 mx-auto px-0' : 'w-full px-3',
        // Active vs rest
        isActive
          ? 'bg-primary/10 text-primary font-semibold dark:bg-primary/15'
          : 'text-muted hover:text-fg hover:bg-surface-2/70',
      ].join(' ')}
    >
      {/* Active left-rail indicator — animated height */}
      {!collapsed && (
        <span
          aria-hidden="true"
          className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] rounded-full bg-primary"
          style={{
            height: isActive ? '1.125rem' : '0',
            opacity: isActive ? 1 : 0,
            transition: 'height 200ms cubic-bezier(0.22,1,0.36,1), opacity 200ms',
          }}
        />
      )}

      <NavItemIcon
        size={16}
        strokeWidth={isActive ? 2.25 : 1.85}
        aria-hidden="true"
        className={[
          'shrink-0 transition-colors duration-120',
          isActive ? 'text-primary' : 'text-muted group-hover:text-fg',
        ].join(' ')}
      />

      {!collapsed && (
        <span className="truncate leading-none tracking-[-0.005em]">{label}</span>
      )}
    </NavLink>
  )
}

// ---------------------------------------------------------------------------
// Sidebar inner content — shared by desktop + mobile
// ---------------------------------------------------------------------------

function SidebarContent({ collapsed, showToggle = true }) {
  const { toggleSidebar } = useUi()
  const { user } = useAuth()
  const isSuperadmin = Boolean(user?.is_superadmin)

  return (
    <div className="flex flex-col h-full">
      {/* Logo + collapse toggle */}
      <div
        className={[
          'flex items-center shrink-0 px-3 pt-3 pb-2',
          collapsed ? 'justify-center' : 'justify-between',
        ].join(' ')}
      >
        <Link
          to="/"
          aria-label="Nubi — back to landing page"
          className="rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {collapsed
            ? <Logo size={24} showName={false} />
            : <Logo size={24} showName={true} />}
        </Link>

        {showToggle && !collapsed && (
          <button
            onClick={toggleSidebar}
            aria-label="Collapse sidebar"
            className="
              flex items-center justify-center w-7 h-7 rounded-lg shrink-0
              text-muted hover:text-fg hover:bg-surface-2
              border border-border
              transition-colors duration-100
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
            "
          >
            <ChevronLeft size={13} aria-hidden="true" />
          </button>
        )}

        {showToggle && collapsed && (
          <button
            onClick={toggleSidebar}
            aria-label="Expand sidebar"
            className="
              mt-2 flex items-center justify-center w-7 h-7 rounded-lg shrink-0
              text-muted hover:text-fg hover:bg-surface-2
              border border-border
              transition-colors duration-100
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
            "
          >
            <ChevronRight size={13} aria-hidden="true" />
          </button>
        )}
      </div>

      {/* Workspace switcher */}
      <div className={collapsed ? 'mx-1 mb-2' : 'mx-2 mb-2'}>
        <WorkspaceSwitcher collapsed={collapsed} />
      </div>

      {/* Primary nav — scrolls internally */}
      <nav
        aria-label="App navigation"
        className={[
          'nubi-sidebar-scroll flex flex-col gap-0.5 flex-1 min-h-0 overflow-y-auto py-1',
          collapsed ? 'items-center px-1' : 'px-2',
        ].join(' ')}
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

      {/* Version label */}
      {!collapsed && (
        <div className="px-4 pb-1.5">
          <p className="text-[10px] text-muted/45 font-mono tracking-wide select-none">
            nubi · beta
          </p>
        </div>
      )}

      {/* Secondary nav — pinned at bottom */}
      <nav
        aria-label="Settings navigation"
        className={[
          'flex flex-col gap-0.5 pt-2 pb-2.5 border-t border-border',
          collapsed ? 'items-center px-1' : 'px-2',
        ].join(' ')}
      >
        <SidebarNavItem to="/docs"     label="Docs"     Icon={BookOpen} collapsed={collapsed} />
        {isSuperadmin && (
          <SidebarNavItem to="/admin"  label="Admin"    Icon={Shield}   collapsed={collapsed} />
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
      className={[
        'hidden md:flex flex-col shrink-0',
        'bg-surface border-r border-border',
        'transition-[width] duration-200 ease-in-out',
        sidebarCollapsed ? 'w-[56px]' : 'w-[216px]',
      ].join(' ')}
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
        className={[
          'md:hidden fixed inset-0 z-40',
          'bg-black/40 backdrop-blur-sm',
          'transition-opacity duration-200',
          open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none',
        ].join(' ')}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel */}
      <aside
        className={[
          'md:hidden fixed inset-y-0 left-0 z-50',
          'w-[240px] bg-surface border-r border-border',
          'transition-transform duration-250 ease-in-out',
          open ? 'translate-x-0' : '-translate-x-full',
          'shadow-nubi-xl',
        ].join(' ')}
        aria-label="Mobile navigation"
        aria-modal="true"
      >
        <SidebarContent collapsed={false} showToggle={false} />
      </aside>
    </>
  )
}

export default AppSidebarDesktop
