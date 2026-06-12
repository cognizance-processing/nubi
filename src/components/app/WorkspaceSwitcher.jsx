/**
 * WorkspaceSwitcher — the single, integrated workspace control for the app
 * sidebar. Replaces the three vertically-stacked dropdowns (Org / Project /
 * Environment) with one cohesive unit:
 *
 *   ┌─────────────────────────────────┐
 *   │ ▢ Acme Inc                    ⌄ │   ← org (muted, secondary)
 *   │ ▣ Billing Pipeline              │   ← project (primary, bold)
 *   ├─────────────────────────────────┤
 *   │ ● prod                        ⌄ │   ← env pill (secondary, runtime axis)
 *   └─────────────────────────────────┘
 *
 * The top region is ONE button (org › project breadcrumb) that opens ONE rich
 * popover with two sections — Organisations + Projects-for-the-active-org —
 * plus a "New project" action. The environment is a separate, clearly-secondary
 * pill on its own row (it's a different axis: runtime env, not a resource) that
 * opens its own compact popover carrying the richer env affordances (custom
 * envs, git-branch seeding, the commit graph, delete).
 *
 * Works in both the expanded sidebar and the collapsed icon-rail: collapsed, the
 * unit becomes a workspace icon-button with an env dot beneath, both opening
 * their popovers to the right of the rail.
 *
 * All switching is wired to the same context setters the old components used
 * (useOrg / useProject / useEnv) so behaviour is unchanged.
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import {
  ChevronsUpDown,
  ChevronDown,
  Building2,
  FolderGit2,
  Folder,
  GitBranch,
  Plus,
  Check,
  Lock,
  X,
} from 'lucide-react'
import { useOrg } from '../../contexts/OrgContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useEnv, envDotClass } from '../../contexts/EnvContext.jsx'
import { buildEnvRows, isCustomEnv, normalizeEnvKey } from '../../shell/shellLogic.js'
import { getGitGraph } from '../../lib/gitenv.js'
import GitGraphDialog from './GitGraphDialog.jsx'
import NewProjectDialog from './NewProjectDialog.jsx'

// ---------------------------------------------------------------------------
// Portaled panel shell — popovers render into document.body with a fixed
// position derived from the trigger rect, so the sidebar (or any scroll /
// overflow ancestor) can never clip them, and they get a real width instead
// of being squeezed to the 220px rail.
// ---------------------------------------------------------------------------

function usePanelPosition(anchorRef, collapsed, open) {
  const [pos, setPos] = useState(null)
  const recompute = useCallback(() => {
    const el = anchorRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setPos(
      collapsed
        ? { left: r.right + 10, top: Math.max(8, r.top) }
        : { left: r.left + 4, top: r.bottom + 6 }
    )
  }, [anchorRef, collapsed])
  useEffect(() => {
    if (!open) return
    // rAF for the initial measure: layout must settle first, and it keeps the
    // setState out of the synchronous effect body.
    const raf = requestAnimationFrame(recompute)
    window.addEventListener('resize', recompute)
    window.addEventListener('scroll', recompute, true)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', recompute)
      window.removeEventListener('scroll', recompute, true)
    }
  }, [open, recompute])
  return pos
}

function PanelShell({ pos, width, label, children }) {
  if (!pos) return null
  return createPortal(
    <div
      data-ws-panel
      role="dialog"
      aria-label={label}
      style={{ position: 'fixed', left: pos.left, top: pos.top, width, maxHeight: 'calc(100vh - 24px)' }}
      className="z-50 overflow-y-auto rounded-2xl bg-surface border border-border shadow-2xl shadow-black/20"
    >
      {children}
    </div>,
    document.body
  )
}

// ---------------------------------------------------------------------------
// Section header used inside the popovers
// ---------------------------------------------------------------------------

function SectionLabel({ children, action }) {
  return (
    <div className="flex items-center gap-2 px-3 pt-2 pb-1">
      <p className="flex-1 text-[10px] font-semibold text-muted uppercase tracking-wider">{children}</p>
      {action}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Org › Project popover — the rich, single workspace panel
// ---------------------------------------------------------------------------

function WorkspacePanel({ pos, onClose, onNewProject }) {
  const { orgs, activeOrg, setActiveOrg, createOrg } = useOrg()
  const { projects, activeProject, setActiveProject } = useProject()

  function handleNewProject() {
    onClose()
    onNewProject()
  }

  async function handleNewOrg() {
    onClose()
    const name = window.prompt('New organization name')
    if (!name || !name.trim()) return
    try {
      await createOrg(name.trim())
    } catch (err) {
      console.error('Failed to create organization:', err)
      window.alert(err?.message ?? 'Failed to create organization')
    }
  }

  return (
    <PanelShell pos={pos} width={288} label="Switch workspace">
      {/* Organisations — always visible (single-org users still see where they
          are and can create another org from here) */}
      <SectionLabel
        action={
          <button
            type="button"
            onClick={handleNewOrg}
            title="New organization"
            aria-label="Create a new organization"
            className="w-6 h-6 flex items-center justify-center rounded-md text-muted/70 hover:text-fg hover:bg-surface-2 transition-colors shrink-0"
          >
            <Plus size={12} />
          </button>
        }
      >
        Organization
      </SectionLabel>
      <ul className="px-1.5 pb-1 max-h-44 overflow-y-auto">
            {orgs.map(org => {
              const active = org.id === activeOrg?.id
              return (
                <li key={org.id}>
                  <button
                    onClick={() => setActiveOrg(org.id)}
                    className={`
                      group flex items-center gap-2.5 w-full px-2 py-1.5 rounded-lg text-left
                      min-h-[36px] transition-colors
                      ${active ? 'bg-surface-2' : 'hover:bg-surface-2'}
                    `}
                  >
                    <span className="flex items-center justify-center w-6 h-6 rounded-md bg-primary/10 text-primary shrink-0">
                      <Building2 size={13} />
                    </span>
                    <span className="flex-1 truncate text-sm font-medium text-fg">{org.name}</span>
                    {active && <Check size={14} className="text-primary shrink-0" />}
                  </button>
                </li>
              )
            })}
      </ul>
      <div className="mx-3 border-t border-border" />

      {/* Projects (for the active org) */}
      <SectionLabel>
        {activeOrg ? `Projects · ${activeOrg.name}` : 'Projects'}
      </SectionLabel>
      <ul className="px-1.5 pb-1 max-h-64 overflow-y-auto">
        {projects.length === 0 && (
          <li className="px-2 py-2 text-sm text-muted">No projects yet</li>
        )}
        {projects.map(project => {
          const active = project.id === activeProject?.id
          return (
            <li key={project.id}>
              <button
                onClick={() => { setActiveProject(project.id); onClose() }}
                className={`
                  group flex items-center gap-2.5 w-full px-2 py-1.5 rounded-lg text-left
                  min-h-[36px] transition-colors
                  ${active ? 'bg-primary/10' : 'hover:bg-surface-2'}
                `}
              >
                <span className={`
                  flex items-center justify-center w-6 h-6 rounded-md shrink-0
                  ${active ? 'bg-primary/15 text-primary' : 'bg-surface-2 text-muted group-hover:text-fg'}
                `}>
                  {active ? <FolderGit2 size={13} /> : <Folder size={13} />}
                </span>
                <span className={`flex-1 truncate text-sm ${active ? 'font-semibold text-fg' : 'font-medium text-fg'}`}>
                  {project.name}
                </span>
                {active && <Check size={14} className="text-primary shrink-0" />}
              </button>
            </li>
          )
        })}
      </ul>

      <div className="mx-3 border-t border-border" />
      <div className="p-1.5">
        <button
          onClick={handleNewProject}
          className="flex items-center gap-2.5 w-full px-2 py-1.5 rounded-lg text-left min-h-[36px] text-sm font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors"
        >
          <span className="flex items-center justify-center w-6 h-6 rounded-md border border-dashed border-border text-muted shrink-0">
            <Plus size={13} />
          </span>
          <span className="flex-1 truncate">New project</span>
        </button>
      </div>
    </PanelShell>
  )
}

