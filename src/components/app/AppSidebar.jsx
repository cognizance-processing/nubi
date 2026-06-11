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
import { Link, NavLink, useMatch } from 'react-router-dom'
import {
  Home,
  Plug,
  Warehouse,
  FileCode2,
  LayoutDashboard,
  Workflow,
  Sigma,
  BellRing,
  CalendarClock,
  Gauge,
  Table2,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Building2,
  FolderGit2,
  Folder,
  GitBranch,
  Plus,
  Check,
  Settings,
  Shield,
  BookOpen,
  Lock,
  X,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import { useOrg } from '../../contexts/OrgContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useEnv, envDotClass } from '../../contexts/EnvContext.jsx'
import { buildEnvRows, isCustomEnv, normalizeEnvKey } from '../../shell/shellLogic.js'
import { getGitGraph } from '../../lib/gitenv.js'
import GitGraphDialog from './GitGraphDialog.jsx'
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

  // Render a sensible placeholder while projects load or when none exist yet,
  // rather than vanishing — so the control is always present and discoverable.
  const label = activeProject?.name ?? (projects.length ? 'Select project' : 'No project')

  return (
    <div className={`relative ${collapsed ? 'px-1' : 'px-2'}`} ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        aria-label="Switch project"
        aria-expanded={open}
        title={collapsed ? `Project: ${label}` : undefined}
        className={`
          flex items-center gap-2 rounded-xl border
          text-sm font-medium text-fg
          transition-colors duration-150 min-h-[40px]
          focus:outline-none focus:ring-2 focus:ring-ring
          ${open
            ? 'border-primary/40 bg-primary/5'
            : 'border-border bg-surface-2 hover:bg-surface hover:border-primary/30'}
          ${collapsed ? 'justify-center w-11 mx-auto px-0' : 'w-full px-2.5'}
        `}
      >
        <FolderGit2 size={15} className="text-primary shrink-0" />
        {!collapsed && (
          <span className="flex flex-col items-start min-w-0 flex-1 leading-tight">
            <span className="text-[9.5px] font-semibold uppercase tracking-wider text-muted">Project</span>
            <span className="truncate w-full text-left text-[13px]">{label}</span>
          </span>
        )}
        {!collapsed && <ChevronDown size={14} className={`text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />}
      </button>

      {open && (
        <div className={`
          absolute z-50 mt-1 min-w-[200px] max-w-[240px] py-1.5 rounded-xl
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
              {project.id === activeProject?.id && <Check size={13} className="text-primary shrink-0" />}
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
// Environment selector — sits beneath the project selector. The selected env
// is global app state (EnvContext) shared with the Flows toolbar EnvSelector.
// ---------------------------------------------------------------------------

function SidebarEnvSelector({ collapsed }) {
  const { environments, activeEnv, setActiveEnv, addEnv, removeEnv } = useEnv()
  const { activeProject } = useProject()
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  // Git additions: branch graph dialog + optional from-branch on create.
  const [graphOpen, setGraphOpen] = useState(false)
  // Branch list cache keyed by project so a project switch invalidates it
  // without a state-resetting effect.
  const [branchCache, setBranchCache] = useState(null) // { projectId, list }
  const [fromBranch, setFromBranch] = useState('')
  const ref = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    if (!open) return
    function onDown(e) { if (ref.current && !ref.current.contains(e.target)) { setOpen(false); setAdding(false) } }
    function onKey(e) { if (e.key === 'Escape') { setOpen(false); setAdding(false) } }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  useEffect(() => { if (adding) inputRef.current?.focus() }, [adding])

  // Feed the optional 'from branch' picker from the project's git graph the
  // first time the add form opens (graceful: null graph → no picker).
  useEffect(() => {
    if (!adding || !activeProject?.id) return
    if (branchCache?.projectId === activeProject.id) return
    let cancelled = false
    getGitGraph(activeProject.id).then(graph => {
      if (cancelled) return
      setBranchCache({
        projectId: activeProject.id,
        list: (graph?.branches ?? []).map(b => b.branch),
      })
    })
    return () => { cancelled = true }
  }, [adding, branchCache, activeProject?.id])

  // Guard the no-cache + no-project case: `undefined === undefined` would
  // otherwise be true and `null.list` would throw during the initial render.
  const branches = branchCache && branchCache.projectId === activeProject?.id
    ? branchCache.list
    : null

  // API mode once the project's environments loaded; before that (or when the
  // API is unavailable) the helper lists the standard pair so the control stays
  // usable, and always surfaces the active key (even a legacy localStorage one).
  const { apiMode, rows } = buildEnvRows(environments, activeEnv)

  function select(key) {
    setActiveEnv(key)
    setOpen(false)
    setAdding(false)
  }

  async function commitNew() {
    const key = normalizeEnvKey(draft)
    if (!key) return
    if (!rows.some(e => e.key === key)) {
      try {
        // Optionally seed the new env from an existing git branch.
        const created = await addEnv(key, fromBranch ? { from_branch: fromBranch } : {})
        if (created?.warning) window.alert(created.warning)
      } catch (err) {
        window.alert(err?.message ?? 'Could not create environment.')
        return
      }
    }
    setDraft('')
    setFromBranch('')
    select(key)
  }

  async function handleRemove(env, e) {
    e.stopPropagation()
    if (!window.confirm(`Delete environment "${env.key}" from this project?`)) return
    try {
      // EnvContext resets the selection to the default env when the active
      // one is removed. Throws on 409 (default/protected) — alert below.
      await removeEnv(env)
    } catch (err) {
      window.alert(err?.message ?? 'Could not delete environment.')
    }
  }

  return (
    <div className={`relative ${collapsed ? 'px-1' : 'px-2'}`} ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        aria-label="Switch environment"
        aria-haspopup="listbox"
        aria-expanded={open}
        title={collapsed ? `Environment: ${activeEnv}` : undefined}
        className={`
          flex items-center gap-2 rounded-xl border
          text-sm font-medium text-fg
          transition-colors duration-150 min-h-[40px]
          focus:outline-none focus:ring-2 focus:ring-ring
          ${open
            ? 'border-primary/40 bg-primary/5'
            : 'border-border bg-surface-2 hover:bg-surface hover:border-primary/30'}
          ${collapsed ? 'justify-center w-11 mx-auto px-0' : 'w-full px-2.5'}
        `}
      >
        <span className={`w-2 h-2 rounded-full shrink-0 ${envDotClass(activeEnv)}`} />
        {!collapsed && (
          <span className="flex flex-col items-start min-w-0 flex-1 leading-tight">
            <span className="text-[9.5px] font-semibold uppercase tracking-wider text-muted">Environment</span>
            <span className="truncate w-full text-left font-mono text-xs">{activeEnv}</span>
          </span>
        )}
        {!collapsed && <ChevronDown size={14} className={`text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />}
      </button>

      {open && (
        <div className={`
          absolute z-50 mt-1 min-w-[200px] max-w-[240px] py-1.5 rounded-xl
          bg-surface border border-border shadow-lg shadow-black/10
          ${collapsed ? 'left-full top-0 ml-2' : 'left-2 right-2'}
        `}>
          <div className="flex items-center px-3 py-1">
            <p className="text-[10px] font-semibold text-muted uppercase tracking-wider flex-1">Environments</p>
            <button
              type="button"
              onClick={() => { setOpen(false); setAdding(false); setGraphOpen(true) }}
              title="Branch graph"
              aria-label="Open git branch graph"
              className="w-6 h-6 flex items-center justify-center rounded-md text-muted/70 hover:text-fg hover:bg-surface-2 transition-colors shrink-0"
            >
              <GitBranch size={12} />
            </button>
          </div>
          <ul role="listbox" className="max-h-60 overflow-y-auto">
            {rows.map(env => {
              const isCustom = isCustomEnv(env, apiMode)
              return (
                <li key={env.key}>
                  <button
                    role="option"
                    aria-selected={env.key === activeEnv}
                    onClick={() => select(env.key)}
                    className="group flex items-center gap-2 w-full px-3 py-2 text-sm text-fg hover:bg-surface-2 transition-colors text-left min-h-[36px]"
                  >
                    <span className={`w-2 h-2 rounded-full shrink-0 ${envDotClass(env.key)}`} />
                    <span className="flex-1 min-w-0 leading-tight">
                      <span className="block truncate font-mono text-xs">{env.key}</span>
                      {/* Bound git branch (env ⇄ branch sync, see GitGraphDialog) */}
                      {env.git_branch && (
                        <span className="flex items-center gap-1 text-[10px] font-mono text-muted/60">
                          <GitBranch size={9} className="shrink-0" />
                          <span className="truncate">{env.git_branch}</span>
                        </span>
                      )}
                    </span>
                    {env.protected && (
                      <span title="Protected environment" className="shrink-0 flex items-center">
                        <Lock size={11} className="text-muted/60" />
                      </span>
                    )}
                    {isCustom && (
                      <span
                        role="button"
                        tabIndex={0}
                        onClick={(e) => handleRemove(env, e)}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleRemove(env, e) }}
                        title="Remove environment"
                        aria-label={`Remove environment ${env.key}`}
                        className="opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center rounded text-muted/60 hover:text-red-500 transition-colors shrink-0"
                      >
                        <X size={12} />
                      </span>
                    )}
                    {env.key === activeEnv && <Check size={13} className="text-primary shrink-0" />}
                  </button>
                </li>
              )
            })}
          </ul>
          {apiMode && (
            <>
              <div className="my-1 border-t border-border" />
              {adding ? (
                <div className="px-2 py-1 space-y-1">
                  <div className="flex items-center gap-1">
                    <input
                      ref={inputRef}
                      type="text"
                      value={draft}
                      placeholder="staging"
                      aria-label="New environment key"
                      className="h-7 flex-1 min-w-0 text-xs font-mono border border-border rounded-md px-2 bg-surface text-fg placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60"
                      onChange={e => setDraft(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') commitNew()
                        if (e.key === 'Escape') { setAdding(false); setDraft(''); setFromBranch('') }
                      }}
                    />
                    <button
                      onClick={commitNew}
                      className="h-7 px-2 rounded-md text-xs font-medium bg-primary text-primary-fg hover:opacity-90 transition-opacity shrink-0"
                    >
                      Add
                    </button>
                  </div>
                  {/* Optional: seed the env from an existing git branch */}
                  {Array.isArray(branches) && branches.length > 0 && (
                    <select
                      value={fromBranch}
                      onChange={e => setFromBranch(e.target.value)}
                      aria-label="Seed new environment from git branch (optional)"
                      className="h-7 w-full text-[11px] font-mono border border-border rounded-md px-1.5 bg-surface text-muted focus:outline-none focus:ring-2 focus:ring-ring/60"
                    >
                      <option value="">empty environment</option>
                      {branches.map(branch => (
                        <option key={branch} value={branch}>from branch: {branch}</option>
                      ))}
                    </select>
                  )}
                </div>
              ) : (
                <button
                  onClick={() => { setFromBranch(''); setAdding(true) }}
                  className="flex items-center gap-2 w-full px-3 py-2 text-sm text-muted hover:text-fg hover:bg-surface-2 transition-colors text-left min-h-[36px]"
                >
                  <Plus size={13} className="shrink-0" />
                  <span className="flex-1 truncate">Add environment</span>
                </button>
              )}
            </>
          )}
        </div>
      )}

      {/* Per-project commit graph + env branch sync actions */}
      <GitGraphDialog open={graphOpen} onClose={() => setGraphOpen(false)} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Nav item definitions
// ---------------------------------------------------------------------------

const NAV_ITEMS = [
  { label: 'Home',        to: '/home',        Icon: Home },
  { label: 'Connectors',  to: '/connectors',  Icon: Plug },
  { label: 'Lakehouse',   to: '/lakehouse',   Icon: Warehouse },
  { label: 'Data',        to: '/data',        Icon: Table2 },
  { label: 'Queries',     to: '/queries',     Icon: FileCode2 },
  { label: 'Dashboards',  to: '/dashboards',  Icon: LayoutDashboard },
  { label: 'Flows',       to: '/flows',       Icon: Workflow },
  { label: 'Metrics',     to: '/metrics',     Icon: Sigma },
  { label: 'Watches',     to: '/watches',     Icon: BellRing },
  { label: 'Automations', to: '/automations', Icon: CalendarClock },
  { label: 'Usage',       to: '/usage',       Icon: Gauge },
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

      {/* Org selector (moved here from the topbar) */}
      <div className="mb-2">
        <SidebarOrgSelector collapsed={collapsed} />
      </div>

      {/* Project selector (org → project → resources) */}
      <div className="mb-2">
        <SidebarProjectSelector collapsed={collapsed} />
      </div>

      {/* Environment selector (project → environment; global, see EnvContext) */}
      <div className="mb-3">
        <SidebarEnvSelector collapsed={collapsed} />
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
