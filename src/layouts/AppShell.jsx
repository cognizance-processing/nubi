/**
 * AppShell — the authenticated app layout.
 *
 * Structure (desktop):
 *
 *   ┌──────────────────────────────────────────────────────┐
 *   │  [sidebar]  │  [topbar]                              │
 *   │             ├────────────────────────────────────────┤
 *   │             │  <Outlet/> (main content)   │ [chat]  │
 *   └──────────────────────────────────────────────────────┘
 *
 * Mobile:
 *   - Sidebar becomes an off-canvas drawer (hamburger in topbar)
 *   - Chat panel becomes a full-screen overlay
 *
 * Wrapped by UiProvider + OrgProvider (injected in App.jsx routing tree).
 */

import { useState, useEffect, lazy, Suspense } from 'react'
import { createPortal } from 'react-dom'
import { Outlet } from 'react-router-dom'
import { useUi } from '../contexts/UiContext.jsx'
import { useProject } from '../contexts/ProjectContext.jsx'
import { AppSidebarDesktop, AppSidebarMobile } from '../components/app/AppSidebar.jsx'
import AppTopbar from '../components/app/AppTopbar.jsx'
import { ChatPanel } from '../chat/ChatPanel.jsx'
import { GitBranch } from 'lucide-react'

// Lazy-load GitSyncPanel — a sibling agent creates this; it may not exist yet
// in the OSS build, so we degrade silently if the import fails.
const GitSyncPanel = lazy(() =>
  import('../components/app/GitSyncPanel.jsx').catch(() => ({ default: () => null }))
)

// ---------------------------------------------------------------------------
// Git / Versions control — PERSISTENT shell chrome.
//
// The git surface must be reachable identically on every authenticated page
// (dashboards, queries, flows, the editor), so this control is owned by the
// AppShell itself and never depends on a page setting `topbarSlot`.
//
// Placement strategy (without editing AppTopbar.jsx):
//   - When a page HAS mounted the center topbar slot, we portal the control
//     INTO that slot so it sits inline in the header toolbar (no stacked bar).
//   - When no slot is mounted (e.g. dashboards), we render a fixed fallback
//     pinned to the top-right of the viewport, layered above the sticky topbar,
//     so the button is NEVER missing.
// Exactly one instance renders at a time, so pages that set the slot never get
// a duplicate button.
// ---------------------------------------------------------------------------

function GitControlButton({ open, onToggle, fixed = false }) {
  return (
    <button
      onClick={onToggle}
      aria-label={open ? 'Close Git / Versions panel' : 'Open Git / Versions panel'}
      aria-pressed={open}
      title="Git / Versions — push, pull, branch graph and version history"
      data-testid="global-git-btn"
      className={`
        flex items-center justify-center gap-1.5 h-9 px-2.5 rounded-lg
        border border-border text-xs font-medium
        transition-colors duration-150
        focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1
        ${fixed ? 'fixed top-2.5 right-[7.5rem] z-40 bg-surface/95 backdrop-blur-sm shadow-sm' : ''}
        ${open
          ? 'bg-primary text-primary-fg border-primary'
          : 'text-muted hover:text-fg hover:bg-surface-2'
        }
      `}
    >
      <GitBranch size={15} strokeWidth={2} />
      <span className="hidden sm:inline">Git</span>
    </button>
  )
}

function GitPersistentControl({ open, onToggle }) {
  const { topbarSlot } = useUi()

  // Inline in the page's topbar toolbar when one is mounted…
  if (topbarSlot) {
    return createPortal(
      <GitControlButton open={open} onToggle={onToggle} />,
      topbarSlot,
    )
  }

  // …otherwise a fixed fallback so the control is always visible.
  return <GitControlButton open={open} onToggle={onToggle} fixed />
}

// ---------------------------------------------------------------------------
// GitSyncPanel wrapper — desktop slide-in aside (mirrors ChatPanelWrapper).
// ---------------------------------------------------------------------------