// ---------------------------------------------------------------------------
// Environment popover — secondary axis, carries the richer env affordances
// ---------------------------------------------------------------------------

function EnvPanel({ pos, onClose, onOpenGraph }) {
  const { environments, activeEnv, setActiveEnv, addEnv } = useEnv()
  const { activeProject } = useProject()
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const [branchCache, setBranchCache] = useState(null) // { projectId, list }
  const [fromBranch, setFromBranch] = useState('')
  const inputRef = useRef(null)

  const { removeEnv } = useEnv()

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

  const branches = branchCache && branchCache.projectId === activeProject?.id
    ? branchCache.list
    : null

  const { apiMode, rows } = buildEnvRows(environments, activeEnv)

  function select(key) {
    setActiveEnv(key)
    onClose()
  }

  async function commitNew() {
    const key = normalizeEnvKey(draft)
    if (!key) return
    if (!rows.some(e => e.key === key)) {
      try {
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
      await removeEnv(env)
    } catch (err) {
      window.alert(err?.message ?? 'Could not delete environment.')
    }
  }

  return (
    <PanelShell pos={pos} width={264} label="Switch environment">
      <SectionLabel
        action={
          <button
            type="button"
            onClick={() => { onClose(); onOpenGraph() }}
            title="Branch graph"
            aria-label="Open git branch graph"
            className="w-6 h-6 flex items-center justify-center rounded-md text-muted/70 hover:text-fg hover:bg-surface-2 transition-colors shrink-0"
          >
            <GitBranch size={12} />
          </button>
        }
      >
        Environment
      </SectionLabel>
      <ul role="listbox" className="px-1.5 pb-1 max-h-60 overflow-y-auto">
        {rows.map(env => {
          const isCustom = isCustomEnv(env, apiMode)
          const active = env.key === activeEnv
          return (
            <li key={env.key}>
              <button
                role="option"
                aria-selected={active}
                onClick={() => select(env.key)}
                className={`
                  group flex items-center gap-2.5 w-full px-2 py-1.5 rounded-lg text-left
                  min-h-[36px] transition-colors
                  ${active ? 'bg-primary/10' : 'hover:bg-surface-2'}
                `}
              >
                <span className={`w-2 h-2 rounded-full shrink-0 ${envDotClass(env.key)}`} />
                <span className="flex-1 min-w-0 leading-tight">
                  <span className="block truncate font-mono text-xs text-fg">{env.key}</span>
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
                {active && <Check size={14} className="text-primary shrink-0" />}
              </button>
            </li>
          )
        })}
      </ul>
      {apiMode && (
        <>
          <div className="mx-3 border-t border-border" />
          {adding ? (
            <div className="px-2 py-2 space-y-1.5">
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
                  className="h-7 px-2.5 rounded-md text-xs font-medium bg-primary text-primary-fg hover:opacity-90 transition-opacity shrink-0"
                >
                  Add
                </button>
              </div>
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
            <div className="p-1.5">
              <button
                onClick={() => { setFromBranch(''); setAdding(true) }}
                className="flex items-center gap-2.5 w-full px-2 py-1.5 rounded-lg text-left min-h-[36px] text-sm font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors"
              >
                <span className="flex items-center justify-center w-5 h-5 shrink-0">
                  <Plus size={13} />
                </span>
                <span className="flex-1 truncate">Add environment</span>
              </button>
            </div>
          )}
        </>
      )}
    </PanelShell>
  )
}

// ---------------------------------------------------------------------------
// WorkspaceSwitcher — the integrated unit (button[s] + popovers)
// ---------------------------------------------------------------------------

export default function WorkspaceSwitcher({ collapsed }) {
  const { activeOrg } = useOrg()
  const { projects, activeProject } = useProject()
  const { activeEnv } = useEnv()

  // Only one popover open at a time: 'workspace' | 'env' | null
  const [openPanel, setOpenPanel] = useState(null)
  const [graphOpen, setGraphOpen] = useState(false)
  const [newProjectOpen, setNewProjectOpen] = useState(false)
  const ref = useRef(null)
  const panelPos = usePanelPosition(ref, collapsed, openPanel !== null)

  useEffect(() => {
    if (!openPanel) return
    function onDown(e) {
      // The panels render in a portal, so a plain contains() check would treat
      // clicks inside them as "outside" and close the panel mid-interaction.
      if (e.target.closest('[data-ws-panel]')) return
      if (ref.current && !ref.current.contains(e.target)) setOpenPanel(null)
    }
    function onKey(e) { if (e.key === 'Escape') setOpenPanel(null) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [openPanel])

  if (!activeOrg) return null

  const orgInitial = (activeOrg.name || '?').trim()[0]?.toUpperCase() ?? '?'

  const projectLabel = activeProject?.name ?? (projects.length ? 'Select project' : 'No project')
  const close = () => setOpenPanel(null)
  const toggle = (panel) => setOpenPanel(v => (v === panel ? null : panel))

  // -------------------------------------------------------------------------
  // Collapsed icon-rail: a workspace icon-button + an env dot button beneath.
  // -------------------------------------------------------------------------
  if (collapsed) {
    return (
      <div className="relative px-1" ref={ref}>
        <div className="flex flex-col items-center gap-1.5">
          <button
            onClick={() => toggle('workspace')}
            aria-label="Switch workspace"
            aria-expanded={openPanel === 'workspace'}
            title={`${activeOrg.name} · ${projectLabel}`}
            className={`
              relative flex items-center justify-center w-11 h-11 mx-auto rounded-xl border
              transition-colors focus:outline-none focus:ring-2 focus:ring-ring
              ${openPanel === 'workspace'
                ? 'border-primary/40 bg-primary/10 text-primary'
                : 'border-border bg-surface-2 hover:bg-surface text-primary'}
            `}
          >
            <FolderGit2 size={17} />
          </button>

          <button
            onClick={() => toggle('env')}
            aria-label={`Environment: ${activeEnv}`}
            aria-expanded={openPanel === 'env'}
            title={`Environment: ${activeEnv}`}
            className={`
              flex items-center justify-center w-11 h-8 mx-auto rounded-lg border
              transition-colors focus:outline-none focus:ring-2 focus:ring-ring
              ${openPanel === 'env'
                ? 'border-primary/40 bg-primary/5'
                : 'border-border bg-surface-2 hover:bg-surface'}
            `}
          >
            <span className={`w-2.5 h-2.5 rounded-full ${envDotClass(activeEnv)}`} />
          </button>
        </div>

        {openPanel === 'workspace' && (
          <WorkspacePanel pos={panelPos} onClose={close} onNewProject={() => setNewProjectOpen(true)} />
        )}
        {openPanel === 'env' && (
          <EnvPanel pos={panelPos} onClose={close} onOpenGraph={() => setGraphOpen(true)} />
        )}
        <GitGraphDialog open={graphOpen} onClose={() => setGraphOpen(false)} />
        <NewProjectDialog open={newProjectOpen} onClose={() => setNewProjectOpen(false)} />
      </div>
    )
  }

  // -------------------------------------------------------------------------
  // Expanded: one integrated card — org › project button + env pill row.
  // -------------------------------------------------------------------------
  return (
    <div className="relative px-2" ref={ref}>
      <div className={`
        rounded-2xl border overflow-hidden transition-colors
        ${openPanel ? 'border-primary/40' : 'border-border'}
      `}>
        {/* Org › Project — the primary control */}
        <button
          onClick={() => toggle('workspace')}
          aria-label="Switch workspace"
          aria-expanded={openPanel === 'workspace'}
          className={`
            flex items-center gap-2.5 w-full px-2.5 py-2 text-left
            transition-colors focus:outline-none focus:ring-2 focus:ring-inset focus:ring-ring
            ${openPanel === 'workspace' ? 'bg-primary/5' : 'bg-surface-2 hover:bg-surface'}
          `}
        >
          <span
            className="flex items-center justify-center w-8 h-8 rounded-lg text-white text-[13px] font-bold shrink-0 shadow-sm"
            style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
            aria-hidden="true"
          >
            {orgInitial}
          </span>
          <span className="flex flex-col min-w-0 flex-1 leading-tight" title={`${activeOrg.name} · ${projectLabel}`}>
            <span className="truncate text-[11px] font-medium text-muted">{activeOrg.name}</span>
            <span className="truncate text-[13.5px] font-semibold text-fg">{projectLabel}</span>
          </span>
          <ChevronsUpDown size={14} className="text-muted shrink-0" />
        </button>

        {/* Divider between the two axes */}
        <div className="border-t border-border" />

        {/* Environment — the secondary, runtime axis */}
        <button
          onClick={() => toggle('env')}
          aria-label="Switch environment"
          aria-haspopup="listbox"
          aria-expanded={openPanel === 'env'}
          className={`
            flex items-center gap-2.5 w-full px-2.5 py-1.5 text-left
            transition-colors focus:outline-none focus:ring-2 focus:ring-inset focus:ring-ring
            ${openPanel === 'env' ? 'bg-primary/5' : 'bg-surface hover:bg-surface-2'}
          `}
        >
          <span className="flex items-center justify-center w-8 shrink-0">
            <span className={`w-2.5 h-2.5 rounded-full ${envDotClass(activeEnv)}`} />
          </span>
          <span className="flex items-baseline gap-1.5 min-w-0 flex-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted shrink-0">Env</span>
            <span className="truncate font-mono text-xs text-fg">{activeEnv}</span>
          </span>
          <ChevronDown size={13} className={`text-muted shrink-0 transition-transform ${openPanel === 'env' ? 'rotate-180' : ''}`} />
        </button>
      </div>

      {openPanel === 'workspace' && (
        <WorkspacePanel pos={panelPos} onClose={close} onNewProject={() => setNewProjectOpen(true)} />
      )}
      {openPanel === 'env' && (
        <EnvPanel pos={panelPos} onClose={close} onOpenGraph={() => setGraphOpen(true)} />
      )}
      <GitGraphDialog open={graphOpen} onClose={() => setGraphOpen(false)} />
      <NewProjectDialog open={newProjectOpen} onClose={() => setNewProjectOpen(false)} />
    </div>
  )
}
