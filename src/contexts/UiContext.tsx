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
 *   chatDraft / setChatDraft(text) — text a surface (Spotlight "Ask AI") hands to the chat composer
 *   crumbLabel / setCrumbLabel(label) — label for the last breadcrumb segment on a
 *                      dynamic route (set by PageShell's `crumb` prop / usePageCrumb)
 *
 * sidebarCollapsed is persisted to localStorage under 'nubi-sidebar-collapsed'.
 */

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

export interface UiContextValue {
  sidebarCollapsed: boolean
  toggleSidebar: () => void
  chatOpen: boolean
  openChat: () => void
  closeChat: () => void
  toggleChat: () => void
  topbarSlot: HTMLElement | null
  setTopbarSlot: (node: HTMLElement | null) => void
  pageOwnsChat: boolean
  setPageOwnsChat: (owns: boolean) => void
  crumbLabel: string | null
  setCrumbLabel: (label: string | null) => void
  chatDraft: string
  setChatDraft: (text: string) => void
}

const UiContext = createContext<UiContextValue | null>(null)

const SIDEBAR_KEY = 'nubi-sidebar-collapsed'

function getInitialSidebarCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === 'true'
  } catch {
    return false
  }
}

export function UiProvider({ children }: { children: ReactNode }) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(getInitialSidebarCollapsed)
  const [chatOpen, setChatOpen] = useState(false)
  // DOM node in AppTopbar that pages (e.g. the dashboard editor) portal their
  // own toolbar into — so there's a single top bar instead of a stacked second one.
  const [topbarSlot, setTopbarSlot] = useState<HTMLElement | null>(null)
  // When a page (e.g. the dashboard editor) owns the chat UI itself, the global
  // chat button and panel are suppressed so there are never two chats at once.
  const [pageOwnsChat, setPageOwnsChat] = useState(false)
  // Label for the last breadcrumb segment on dynamic routes (a board or query name).
  const [crumbLabel, setCrumbLabel] = useState<string | null>(null)
  // A draft message handed to the chat composer by another surface (e.g. Spotlight's "Ask AI").
  const [chatDraft, setChatDraft] = useState('')

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
        crumbLabel,
        setCrumbLabel,
        chatDraft,
        setChatDraft,
      }}
    >
      {children}
    </UiContext.Provider>
  )
}

export function useUi(): UiContextValue {
  const ctx = useContext(UiContext)
  if (!ctx) throw new Error('useUi must be used inside <UiProvider>')
  return ctx
}
