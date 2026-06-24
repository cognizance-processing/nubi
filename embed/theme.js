/**
 * theme.js — 25-token Nubi design system for embedded components.
 *
 * Exports
 * -------
 *  NUBI_THEME_TOKENS   — dark preset token map (default)
 *  NUBI_THEME_PRESETS  — { dark, light } preset maps
 *  NUBI_THEME_CSS      — CSS string of :host rules for shadow DOM injection
 *  applyTheme(el, preset, overrides) — set CSS custom properties on an element
 */

/** @type {Record<string, string>} */
export const NUBI_THEME_TOKENS = {
  /* Backgrounds */
  '--nubi-bg':              '#0f1117',
  '--nubi-bg-2':            '#1a1f2e',
  '--nubi-bg-3':            '#1e2433',
  /* Foreground */
  '--nubi-fg':              '#e2e8f0',
  '--nubi-fg-muted':        '#718096',
  /* Surface */
  '--nubi-accent':          '#1e2433',
  '--nubi-border':          '#2d3748',
  /* Palette */
  '--nubi-primary':         '#6366f1',
  '--nubi-primary-fg':      '#ffffff',
  '--nubi-success':         '#10b981',
  '--nubi-warning':         '#f59e0b',
  '--nubi-error':           '#ef4444',
  /* Shape */
  '--nubi-radius':          '8px',
  '--nubi-radius-sm':       '4px',
  /* Typography */
  '--nubi-font-sans':       "system-ui, -apple-system, 'Segoe UI', sans-serif",
  '--nubi-font-mono':       "'Fira Code', 'Cascadia Code', Consolas, monospace",
  '--nubi-font-size-base':  '13px',
  '--nubi-font-size-sm':    '11px',
  '--nubi-font-size-xs':    '10px',
  '--nubi-line-height':     '1.5',
  /* Layout */
  '--nubi-toolbar-h':       '36px',
  /* Z-layers */
  '--nubi-z-editor':        '100',
  '--nubi-z-overlay':       '200',
  '--nubi-z-popover':       '300',
  /* Motion */
  '--nubi-transition':      '0.15s ease',
}

const NUBI_THEME_TOKENS_LIGHT = {
  '--nubi-bg':              '#ffffff',
  '--nubi-bg-2':            '#f8f9fc',
  '--nubi-bg-3':            '#f1f3f7',
  '--nubi-fg':              '#1a202c',
  '--nubi-fg-muted':        '#718096',
  '--nubi-accent':          '#edf2f7',
  '--nubi-border':          '#e2e8f0',
  '--nubi-primary':         '#4f46e5',
  '--nubi-primary-fg':      '#ffffff',
  '--nubi-success':         '#059669',
  '--nubi-warning':         '#d97706',
  '--nubi-error':           '#dc2626',
  '--nubi-radius':          '8px',
  '--nubi-radius-sm':       '4px',
  '--nubi-font-sans':       "system-ui, -apple-system, 'Segoe UI', sans-serif",
  '--nubi-font-mono':       "'Fira Code', 'Cascadia Code', Consolas, monospace",
  '--nubi-font-size-base':  '13px',
  '--nubi-font-size-sm':    '11px',
  '--nubi-font-size-xs':    '10px',
  '--nubi-line-height':     '1.5',
  '--nubi-toolbar-h':       '36px',
  '--nubi-z-editor':        '100',
  '--nubi-z-overlay':       '200',
  '--nubi-z-popover':       '300',
  '--nubi-transition':      '0.15s ease',
}

/** Preset maps keyed by name. */
export const NUBI_THEME_PRESETS = {
  dark:  NUBI_THEME_TOKENS,
  light: NUBI_THEME_TOKENS_LIGHT,
}

/**
 * Build a CSS string of :host { … } custom property declarations.
 * Suitable for injection into a <style> element inside a shadow root.
 *
 * @param {Record<string,string>} tokens
 * @returns {string}
 */
function buildThemeCss(tokens) {
  const decls = Object.entries(tokens)
    .map(([k, v]) => `  ${k}: ${v};`)
    .join('\n')
  return `:host {\n${decls}\n}\n`
}

/** CSS string for the dark preset — inject into shadow DOM <style> tags. */
export const NUBI_THEME_CSS = buildThemeCss(NUBI_THEME_TOKENS)

/**
 * Apply theme tokens as inline CSS custom properties on any element.
 * Each widget's host element gets independent theming via this call.
 *
 * @param {HTMLElement} element        — the element to style (usually `this` in a CE)
 * @param {'dark'|'light'} [preset]    — base preset (default 'dark')
 * @param {Record<string,string>} [overrides] — token overrides to merge on top
 */
export function applyTheme(element, preset = 'dark', overrides = {}) {
  const base = NUBI_THEME_PRESETS[preset] ?? NUBI_THEME_PRESETS.dark
  const merged = { ...base, ...overrides }
  for (const [k, v] of Object.entries(merged)) {
    element.style.setProperty(k, v)
  }
}
