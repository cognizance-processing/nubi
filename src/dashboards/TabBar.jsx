/**
 * TabBar.jsx — standalone, accessible tablist for dashboard tabs (Track T).
 *
 * See DASHBOARD_TABS_AND_FILTERS_IMPLEMENTATION.md (Track T2). This component is
 * deliberately self-contained: it imports nothing from the rest of the app (only
 * React) so it can be dropped into SpecRenderer / EditorPage post-merge without
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

  const containerClass = {
    underline: 'flex items-stretch gap-1 border-b border-border',
    pills: 'flex items-center gap-1.5',
    segmented: 'inline-flex items-center gap-1 p-1 rounded-xl bg-surface-2 border border-border',
  }[variant] ?? 'flex items-stretch gap-1 border-b border-border'

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

        const baseBtn = 'relative text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-0'

        let variantBtn
        let inlineStyle
        switch (variant) {
          case 'pills':
            variantBtn = cx(
              'px-3.5 py-1.5 rounded-full',
              isActive
                ? 'text-white'
                : 'bg-surface-2 text-muted hover:text-fg border border-border',
            )
            inlineStyle = isActive && accent ? { backgroundColor: accent } : undefined
            break
          case 'segmented':
            variantBtn = cx(
              'px-3.5 py-1.5 rounded-lg',
              isActive
                ? 'bg-surface text-fg shadow-sm'
                : 'text-muted hover:text-fg',
            )
            inlineStyle = isActive && accent ? { color: accent } : undefined
            break
          case 'underline':
          default:
            // Active tab is underlined via a bottom border that sits on the bar line.
            variantBtn = cx(
              'px-3.5 py-2 -mb-px border-b-2',
              isActive
                ? 'text-fg border-accent'
                : 'text-muted hover:text-fg border-transparent',
            )
            inlineStyle = isActive && accent
              ? { color: accent, borderBottomColor: accent }
              : undefined
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