function GitPanelWrapper({ projectId, open, onClose }) {
  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div className="md:hidden fixed inset-0 z-50 bg-surface flex flex-col">
          <Suspense fallback={null}>
            <GitSyncPanel projectId={projectId} open={open} onClose={onClose} />
          </Suspense>
        </div>
      )}

      {/* Desktop slide-in panel */}
      <aside
        className={`
          hidden md:flex flex-col shrink-0
          border-l border-border bg-surface
          transition-all duration-250 ease-in-out overflow-hidden
          ${open ? 'w-[340px]' : 'w-0'}
        `}
        aria-label="Git sync panel"
        aria-hidden={!open}
        inert={!open ? '' : undefined}
      >
        <div className="w-[340px] h-full flex flex-col">
          <Suspense fallback={null}>
            <GitSyncPanel projectId={projectId} open={open} onClose={onClose} />
          </Suspense>
        </div>
      </aside>
    </>
  )
}

// ---------------------------------------------------------------------------
// Chat panel wrapper — desktop slide-in OR mobile full-screen overlay
// Suppressed entirely when a page (e.g. the dashboard editor) owns chat.
// ---------------------------------------------------------------------------

function ChatPanelWrapper() {
  const { chatOpen, closeChat, pageOwnsChat } = useUi()

  // Force-close the global chat if a page takes ownership.
  useEffect(() => {
    if (pageOwnsChat && chatOpen) {
      closeChat()
    }
  }, [pageOwnsChat, chatOpen, closeChat])

  // When the editor (or any page) owns chat, don't render the global panel at all.
  if (pageOwnsChat) return null

  return (
    <>
      {/* Mobile overlay */}
      {chatOpen && (
        <div className="
          md:hidden fixed inset-0 z-50
          bg-surface flex flex-col
        ">
          <ChatPanel onClose={closeChat} />
        </div>
      )}

      {/* Desktop slide-in panel */}
      <aside
        className={`
          hidden md:flex flex-col shrink-0
          border-l border-border bg-surface
          transition-all duration-250 ease-in-out overflow-hidden
          ${chatOpen ? 'w-[340px]' : 'w-0'}
        `}
        aria-label="AI chat panel"
        aria-hidden={!chatOpen}
        inert={!chatOpen ? '' : undefined}
      >
        <div className="w-[340px] h-full flex flex-col">
          <ChatPanel onClose={closeChat} />
        </div>
      </aside>
    </>
  )
}

// ---------------------------------------------------------------------------
// AppShell
// ---------------------------------------------------------------------------

export default function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [gitOpen, setGitOpen] = useState(false)

  // Read the active project so we can pass its id to GitSyncPanel.
  // useProject() is safe here because AppShell is mounted inside ProjectProvider.
  const { activeProject } = useProject()
  const projectId = activeProject?.id ?? null

  return (
    <div className="flex h-screen overflow-hidden bg-bg text-fg">
      {/* ── Desktop sidebar ─────────────────────────────────── */}
      <AppSidebarDesktop />

      {/* ── Mobile off-canvas drawer ────────────────────────── */}
      <AppSidebarMobile
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
      />

      {/* ── Main column: topbar + content ──────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <AppTopbar onMobileMenuOpen={() => setMobileNavOpen(true)} />

        {/* Persistent Git / Versions control — always reachable on every authed
            page (portalled into the topbar slot when present, fixed otherwise). */}
        <GitPersistentControl open={gitOpen} onToggle={() => setGitOpen(v => !v)} />

        {/* Content area + chat panel + git panel side-by-side */}
        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* Page content */}
          <main
            className="flex-1 overflow-y-auto bg-bg"
            id="main-content"
          >
            <Outlet />
          </main>

          {/* Chat panel */}
          <ChatPanelWrapper />

          {/* Git sync panel — project-scoped, available on all authed pages */}
          <GitPanelWrapper
            projectId={projectId}
            open={gitOpen}
            onClose={() => setGitOpen(false)}
          />
        </div>
      </div>
    </div>
  )
}
