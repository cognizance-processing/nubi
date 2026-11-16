/**
 * useShellShortcuts — app-wide single-key shortcuts, mounted once by AppShell.
 *
 *   g then h/c/e/q/d/f/w/s   go to a page (1.2s window for the second key)
 *   [                        collapse / expand the sidebar
 *   ?                        open the shortcut sheet
 *
 * Every one is ignored while the user is typing (inputs, textareas, selects, contenteditable,
 * Monaco) and when a modifier key is held. Navigation started this way is marked as keyboard-
 * initiated so the route transition stays still (motion rule: keyboard actions are not animated).
 */

import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useUi } from '../contexts/UiContext.jsx'
import { NAV_CHORDS, isTypingTarget, markKeyboardNav } from './shellLogic.js'
import { openShortcuts } from './ShortcutSheet.jsx'

const CHORD_WINDOW_MS = 1200

export function useShellShortcuts() {
  const navigate = useNavigate()
  const { toggleSidebar } = useUi()

  useEffect(() => {
    let armedAt = 0

    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return
      if (isTypingTarget(e.target)) return
      // A dialog (Spotlight, the sheet, a modal) owns the keyboard while open.
      if (document.querySelector('[role="dialog"][aria-modal="true"]:not([inert])')) return

      const now = Date.now()
      const key = e.key

      if (armedAt && now - armedAt <= CHORD_WINDOW_MS) {
        armedAt = 0
        const target = NAV_CHORDS[key.toLowerCase()]
        if (target) {
          e.preventDefault()
          markKeyboardNav()
          navigate(target.to)
          return
        }
      }

      if (key === 'g') { armedAt = now; return }
      armedAt = 0

      if (key === '[') { e.preventDefault(); toggleSidebar() }
      else if (key === '?') { e.preventDefault(); openShortcuts() }
    }

    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate, toggleSidebar])
}
