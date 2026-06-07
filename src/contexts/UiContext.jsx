/**
 * UiContext — global shell UI state.
 *
 * Provides:
 *   sidebarCollapsed  {boolean}  — sidebar is icon-only mode
 *   toggleSidebar()              — toggle sidebarCollapsed; persists to localStorage
 *   chatOpen          {boolean}  — right-hand chat panel is visible
 *   openChat()
 *   closeChat()
 *   toggleChat()
 *
 * sidebarCollapsed is persisted to localStorage under 'nubi-sidebar-collapsed'.
 */

import { createContext, useContext, useState, useCallback } from 'react'

const UiContext = createContext(null)

const SIDEBAR_KEY = 'nubi-sidebar-collapsed'

function getInitialSidebarCollapsed() {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === 'true'
  } catch {
    return false
  }
}

export function UiProvider({ children }) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(getInitialSidebarCollapsed)
  const [chatOpen, setChatOpen] = useState(false)
  // DOM node in AppTopbar that pages (e.g. the dashboard editor) portal their
  // own toolbar into — so there's a single top bar instead of a stacked second one.
  const [topbarSlot, setTopbarSlot] = useState(null)
  // When a page (e.g. the dashboard editor) owns the chat UI itself, the global
  // chat button and panel are suppressed so there are never two chats at once.
  const [pageOwnsChat, setPageOwnsChat] = useState(false)

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed(prev => {
      const next = !prev
      try {
        localStorage.setItem(SIDEBAR_KEY, String(next))
      } catch {
        // Ignore storage errors
      }
      return next
    })
  }, [])

  const openChat = useCallback(() => setChatOpen(true), [])
  const closeChat = useCallback(() => setChatOpen(false), [])
  const toggleChat = useCallback(() => setChatOpen(v => !v), [])

  return (
    <UiContext.Provider
      value={{
        sidebarCollapsed,
        toggleSidebar,
        chatOpen,
        openChat,
        closeChat,
        toggleChat,
        topbarSlot,
        setTopbarSlot,
        pageOwnsChat,
        setPageOwnsChat,
      }}
    >
      {children}
    </UiContext.Provider>
  )
}

/**
 * @returns {{ sidebarCollapsed: boolean, toggleSidebar: Function, chatOpen: boolean, openChat: Function, closeChat: Function, toggleChat: Function }}
 */
export function useUi() {
  const ctx = useContext(UiContext)
  if (!ctx) throw new Error('useUi must be used inside <UiProvider>')
  return ctx
}
