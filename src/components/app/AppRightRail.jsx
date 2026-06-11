/**
 * AppRightRail — the persistent right-edge switcher rail.
 *
 * A slim, always-present vertical icon strip pinned to the right edge of the
 * authenticated app shell. It is the single, consistent entry point for the
 * shell-level right-hand panels (Git / Versions, and AI Chat) across EVERY
 * authed surface — dashboards, queries, flows and the editor — so the git
 * surface is never hidden behind a page setting `topbarSlot`.
 *
 * Visual language mirrors the established RHS panel toggles used in FlowsPage
 * and the dashboard editor: square icon buttons, `bg-primary text-primary-fg
 * border-primary` when active, muted/hover otherwise.
 *
 * This rail composes WITH page-internal RHS panels (the editor / flows keep
 * their own in-page panel toggles); the shell panels it opens (git / chat)
 * slide in as siblings of the page content, never replacing those panels.
 *
 * Props:
 *   items {Array<{ id, Icon, label, active, onToggle, hidden?, badge? }>}
 *     — the toggles. `badge` (a number) renders an unread-count pill on the
 *       icon (used by the notifications bell); 0/undefined hides it.
 */

import {
  visibleRailItems,
  railItemAriaLabel,
  formatBadgeCount,
} from '../../shell/shellLogic.js'

export default function AppRightRail({ items }) {
  const visible = visibleRailItems(items)
  if (visible.length === 0) return null

  return (
    <div
      className="hidden md:flex shrink-0 flex-col items-center gap-1.5 py-3 px-2 border-l border-border bg-surface/60"
      role="toolbar"
      aria-orientation="vertical"
      aria-label="Panels"
      data-testid="app-right-rail"
    >
      {visible.map(({ id, Icon, label, active, onToggle, badge }) => (
        <button
          key={id}
          type="button"
          onClick={onToggle}
          aria-label={railItemAriaLabel({ active, label, badge })}
          aria-pressed={active}
          title={label}
          data-testid={`rail-toggle-${id}`}
          className={[
            'relative w-9 h-9 flex items-center justify-center rounded-lg border transition-colors duration-150',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
            active
              ? 'bg-primary text-primary-fg border-primary shadow-sm'
              : 'bg-surface text-muted border-border hover:text-fg hover:bg-surface-2',
          ].join(' ')}
        >
          <Icon size={16} strokeWidth={2} />
          {badge > 0 && (
            <span
              className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-bold leading-none shadow"
              aria-hidden="true"
            >
              {formatBadgeCount(badge)}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}
