/**
 * AppShell — the authenticated app layout.
 *
 * Structure (desktop):
 *
 *   ┌────────────────────────────────────────────────────────────┐
 *   │  [sidebar]  │  [topbar … page toolbar …  🔔 ⑂ 💬 │ avatar] │
 *   │             ├──────────────────────────────────────────────┤
 *   │             │  <Outlet/> (main content)         │ [panel]  │
 *   └────────────────────────────────────────────────────────────┘
 *
 * The global panel switcher (Notifications / Git / Chat) lives in the topbar's
 * right zone (AppTopbar → TopbarActions). All three share ONE collapsible
 * right-hand sidebar — a single slot, one panel open at a time, one consistent
 * collapse system (the same in-flow aside Chat has always used, and the same
 * pattern the dashboard editor / flows page use for their own RHS panels).
 * Toggling the active panel collapses the sidebar; opening another switches it.
 *
 * Desktop: the sidebar is an in-flow aside that pushes content and collapses to
 * zero width. Mobile: it becomes a full-screen overlay.
 *
 * NotificationCenter stays mounted whenever the shell is (even while collapsed)
 * so it keeps polling the unread-count badge; it only renders its panel when it
 * is the active panel.
 *
 * Wrapped by UiProvider + OrgProvider (injected in App.jsx routing tree).
 */

import { useState, useEffect, useCallback, lazy, Suspense, type ComponentType } from 'react'
import { Outlet } from 'react-router-dom'
import { useUi } from '../contexts/UiContext.jsx'
import { useProject } from '../contexts/ProjectContext.jsx'
import { AppSidebarDesktop, AppSidebarMobile } from '../components/app/AppSidebar.jsx'
import AppTopbar from '../components/app/AppTopbar.jsx'
import { ChatPanel } from '../chat/ChatPanel.jsx'
import NotificationCenter from '../components/app/NotificationCenter.jsx'
import { GitBranch, MessageSquare, Bell } from 'lucide-react'

// Lazy-load GitSyncPanel — a sibling agent creates this; it may not exist yet
// in the OSS build, so we degrade silently if the import fails. Untyped .jsx
// until components/app/ is converted, so widen to accept any props for now.
const GitSyncPanel = lazy<ComponentType<any>>(() =>
  import('../components/app/GitSyncPanel.jsx')
    .then(mod => ({ default: mod.default as ComponentType<any> }))
    .catch(() => ({ default: (() => null) as ComponentType<any> }))
)

// Width of the shared RHS sidebar (desktop) is a literal `md:w-[380px]` below —
// Tailwind's JIT only generates classes it can see as complete strings, so the
// width must not be interpolated. One value for every panel so switching
// between Notifications / Git / Chat never resizes the sidebar.

// ---------------------------------------------------------------------------
// AppShell
// ---------------------------------------------------------------------------

export default function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [gitOpen, setGitOpen] = useState(false)
  const [notifOpen, setNotifOpen] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)

  const { chatOpen, openChat, closeChat, pageOwnsChat } = useUi()

  // Read the active project so we can pass its id to GitSyncPanel.
  const { activeProject } = useProject()
  const projectId = activeProject?.id ?? null

  // The single active RHS panel (mutually exclusive). Chat is suppressed when a
  // page owns its own chat UI (e.g. the dashboard editor).
  const activePanel =
    gitOpen ? 'git'
    : notifOpen ? 'notifications'
    : (chatOpen && !pageOwnsChat) ? 'chat'
    : null

  // If a page takes over chat while the global chat is open, close it.
  useEffect(() => {
    if (pageOwnsChat && chatOpen) closeChat()
  }, [pageOwnsChat, chatOpen, closeChat])

  const collapseAll = useCallback(() => {
    setGitOpen(false)
    setNotifOpen(false)
    closeChat()
  }, [closeChat])

  // Open one panel exclusively, or collapse the sidebar if it's already active.
  const togglePanel = useCallback((which) => {
    const isActive =
      (which === 'git' && gitOpen) ||
      (which === 'notifications' && notifOpen) ||
      (which === 'chat' && chatOpen)
    setGitOpen(which === 'git' && !isActive)
    setNotifOpen(which === 'notifications' && !isActive)
    if (which === 'chat' && !isActive) openChat()
    else closeChat()
  }, [gitOpen, notifOpen, chatOpen, openChat, closeChat])

  // Topbar switcher items — the single home for the shell RHS panels, on every
  // authed page at every breakpoint. Chat hides when a page owns chat itself.
  const railItems = [
    {
      id: 'notifications',
      Icon: Bell,
      label: 'Notifications',
      active: activePanel === 'notifications',
      onToggle: () => togglePanel('notifications'),
      badge: unreadCount,
    },
    {
      id: 'git',
      Icon: GitBranch,
      label: 'Git / Versions',
      active: activePanel === 'git',
      onToggle: () => togglePanel('git'),
    },
    {
      id: 'chat',
      Icon: MessageSquare,
      label: 'AI Chat',
      active: activePanel === 'chat',
      onToggle: () => togglePanel('chat'),
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
        <AppTopbar
          onMobileMenuOpen={() => setMobileNavOpen(true)}
          actions={railItems}
        />

        {/* Content + the shared RHS sidebar, side by side */}
        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* Page content */}
          <main
            className="flex-1 overflow-y-auto bg-bg"
            id="main-content"
          >
            <Outlet />
          </main>

          {/* ── Shared RHS sidebar — Notifications / Git / Chat ──────────────
              One slot, one panel at a time, one collapse system. Desktop: an
              in-flow aside that pushes content and collapses to zero width.
              Mobile: a full-screen overlay. NotificationCenter stays mounted
              (it polls the unread badge); it only shows its panel when active. */}
          <aside
            aria-label="Side panel"
            aria-hidden={!activePanel}
            inert={!activePanel ? true : undefined}
            className={[
              'flex flex-col bg-surface shrink-0',
              'md:border-l md:border-border md:transition-[width] md:duration-[250ms] md:ease-in-out md:overflow-hidden',
              activePanel
                ? 'fixed inset-0 z-50 md:static md:z-auto md:w-[380px]'
                : 'hidden md:flex md:w-0',
            ].join(' ')}
          >
            <div className="w-full md:w-[380px] h-full flex flex-col">
              {activePanel === 'chat' && <ChatPanel onClose={collapseAll} />}

              {activePanel === 'git' && (
                <Suspense fallback={null}>
                  <GitSyncPanel embedded open projectId={projectId} onClose={collapseAll} />
                </Suspense>
              )}

              {/* Always mounted: keeps polling the unread-count badge even while
                  collapsed. Renders its panel only when it is the active panel. */}
              <NotificationCenter
                embedded
                open={activePanel === 'notifications'}
                onClose={collapseAll}
                onCount={setUnreadCount}
              />
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}
