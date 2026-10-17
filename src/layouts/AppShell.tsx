/**
 * AppShell — the authenticated app layout.
 *
 * Structure (desktop):
 *
 *   ┌─ frame ───────────────────────────────────────────────────────┐
 *   │  [sidebar]  ┌─ sheet ──────────────────────────────────────┐ │
 *   │  on the     │  [crumb · page toolbar …  🔔 ⑂ 💬 │ avatar]  │ │
 *   │  frame      ├──────────────────────────────────────────────┤ │
 *   │             │  <Outlet/> (main content)         │ [panel]  │ │
 *   │             └──────────────────────────────────────────────┘ │
 *   └───────────────────────────────────────────────────────────────┘
 *
 * Swiss "inset sheet": the sidebar sits directly on the grey frame; the topbar
 * and page share one white sheet. ⌘K opens Spotlight from anywhere; `?` opens the
 * shortcut sheet; `g` + a letter jumps between pages (see shell/useShellShortcuts).
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
 * Motion: content fades in on route change (120ms opacity + 4px, --ease-out; not for
 * keyboard-started navigation); the RHS panel and the mobile drawer move on --dur-3.
 * All of it is off under prefers-reduced-motion. The page area has its own Suspense
 * boundary so the frame stays put while a route's code loads.
 *
 * Wrapped by UiProvider + OrgProvider (injected in App.jsx routing tree).
 */

import { useState, useEffect, useCallback, useLayoutEffect, useRef, lazy, Suspense, type ComponentType } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { useUi } from '../contexts/UiContext.jsx'
import { useProject } from '../contexts/ProjectContext.jsx'
import { AppSidebarDesktop, AppSidebarMobile } from '../components/app/AppSidebar.jsx'
import AppTopbar from '../components/app/AppTopbar.jsx'
import { ChatPanel } from '../chat/ChatPanel.jsx'
import NotificationCenter from '../components/app/NotificationCenter.jsx'
import Spotlight from '../components/app/Spotlight.jsx'
import ShortcutSheet from '../shell/ShortcutSheet.jsx'
import { RouteErrorBoundary } from '../shell/ErrorBoundary.jsx'
import { useShellShortcuts } from '../shell/useShellShortcuts.js'
import { consumeKeyboardNav } from '../shell/shellLogic.js'
import { GitBranch, MessageSquare, Bell } from 'lucide-react'
import '../shell/shell.css'

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
// Page-area loading state — shown only if a route's code takes > 300ms, so quick
// navigations never flash it. The frame (sidebar, topbar) stays put around it.
// ---------------------------------------------------------------------------

