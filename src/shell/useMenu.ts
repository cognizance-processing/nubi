/**
 * useMenu — the small amount of behaviour every shell popover menu needs, so each menu
 * (user, overflow, workspace) does not re-implement it:
 *
 *   - closes on outside pointer-down and on Escape (returning focus to the trigger)
 *   - moves focus to the first item when opened from the keyboard
 *   - ArrowUp / ArrowDown / Home / End roam the `[role="menuitem"]` children
 */

import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'

export function useMenu() {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const close = useCallback((returnFocus = true) => {
    setOpen(false)
    if (returnFocus) triggerRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); close() }
    }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open, close])

  // Focus the first item once the menu mounts.
  useEffect(() => {
    if (!open) return
    const id = requestAnimationFrame(() => menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus())
    return () => cancelAnimationFrame(id)
  }, [open])

  const onMenuKeyDown = useCallback((e: ReactKeyboardEvent) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'Tab'].includes(e.key)) return
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])
    if (!items.length) return
    if (e.key === 'Tab') { setOpen(false); return }
    const i = items.indexOf(document.activeElement as HTMLElement)
    let next = i
    if (e.key === 'ArrowDown') next = (i + 1) % items.length
    else if (e.key === 'ArrowUp') next = (i - 1 + items.length) % items.length
    else if (e.key === 'Home') next = 0
    else next = items.length - 1
    e.preventDefault()
    items[next].focus()
  }, [])

  return { open, setOpen, close, rootRef, triggerRef, menuRef, onMenuKeyDown }
}
