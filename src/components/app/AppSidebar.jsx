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

import { useState, useEffect, useRef } from 'react'
import { NavLink, useMatch } from 'react-router-dom'
import {
  Home,
  Plug,
  FileCode2,
  LayoutDashboard,
  Workflow,
  CalendarClock,
  Table2,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Building2,
  FolderGit2,
  Folder,
  Plus,
  Check,
  Settings,
  Shield,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import { useOrg } from '../../contexts/OrgContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import Logo from '../Logo.jsx'

// ---------------------------------------------------------------------------
// Org selector — lives in the sidebar (moved out of the topbar)
// ---------------------------------------------------------------------------

function SidebarOrgSelector({ collapsed }) {
  const { orgs, activeOrg, setActiveOrg } = useOrg()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function onDown(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  if (!activeOrg) return null

  return (
    <div className={`relative ${collapsed ? 'px-1' : 'px-2'}`} ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        aria-label="Switch organisation"
        aria-expanded={open}
        title={collapsed ? activeOrg.name : undefined}
        className={`
          flex items-center gap-2 rounded-xl border border-border
          bg-surface-2 hover:bg-surface text-sm font-medium text-fg
          transition-colors duration-150 min-h-[40px]
          focus:outline-none focus:ring-2 focus:ring-ring
          ${collapsed ? 'justify-center w-11 mx-auto px-0' : 'w-full px-2.5'}
        `}
      >
        <Building2 size={15} className="text-muted shrink-0" />
        {!collapsed && <span className="truncate flex-1 text-left">{activeOrg.name}</span>}
        {!collapsed && <ChevronDown size={13} className={`text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />}
      </button>

      {open && (
        <div className={`
          absolute z-50 mt-1 min-w-[180px] max-w-[240px] py-1.5 rounded-xl
          bg-surface border border-border shadow-lg shadow-black/10
          ${collapsed ? 'left-full top-0 ml-2' : 'left-2 right-2'}
        `}>
          <p className="px-3 py-1 text-[10px] font-semibold text-muted uppercase tracking-wider">Organisations</p>
          {orgs.map(org => (
            <button
              key={org.id}
              onClick={() => { setActiveOrg(org.id); setOpen(false) }}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-fg hover:bg-surface-2 transition-colors text-left min-h-[36px]"
            >
              <span className="flex-1 truncate">{org.name}</span>
              {org.id === activeOrg.id && <Check size={13} className="text-primary shrink-0" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Project selector — sits beneath the org selector (org → project → resources)
// ---------------------------------------------------------------------------

function SidebarProjectSelector({ collapsed }) {
  const { projects, activeProject, setActiveProject, createProject } = useProject()
  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function onDown(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  async function handleNewProject() {
    setOpen(false)
    const name = window.prompt('New project name')
    if (!name || !name.trim()) return
    setCreating(true)
    try {
      await createProject(name.trim())
    } catch (err) {
      console.error('Failed to create project:', err)
      window.alert(err?.message ?? 'Failed to create project')
    } finally {
      setCreating(false)
    }
  }

  if (!activeProject) return null

  return (
    <div className={`relative ${collapsed ? 'px-1' : 'px-2'}`} ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        aria-label="Switch project"
        aria-expanded={open}
        title={collapsed ? activeProject.name : undefined}
        className={`
          flex items-center gap-2 rounded-xl border border-border
          bg-surface-2 hover:bg-surface text-sm font-medium text-fg
          transition-colors duration-150 min-h-[36px]
          focus:outline-none focus:ring-2 focus:ring-ring
          ${collapsed ? 'justify-center w-11 mx-auto px-0' : 'w-full px-2.5'}
        `}
      >
        <FolderGit2 size={14} className="text-muted shrink-0" />
        {!collapsed && <span className="truncate flex-1 text-left">{activeProject.name}</span>}
        {!collapsed && <ChevronDown size={13} className={`text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />}
      </button>

      {open && (
        <div className={`
          absolute z-50 mt-1 min-w-[180px] max-w-[240px] py-1.5 rounded-xl
          bg-surface border border-border shadow-lg shadow-black/10
          ${collapsed ? 'left-full top-0 ml-2' : 'left-2 right-2'}
        `}>
          <p className="px-3 py-1 text-[10px] font-semibold text-muted uppercase tracking-wider">Projects</p>
          {projects.map(project => (
            <button
              key={project.id}
              onClick={() => { setActiveProject(project.id); setOpen(false) }}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-fg hover:bg-surface-2 transition-colors text-left min-h-[36px]"
            >
              <Folder size={13} className="text-muted shrink-0" />
              <span className="flex-1 truncate">{project.name}</span>
              {project.id === activeProject.id && <Check size={13} className="text-primary shrink-0" />}
            </button>
          ))}
          <div className="my-1 border-t border-border" />
          <button
            onClick={handleNewProject}
            disabled={creating}
            className="flex items-center gap-2 w-full px-3 py-2 text-sm text-muted hover:text-fg hover:bg-surface-2 transition-colors text-left min-h-[36px] disabled:opacity-50"
          >
            <Plus size={13} className="shrink-0" />
            <span className="flex-1 truncate">New project</span>
          </button>
        </div>
      )}
    </div>
  )
}

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
        {!collapsed && (
          <Logo size={26} showName={true} />
        )}
        {collapsed && (
          <Logo size={26} showName={false} />
        )}

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

      {/* Org selector (moved here from the topbar) */}
      <div className="mb-2">
        <SidebarOrgSelector collapsed={collapsed} />
      </div>

      {/* Project selector (org → project → resources) */}
      <div className="mb-3">
        <SidebarProjectSelector collapsed={collapsed} />
      </div>

      {/* Nav items */}
      <nav
        className={`flex flex-col gap-0.5 flex-1 ${collapsed ? 'items-center px-1' : 'px-2'}`}
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
