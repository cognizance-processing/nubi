/**
 * TabBar.jsx — standalone, accessible tablist for dashboard tabs (Track T).
 *
 * See DASHBOARD_TABS_AND_FILTERS_IMPLEMENTATION.md (Track T2). This component is
 * deliberately self-contained: it imports nothing from the rest of the app (only
 * React and one pure contrast helper) so it can be dropped into SpecRenderer / EditorPage post-merge without
 * pulling in shared dependencies.
 *
 * Accessibility follows the WAI-ARIA tablist pattern (full arrow-key navigation,
 * cribbed from the keyboard handling in src/components/pricing/PricingCalculator.jsx):
 *   - role="tablist" on the container
 *   - role="tab" + aria-selected + aria-controls on each tab button
 *   - Left/Right/Home/End move the selection; Enter/Space activate
 *
 * User-supplied colors (tab.style.accent / tabBar.accent) are applied via inline
 * `style` ONLY — they are sanitized upstream and must never be injected as
 * arbitrary class names.
 *
 * Props
 * -----
 *   tabs        Array<{ id, label, style? }>   the tabs to render
 *   activeTabId string                          id of the currently-active tab
 *   onChange    (id: string) => void            called when a tab is activated
 *   tabBar      { variant?, accent? }           optional bar-level style config
 *                 variant: 'underline' | 'pills' | 'segmented' (default 'underline')
 *
 * The bar hides itself entirely when tabs.length <= 1.
 */

import { useRef } from 'react'
import { readableInk } from '../lib/contrast.js'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

// aria-controls points at the panel a renderer would mount for this tab.
function panelId(tabId) {
  return `dashboard-tabpanel-${tabId}`
}

function tabButtonId(tabId) {
  return `dashboard-tab-${tabId}`
}

export default function TabBar({ tabs, activeTabId, onChange, tabBar }) {
  const tabRefs = useRef([])

  // Hide the bar when there is nothing meaningful to switch between.
  if (!Array.isArray(tabs) || tabs.length <= 1) return null

  const variant = tabBar?.variant ?? 'underline'
  const barAccent = tabBar?.accent ?? null

  const focusTab = (index) => {
    const node = tabRefs.current[index]
    if (node) node.focus()
  }

  // WAI-ARIA tablist keyboard navigation. Arrow keys move selection (and focus)
  // immediately; Home/End jump to the ends; Enter/Space (re)activate the focused
  // tab. We activate on move so the panel follows focus, matching the automatic-
  // activation flavour of the pattern.
  const onKeyDown = (event, index) => {
    let nextIndex = null
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        nextIndex = (index + 1) % tabs.length
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        nextIndex = (index - 1 + tabs.length) % tabs.length
        break
      case 'Home':
        nextIndex = 0
        break
      case 'End':
        nextIndex = tabs.length - 1
        break
      case 'Enter':
      case ' ':
      case 'Spacebar':
        event.preventDefault()
        onChange?.(tabs[index].id)
        return
      default:
        return
    }

    if (nextIndex != null) {
      event.preventDefault()
      focusTab(nextIndex)
      onChange?.(tabs[nextIndex].id)
    }
  }

  // Swiss: square corners, hairlines, no shadow. The active tab is marked by a
  // 2px rule (underline), an ink fill (pills) or a raised hairline (segmented).
  const containerClass = {
    underline: 'flex items-stretch gap-0 border-b border-border overflow-x-auto',
    pills: 'flex items-center gap-1.5 overflow-x-auto',
    segmented: 'inline-flex items-center border border-frame-line bg-surface-2 p-0.5 rounded-sm max-w-full overflow-x-auto',
  }[variant] ?? 'flex items-stretch gap-0 border-b border-border overflow-x-auto'

  return (
    <div
      role="tablist"
      aria-label="Dashboard tabs"
      aria-orientation="horizontal"
      className={containerClass}
      data-variant={variant}
    >
      {tabs.map((tab, index) => {
        const isActive = tab.id === activeTabId
        // Per-tab accent overrides the bar-level accent.
        const accent = tab.style?.accent ?? barAccent ?? null

        const baseBtn = 'relative shrink-0 whitespace-nowrap text-[13px] transition-colors duration-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring'

        let variantBtn
        let inlineStyle
        switch (variant) {
          case 'pills':
            variantBtn = cx(
              'h-8 px-3 rounded-sm border',
              isActive
                ? 'border-[var(--ink)] bg-[var(--ink)] font-semibold text-[var(--ink-fg)]'
                : 'border-frame-line bg-surface font-medium text-muted hover:border-fg hover:text-fg',
            )
            inlineStyle = isActive && accent
              ? { backgroundColor: accent, borderColor: accent, color: readableInk(accent) ?? 'var(--primary-fg)' }
              : undefined
            break
          case 'segmented':
            variantBtn = cx(
              'h-7 px-3 rounded-sm',
              isActive
                ? 'border border-frame-line bg-surface font-semibold text-fg'
                : 'border border-transparent font-medium text-muted hover:text-fg',
            )
            inlineStyle = isActive && accent ? { color: accent } : undefined
            break
          case 'underline':
          default:
            // The active tab carries a 2px rule that sits on the bar's hairline.
            // `bg-current` makes the rule follow the label colour, so a tab with
            // its own accent underlines in that accent.
            variantBtn = cx(
              'h-10 px-3.5',
              isActive
                ? 'font-semibold text-fg after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:bg-current'
                : 'font-medium text-muted hover:bg-surface-2 hover:text-fg',
            )
            inlineStyle = isActive && accent ? { color: accent } : undefined
            break
        }

        return (
          <button
            key={tab.id}
            ref={(el) => {
              tabRefs.current[index] = el
            }}
            id={tabButtonId(tab.id)}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-controls={panelId(tab.id)}
            tabIndex={isActive ? 0 : -1}
            className={cx(baseBtn, variantBtn)}
            style={inlineStyle}
            onClick={() => onChange?.(tab.id)}
            onKeyDown={(e) => onKeyDown(e, index)}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

export { panelId as tabPanelId, tabButtonId }
