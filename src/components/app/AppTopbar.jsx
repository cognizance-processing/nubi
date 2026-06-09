/**
 * AppTopbar — top navigation bar inside the authenticated AppShell.
 *
 * Left:   [mobile hamburger] + Logo (collapsed-mode or mobile only) + sidebar toggle
 * Right:  Org selector | Chat toggle | Theme toggle | User avatar menu
 *
 * Contracts:
 *   - Reads UiContext for sidebarCollapsed, toggleSidebar, toggleChat
 *   - Reads OrgContext for orgs, activeOrg, setActiveOrg
 *   - Reads AuthContext for user, logout
 *   - Reads ThemeContext for theme, toggleTheme
 */

import { useState, useEffect, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Menu,
  Sun,
  Moon,
  MessageSquare,
  LogOut,
  Settings,
} from 'lucide-react'
import { useUi } from '../../contexts/UiContext.jsx'
import { useAuth } from '../../contexts/AuthContext.jsx'
import { useTheme } from '../../contexts/ThemeContext.jsx'
import Logo from '../Logo.jsx'

// ---------------------------------------------------------------------------
// User avatar dropdown
// ---------------------------------------------------------------------------

function UserMenu() {
  const { user, logout } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function onDown(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  if (!user) return null

  const initials = (user.name || user.email || '?')
    .split(' ')
    .map(s => s[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  async function handleLogout() {
    setOpen(false)
    await logout()
    navigate('/login')
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        aria-label="User account menu"
        aria-expanded={open}
        className="
          flex items-center justify-center overflow-hidden
          w-9 h-9 rounded-full
          text-xs font-bold text-white
          transition-opacity duration-150 hover:opacity-85
          focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-surface
          select-none shrink-0
        "
        style={user.avatar_url ? undefined : { background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
      >
        {user.avatar_url
          ? <img src={user.avatar_url} alt="" className="w-full h-full object-cover" referrerPolicy="no-referrer" />
          : initials}
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-30"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          <div className="
            absolute right-0 top-full mt-1.5 z-40
            w-52 py-1.5 rounded-xl
            bg-surface border border-border shadow-lg shadow-black/10
          ">
            {/* User info */}
            <div className="px-3 py-2 border-b border-border mb-1">
              {user.name && (
                <p className="text-sm font-semibold text-fg truncate">{user.name}</p>
              )}
              <p className="text-xs text-muted truncate">{user.email}</p>
            </div>

            {/* Theme toggle */}
            <button
              onClick={toggleTheme}
              className="
                flex items-center gap-2.5 w-full px-3 py-2.5 text-sm text-fg
                hover:bg-surface-2 transition-colors text-left min-h-[40px]
              "
            >
              {theme === 'dark'
                ? <Sun size={14} className="text-muted shrink-0" />
                : <Moon size={14} className="text-muted shrink-0" />}
              {theme === 'dark' ? 'Light mode' : 'Dark mode'}
            </button>

            {/* Settings link */}
            <Link
              to="/settings"
              onClick={() => setOpen(false)}
              className="
                flex items-center gap-2.5 px-3 py-2.5 text-sm text-fg
                hover:bg-surface-2 transition-colors min-h-[40px]
              "
            >
              <Settings size={14} className="text-muted shrink-0" />
              Settings
            </Link>

            {/* Divider */}
            <div className="border-t border-border my-1" />

            {/* Sign out */}
            <button
              onClick={handleLogout}
              className="
                flex items-center gap-2.5 w-full px-3 py-2.5 text-sm
                text-red-500 dark:text-red-400
                hover:bg-red-50 dark:hover:bg-red-950/30
                transition-colors text-left min-h-[40px]
              "
            >
              <LogOut size={14} className="shrink-0" />
              Sign out
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AppTopbar
// ---------------------------------------------------------------------------

/**
 * @param {{ onMobileMenuOpen: Function }} props
 */
export default function AppTopbar({ onMobileMenuOpen }) {
  const { toggleChat, chatOpen, setTopbarSlot, pageOwnsChat } = useUi()

  return (
    <header className="
      sticky top-0 z-30
      flex items-center gap-3
      px-3 h-14 shrink-0
      bg-surface/90 backdrop-blur-sm
      border-b border-border
    ">
      {/* ── Far left: mobile hamburger / logo ── */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Mobile hamburger */}
        <button
          onClick={onMobileMenuOpen}
          aria-label="Open navigation menu"
          className="
            md:hidden flex items-center justify-center w-9 h-9 rounded-lg
            text-muted hover:text-fg hover:bg-surface-2
            transition-colors duration-150
            focus:outline-none focus:ring-2 focus:ring-ring
          "
        >
          <Menu size={18} />
        </button>

        {/* Logo — mobile only (sidebar hidden); links to the landing page */}
        <Link to="/" aria-label="Nubi — back to landing page" className="md:hidden shrink-0">
          <Logo size={24} showName={false} />
        </Link>
      </div>

      {/* ── Center: per-page toolbar slot (e.g. the dashboard editor) ── */}
      <div
        ref={setTopbarSlot}
        className="flex items-center gap-2 flex-1 min-w-0 overflow-x-auto"
      />

      {/* ── Right: AI chat toggle + user avatar (far right) ── */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Hide global chat button when the current page owns chat (e.g. editor) */}
        {!pageOwnsChat && (
          <button
            onClick={toggleChat}
            aria-label={chatOpen ? 'Close AI chat' : 'Open AI chat'}
            aria-pressed={chatOpen}
            data-testid="global-chat-btn"
            className={`
              flex items-center justify-center w-9 h-9 rounded-lg
              border border-border
              transition-colors duration-150
              focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1
              ${chatOpen
                ? 'bg-primary text-primary-fg border-primary'
                : 'text-muted hover:text-fg hover:bg-surface-2'
              }
            `}
          >
            <MessageSquare size={15} strokeWidth={2} />
          </button>
        )}

        <UserMenu />
      </div>
    </header>
  )
}
