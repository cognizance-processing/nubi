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

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

export type Theme = 'light' | 'dark'

export interface ThemeContextValue {
  theme: Theme
  toggleTheme: () => void
  setTheme: (t: Theme) => void
}

// Exported so components that must render outside a ThemeProvider (editors used
// in tests / Storybook) can read it via useContext with a null-safe default,
// instead of calling useTheme() in a try/catch (which violates rules-of-hooks).
export const ThemeContext = createContext<ThemeContextValue | null>(null)

const STORAGE_KEY = 'nubi-theme'

function getInitialTheme(): Theme {
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

/** Frame colours from the brief, used only if the --bg token can't be read. */
const THEME_COLOR_FALLBACK: Record<Theme, string> = { light: '#eceef1', dark: '#0b0c0e' }

/**
 * Keep <meta name="theme-color"> (browser chrome / mobile status bar) in step with the frame.
 * index.html may ship media-query variants that follow the OS; this explicit, media-less tag is
 * inserted first so the user's own toggle wins. Colour is read from the --bg token so it can't drift.
 */
function syncThemeColor(theme: Theme) {
  try {
    const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
    const color = /^#[0-9a-f]{3,8}$/i.test(bg) ? bg : THEME_COLOR_FALLBACK[theme]
    let meta = document.head.querySelector<HTMLMetaElement>('meta[name="theme-color"][data-nubi-theme]')
    if (!meta) {
      meta = document.createElement('meta')
      meta.name = 'theme-color'
      meta.setAttribute('data-nubi-theme', '')
      document.head.prepend(meta)
    }
    meta.content = color
  } catch {
    // Non-browser environment — nothing to sync.
  }
}

function applyTheme(theme: Theme) {
  const root = document.documentElement
  if (theme === 'dark') {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
  }
  syncThemeColor(theme)
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme)

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
    const handler = (e: MediaQueryListEvent) => {
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

  function setTheme(t: Theme) {
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

/** Access theme state and actions from any component inside <ThemeProvider>. */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    throw new Error('useTheme must be used inside <ThemeProvider>')
  }
  return ctx
}
