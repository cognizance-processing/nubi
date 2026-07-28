/**
 * spaTheme.js — Resolve ECharts theme tokens from the SPA's CSS design tokens.
 *
 * The embed/widgets/chart-options.js builder accepts a `theme` object with the
 * shape: { palette, fg, fgMuted, primary, border, grid, axis, tooltipBg, tooltipFg }
 *
 * This module reads the current document's CSS custom properties (set by the
 * ThemeContext via Tailwind's dark-mode class) and maps them to those tokens.
 * Falls back to dark-mode defaults if called outside a browser context (tests).
 *
 * Usage:
 *   import { resolveSpaTheme } from '../lib/spaTheme.js'
 *   const theme = resolveSpaTheme(isDark)
 */

// Default dark-mode theme tokens — match the design tokens in src/index.css
const DARK_THEME = {
  palette: [
    '#6366f1', '#f59e0b', '#10b981', '#ef4444', '#3b82f6',
    '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#84cc16',
  ],
  fg: '#e2e8f0',
  fgMuted: '#94a3b8',
  primary: '#6366f1',
  border: '#2d3748',
  grid: 'rgba(148,163,184,0.12)',
  axis: 'rgba(148,163,184,0.35)',
  tooltipBg: 'rgba(15,17,23,0.96)',
  tooltipFg: '#e2e8f0',
}

// Light-mode theme tokens
const LIGHT_THEME = {
  palette: [
    '#6366f1', '#f59e0b', '#10b981', '#ef4444', '#3b82f6',
    '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#84cc16',
  ],
  fg: '#1e293b',
  fgMuted: '#64748b',
  primary: '#6366f1',
  border: '#e2e8f0',
  grid: 'rgba(100,116,139,0.10)',
  axis: 'rgba(100,116,139,0.28)',
  tooltipBg: 'rgba(255,255,255,0.97)',
  tooltipFg: '#0f172a',
}

/**
 * Return a theme token object suitable for passing to buildChartOption({ theme }).
 *
 * @param {boolean} [isDark=true] — pass the current app theme mode
 * @param {string[]} [palette]    — optional custom palette override
 * @returns {{ palette, fg, fgMuted, primary, border, grid, axis, tooltipBg, tooltipFg }}
 */
export function resolveSpaTheme(isDark = true, palette = null) {
  const base = isDark ? DARK_THEME : LIGHT_THEME
  if (!palette) return base
  return { ...base, palette }
}
