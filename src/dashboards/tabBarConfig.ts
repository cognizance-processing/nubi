/**
 * tabBarConfig.ts — the board-level tab-bar style a spec carries.
 *
 * The editor's Tabs panel writes `spec.tab_bar` (snake_case, like every other
 * persisted spec key); a few boards written by older tools carry `spec.tabBar`.
 * The renderer used to read only `tabBar`, so a tab-bar style saved from the
 * editor showed in the editor preview and vanished on the live board.
 */
import { parseCssString } from './widgetHtml.js'

export function tabBarConfig(spec: { tab_bar?: any; tabBar?: any } | null | undefined): any {
  return spec?.tab_bar ?? spec?.tabBar
}

export const TAB_BAR_ALIGNS = ['start', 'center', 'end', 'stretch'] as const
export const TAB_BAR_SIZES = ['sm', 'md', 'lg'] as const
export type TabBarAlign = typeof TAB_BAR_ALIGNS[number]
export type TabBarSize = typeof TAB_BAR_SIZES[number]

export interface ResolvedTabBar {
  variant: string
  accent: string | null
  align: TabBarAlign
  size: TabBarSize
  /** custom_css, whitelisted through parseCssString (the widget-card path). */
  style: Record<string, string> | undefined
}

/**
 * Normalise a tab-bar config (from `tabBarConfig(spec)` or the editor's draft)
 * into what TabBar renders. Unknown align/size values fall back to the
 * defaults the editor shows; custom CSS goes through the same sanitising
 * parser as widget `style.css`, so it can only ever become a whitelisted
 * inline style on the tablist element — never a stylesheet.
 */
export function resolveTabBar(raw: any): ResolvedTabBar {
  const cfg = raw && typeof raw === 'object' ? raw : {}
  return {
    variant: typeof cfg.variant === 'string' && cfg.variant ? cfg.variant : 'underline',
    accent: typeof cfg.accent === 'string' && cfg.accent ? cfg.accent : null,
    align: (TAB_BAR_ALIGNS as readonly string[]).includes(cfg.align) ? cfg.align : 'start',
    size: (TAB_BAR_SIZES as readonly string[]).includes(cfg.size) ? cfg.size : 'md',
    style: cssStyle(cfg.custom_css ?? cfg.customCss),
  }
}

/** A single tab's `style.custom_css`, sanitised the same way. */
export function tabCustomStyle(tab: any): Record<string, string> | undefined {
  return cssStyle(tab?.style?.custom_css ?? tab?.style?.customCss)
}

function cssStyle(css: unknown): Record<string, string> | undefined {
  if (typeof css !== 'string' || !css.trim()) return undefined
  const out = parseCssString(css)
  return Object.keys(out).length ? out : undefined
}
