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
import { Outlet } from 'react-router-dom'
import { useUi } from '../contexts/UiContext.jsx'
import { useProject } from '../contexts/ProjectContext.jsx'
import { AppSidebarDesktop, AppSidebarMobile } from '../components/app/AppSidebar.jsx'
import AppTopbar from '../components/app/AppTopbar.jsx'
import AppRightRail from '../components/app/AppRightRail.jsx'
import { ChatPanel } from '../chat/ChatPanel.jsx'
import { GitBranch, MessageSquare } from 'lucide-react'

// Lazy-load GitSyncPanel — a sibling agent creates this; it may not exist yet
// in the OSS build, so we degrade silently if the import fails.
const GitSyncPanel = lazy(() =>
  import('../components/app/GitSyncPanel.jsx').catch(() => ({ default: () => null }))
)

// ---------------------------------------------------------------------------
// GitSyncPanel wrapper — desktop slide-in aside (mirrors ChatPanelWrapper).
// ---------------------------------------------------------------------------

function GitPanelWrapper({ projectId, open, onClose }) {
  // GitSyncPanel is a self-contained slide-over (its own fixed backdrop + aside,
  // responsive for mobile and desktop), so we mount it once and let it own its
  // own presentation — no extra wrapping aside/overlay here.
  return (
    <Suspense fallback={null}>
      <GitSyncPanel projectId={projectId} open={open} onClose={onClose} />
    </Suspense>
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

  // Shell-level RHS panels are surfaced through the persistent right-edge rail.
  const { chatOpen, toggleChat, pageOwnsChat } = useUi()

  // Read the active project so we can pass its id to GitSyncPanel.
  // useProject() is safe here because AppShell is mounted inside ProjectProvider.
  const { activeProject } = useProject()
  const projectId = activeProject?.id ?? null

  // The persistent right-edge switcher items. Always present on every authed
  // page (desktop) — Git/Versions is the primary entry; Chat joins it unless a
  // page owns chat itself (e.g. the dashboard editor mounts its own chat UI).
  const railItems = [
    {
      id: 'git',
      Icon: GitBranch,
      label: 'Git / Versions',
      active: gitOpen,
      onToggle: () => setGitOpen(v => !v),
    },
    {
      id: 'chat',
      Icon: MessageSquare,
      label: 'AI Chat',
      active: chatOpen,
      onToggle: toggleChat,
      hidden: pageOwnsChat,
    },
  ]

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

        {/* Content area + chat panel + git panel + persistent rail side-by-side */}
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

          {/* Persistent right-edge switcher — always reachable on every authed
              page (desktop). The single, consistent entry point for the
              shell-level RHS panels (Git/Versions + Chat). */}
          <AppRightRail items={railItems} />
        </div>
      </div>
    </div>
  )
}
