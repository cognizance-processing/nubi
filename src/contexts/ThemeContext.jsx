/**
 * ThemeContext — provides light/dark theme state throughout the app.
 *
 * State:
 *   theme — 'light' | 'dark'
 *
 * Actions:
 *   toggleTheme() — flips between light and dark, persists to localStorage
 *   setTheme(t)   — explicitly set 'light' or 'dark'
 *
 * Init order (matches the no-FOUC script in index.html):
 *   1. Check localStorage('nubi-theme') → use stored value if present
 *   2. Otherwise match window.matchMedia('prefers-color-scheme: dark')
 *   3. Apply 'dark' class to document.documentElement accordingly
 *
 * The .dark class on <html> is the single source of truth for CSS vars
 * in src/index.css — Tailwind's `darkMode: 'class'` reads it.
 */

import { createContext, useContext, useEffect, useState } from 'react'

const ThemeContext = createContext(null)

const STORAGE_KEY = 'nubi-theme'

function getInitialTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'dark' || stored === 'light') return stored
  } catch {
    // localStorage blocked — fall through
  }
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return 'light'
}

function applyTheme(theme) {
  const root = document.documentElement
  if (theme === 'dark') {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
  }
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(getInitialTheme)

  // Sync DOM on every theme change
  useEffect(() => {
    applyTheme(theme)
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // Ignore storage errors
    }
  }, [theme])

  // Listen for OS preference changes (only if no stored preference)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e) => {
      try {
        if (!localStorage.getItem(STORAGE_KEY)) {
          setThemeState(e.matches ? 'dark' : 'light')
        }
      } catch {
        setThemeState(e.matches ? 'dark' : 'light')
      }
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  function setTheme(t) {
    if (t === 'light' || t === 'dark') {
      setThemeState(t)
    }
  }

  function toggleTheme() {
    setThemeState(prev => (prev === 'dark' ? 'light' : 'dark'))
  }

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

/**
 * Access theme state and actions from any component inside <ThemeProvider>.
 * @returns {{ theme: 'light'|'dark', toggleTheme: Function, setTheme: Function }}
 */
export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    throw new Error('useTheme must be used inside <ThemeProvider>')
  }
  return ctx
}
