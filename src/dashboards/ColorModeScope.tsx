/**
 * ColorModeScope — render a subtree in a fixed light/dark mode.
 *
 * Two things follow the app theme and both need overriding for a board or a
 * card that decides its own mode (see lib/boardTheme.ts): the CSS theme tokens
 * (`.dark` / `.nubi-light` on the element) and the theme React components read
 * through useTheme() — charts and KPI tiles pick their axis, label and series
 * colors from it. `mode == null` renders the subtree as-is, following the app.
 */

import { forwardRef, useContext, useMemo, type CSSProperties, type HTMLAttributes, type ReactNode } from 'react'
import { ThemeContext } from '../contexts/ThemeContext.jsx'
import type { ColorMode } from '../lib/boardTheme.js'
import './widgets/chrome.css'

export function colorModeClass(mode: ColorMode | null | undefined): string | undefined {
  if (mode === 'dark') return 'dark'
  if (mode === 'light') return 'nubi-light'
  return undefined
}

export function ThemeModeProvider({ mode, children }: { mode: ColorMode | null | undefined; children: ReactNode }) {
  const parent = useContext(ThemeContext)
  const value = useMemo(
    () => (mode && parent ? { ...parent, theme: mode } : parent),
    [mode, parent],
  )
  if (!mode || !parent || parent.theme === mode) return <>{children}</>
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

interface ScopeProps extends HTMLAttributes<HTMLDivElement> {
  mode: ColorMode | null | undefined
  style?: CSSProperties
  children: ReactNode
  'data-dashboard-root'?: boolean
}

const ColorModeScope = forwardRef<HTMLDivElement, ScopeProps>(function ColorModeScope(
  { mode, className, style, children, ...rest },
  ref,
) {
  const cls = [className, colorModeClass(mode)].filter(Boolean).join(' ') || undefined
  return (
    <div ref={ref} className={cls} style={style} {...rest}>
      <ThemeModeProvider mode={mode}>{children}</ThemeModeProvider>
    </div>
  )
})

export default ColorModeScope