function ContentFallback() {
  const [show, setShow] = useState(false)
  useEffect(() => {
    const t = window.setTimeout(() => setShow(true), 300)
    return () => window.clearTimeout(t)
  }, [])
  if (!show) return null
  return (
    <div className="sh-page" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Loading page</span>
      <div className="sh-page-inner" aria-hidden="true">
        <div className="nubi-shimmer h-6 w-48 mb-3" />
        <div className="nubi-shimmer h-4 w-80 max-w-full mb-4" />
        <div className="border-t-2 border-[var(--ink)] pt-6 space-y-3">
          {[0, 1, 2].map(i => <div key={i} className="nubi-shimmer h-10 w-full" />)}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AppShell
// ---------------------------------------------------------------------------

export default function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [gitOpen, setGitOpen] = useState(false)
  const [notifOpen, setNotifOpen] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)

  const { chatOpen, openChat, closeChat, pageOwnsChat } = useUi()
  useShellShortcuts()

  // Route transition: restart a 120ms fade on <main> whenever the path changes. Done by toggling
  // a class (not by re-keying), so a page that stays mounted across params keeps its state.
  const { pathname } = useLocation()
  const mainRef = useRef<HTMLElement>(null)
  const firstRoute = useRef(true)
  useLayoutEffect(() => {
    const el = mainRef.current
    if (!el) return
    el.scrollTop = 0
    if (firstRoute.current) { firstRoute.current = false; return }
    if (consumeKeyboardNav()) return
    el.classList.remove('sh-route-in')
    void el.offsetWidth // restart the animation
    el.classList.add('sh-route-in')
  }, [pathname])

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
      <a href="#main-content" className="nubi-skip-link" onClick={e => {
        // The hash would collide with the router; move focus ourselves.
        e.preventDefault()
        mainRef.current?.focus()
      }}>Skip to content</a>

      {/* ── Desktop sidebar ─────────────────────────────────── */}
      <AppSidebarDesktop />

      {/* ── Mobile off-canvas drawer ────────────────────────── */}
      <AppSidebarMobile
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
      />

      {/* ── Main column: topbar + content, set as one inset sheet on the frame.
           Desktop: the sheet floats 8px off the frame with a hairline edge;
           phones: it fills the screen. ─────────────────────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden bg-surface sh-sheet md:my-2 md:mr-2 md:border md:border-[var(--frame-line)]">
        <AppTopbar
          onMobileMenuOpen={() => setMobileNavOpen(true)}
          actions={railItems}
        />

        {/* Content + the shared RHS sidebar, side by side */}
        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* Page content. `flex flex-col` matters here: it's what makes a
              page's `flex-1 min-h-0` root (the dashboard/query editors'
              "fixed shell, only inner regions scroll" layout) actually take
              effect as a flex item — without it those classes are inert (no
              flex container to be an item OF), the editor renders at its
              natural content height instead of being clipped, and THIS
              overflow-y-auto ends up scrolling the whole editor as one block
              instead of its own internal panels/canvas doing so. Normal pages
              that don't opt into a fixed-height layout are unaffected — a
              flex column with one child still just scrolls its content. */}
          <main
            ref={mainRef}
            tabIndex={-1}
            className="flex-1 min-h-0 flex flex-col overflow-y-auto bg-surface outline-none"
            id="main-content"
          >
            <RouteErrorBoundary>
              <Suspense fallback={<ContentFallback />}>
                <Outlet />
              </Suspense>
            </RouteErrorBoundary>
          </main>

          {/* ── Shared RHS sidebar — Notifications / Git / Chat ──────────────
              One slot, one panel at a time, one collapse system. Desktop: an
              in-flow aside that pushes content and collapses to zero width.
              Mobile: a full-screen overlay. NotificationCenter stays mounted
              (it polls the unread badge); it only shows its panel when active. */}
          <aside
            aria-label={activePanel === 'chat' ? 'AI chat panel' : activePanel === 'git' ? 'Git panel' : activePanel === 'notifications' ? 'Notifications panel' : 'Side panel'}
            aria-hidden={!activePanel}
            inert={!activePanel ? true : undefined}
            className={[
              'sh-rhs flex flex-col bg-surface shrink-0 md:overflow-hidden',
              // The rule only while open: a collapsed 0-width panel would otherwise
              // leave a 1px hairline down the right edge of every page.
              activePanel
                ? 'fixed inset-0 z-50 md:static md:z-auto md:w-[380px] md:border-l md:border-border'
                : 'hidden md:flex md:w-0',
            ].join(' ')}
          >
            <div className="w-full md:w-[380px] h-full flex flex-col">
              {activePanel === 'chat' && (
                <div key="chat" className="sh-rhs-body h-full flex flex-col min-h-0"><ChatPanel onClose={collapseAll} /></div>
              )}

              {activePanel === 'git' && (
                <div key="git" className="sh-rhs-body h-full flex flex-col min-h-0">
                  <Suspense fallback={null}>
                    <GitSyncPanel embedded open projectId={projectId} onClose={collapseAll} />
                  </Suspense>
                </div>
              )}

              {/* Always mounted: keeps polling the unread-count badge even while
                  collapsed. Renders its panel only when it is the active panel. */}
              <div className={activePanel === 'notifications' ? 'sh-rhs-body h-full flex flex-col min-h-0' : 'contents'}>
                <NotificationCenter
                  embedded
                  open={activePanel === 'notifications'}
                  onClose={collapseAll}
                  onCount={setUnreadCount}
                />
              </div>
            </div>
          </aside>
        </div>
      </div>

      {/* ⌘K — one search for pages, settings, actions, dashboards and queries */}
      <Spotlight />
      <ShortcutSheet />
    </div>
  )
}
