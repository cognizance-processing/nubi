/**
 * theme.js — Unified theme token contract for Nubi web components.
 *
 * All <nubi-*> web components read their visual appearance from CSS custom
 * properties injected into their shadow root via `injectTheme()`. The token
 * names here are the ONLY names components should reference.
 *
 * Token groups
 * ------------
 *   Colors     --nubi-bg / fg / accent / border / surface / surface-alt /
 *              muted / error / success / warning
 *   Typography --nubi-font-family / font-size-base / font-size-sm / font-size-lg /
 *              font-weight-normal / font-weight-bold
 *   Spacing    --nubi-space-xs … xl
 *   Radius     --nubi-radius-sm / md / lg
 *   Branding   --nubi-brand-logo-url / brand-show (1 = show, 0 = hide)
 */

export const NUBI_TOKENS = Object.freeze([
  '--nubi-bg', '--nubi-fg', '--nubi-accent', '--nubi-border',
  '--nubi-surface', '--nubi-surface-alt', '--nubi-muted',
  '--nubi-error', '--nubi-success', '--nubi-warning',
  '--nubi-font-family', '--nubi-font-size-base', '--nubi-font-size-sm',
  '--nubi-font-size-lg', '--nubi-font-weight-normal', '--nubi-font-weight-bold',
  '--nubi-space-xs', '--nubi-space-sm', '--nubi-space-md',
  '--nubi-space-lg', '--nubi-space-xl',
  '--nubi-radius-sm', '--nubi-radius-md', '--nubi-radius-lg',
  '--nubi-brand-logo-url', '--nubi-brand-show',
])

export const NUBI_TOKEN_DEFAULTS = Object.freeze({
  '--nubi-bg':          '#0f1117',
  '--nubi-fg':          '#e2e8f0',
  '--nubi-accent':      '#1e2433',
  '--nubi-border':      '#2d3748',
  '--nubi-surface':     '#16223b',
  '--nubi-surface-alt': '#1c2d47',
  '--nubi-muted':       '#566377',
  '--nubi-error':       '#ef4444',
  '--nubi-success':     '#10b981',
  '--nubi-warning':     '#f59e0b',
  '--nubi-font-family':        "system-ui, -apple-system, 'Segoe UI', sans-serif",
  '--nubi-font-size-base':     '13px',
  '--nubi-font-size-sm':       '11px',
  '--nubi-font-size-lg':       '16px',
  '--nubi-font-weight-normal': '400',
  '--nubi-font-weight-bold':   '600',
  '--nubi-space-xs': '4px',
  '--nubi-space-sm': '8px',
  '--nubi-space-md': '12px',
  '--nubi-space-lg': '20px',
  '--nubi-space-xl': '32px',
  '--nubi-radius-sm': '4px',
  '--nubi-radius-md': '8px',
  '--nubi-radius-lg': '12px',
  '--nubi-brand-logo-url': '',
  '--nubi-brand-show':     '1',
})

export const BRANDING_OFF_TOKENS = Object.freeze({
  '--nubi-brand-show': '0',
})

/**
 * Inject a `:host { …token vars… }` style block into a shadow root.
 * Tagged with `data-nubi-theme` so subsequent calls replace rather than
 * append. Tokens resolve as: tokenOverrides > NUBI_TOKEN_DEFAULTS.
 *
 * @param {ShadowRoot} shadowRoot
 * @param {Record<string, string>} [tokenOverrides]
 */
export function injectTheme(shadowRoot, tokenOverrides = {}) {
  const merged = { ...NUBI_TOKEN_DEFAULTS, ...tokenOverrides }
  const lines = Object.entries(merged).map(([k, v]) => `  ${k}: ${v};`).join('\n')
  const css = `:host {\n${lines}\n}`

  let styleEl = shadowRoot.querySelector('style[data-nubi-theme]')
  if (!styleEl) {
    styleEl = document.createElement('style')
    styleEl.setAttribute('data-nubi-theme', '1')
    shadowRoot.insertBefore(styleEl, shadowRoot.firstChild)
  }
  styleEl.textContent = css
}
