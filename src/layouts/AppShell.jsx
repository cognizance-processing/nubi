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

import { useState, useEffect } from 'react'
import { Outlet } from 'react-router-dom'
import { useUi } from '../contexts/UiContext.jsx'
import { AppSidebarDesktop, AppSidebarMobile } from '../components/app/AppSidebar.jsx'
import AppTopbar from '../components/app/AppTopbar.jsx'
import { ChatPanel } from '../chat/ChatPanel.jsx'
import { X } from 'lucide-react'

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

        {/* Content area + chat panel side-by-side */}
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
        </div>
      </div>
    </div>
  )
}
