/**
 * stylePresets.js — named bundles of widget/dashboard appearance.
 *
 * Pure data, in the exact shape `styleToCss()` / `backgroundToCss()` already
 * consume (see widgetHtml.js) — no new rendering path, no backend schema
 * change. Adding a future preset is a one-entry addition here, not new
 * plumbing: WidgetAppearanceSection / DashboardPanel just map over these
 * arrays to render preset pickers.
 *
 * Colors are built from the app's semantic CSS custom properties
 * (`var(--surface)`, `var(--border)`, `var(--text)`) via `color-mix()` —
 * the same technique already proven at `.nubi-navbar` (src/index.css) — so
 * every preset renders correctly in both light and dark mode automatically.
 */

export const WIDGET_STYLE_PRESETS = [
  {
    key: 'default',
    label: 'Default',
    description: "Theme's standard card — no override.",
    style: null,
  },
  {
    key: 'card',
    label: 'Card',
    description: 'Clean surface card with a hairline border — corporate, adapts to light & dark.',
    style: {
      // All values reference theme tokens so the card is correct in BOTH themes
      // without any per-widget hard-coding — the surface, border and text all
      // flip automatically. The shadow is a fixed soft ambient (reads on light,
      // harmless on dark where the border carries separation).
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: '14px',
      boxShadow: '0 1px 2px rgba(15, 23, 42, 0.04), 0 6px 20px rgba(15, 23, 42, 0.06)',
    },
  },
  {
    key: 'elevated',
    label: 'Elevated',
    description: 'Raised surface card with a stronger shadow — for hero tiles.',
    style: {
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: '16px',
      boxShadow: '0 2px 4px rgba(15, 23, 42, 0.06), 0 14px 40px rgba(15, 23, 42, 0.10)',
    },
  },
  {
    key: 'glass',
    label: 'Glass',
    description: 'Frosted, translucent card — premium look, pairs best with the Glass dashboard background.',
    style: {
      // Panel tint stays low-opacity so the blurred backdrop reads through —
      // definition comes from the shadow/highlight pair below, not the tint.
      background: 'color-mix(in srgb, var(--surface) 45%, transparent)',
      backdropFilter: 'blur(20px) saturate(180%)',
      WebkitBackdropFilter: 'blur(20px) saturate(180%)',
      // A theme border color at reduced alpha (the old approach) washes out
      // to invisible on both a light --border (#e2e8f0) and a dark one
      // (#21304a) — glass needs a fixed white "rim" highlight instead, which
      // is the actual cue that reads as a glass edge in light AND dark mode.
      border: '1px solid rgba(255, 255, 255, 0.28)',
      borderRadius: '16px',
      // Dark ambient shadow for separation from the page (rgba(0,0,0,0.12)
      // was too faint to register against a dark --bg) + an inset top
      // highlight for the glossy "light hitting glass" sheen. Both are
      // fixed values on purpose — shadow/highlight contrast must stay
      // constant across themes, unlike the tint above.
      boxShadow: '0 8px 32px rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.35)',
    },
  },
]

export const DASHBOARD_STYLE_PRESETS = [
  {
    key: 'default',
    label: 'Default',
    description: "Theme's standard page background.",
    background: null,
  },
  {
    key: 'canvas',
    label: 'Canvas',
    description: 'Subtle corporate page tint with a faint brand glow — adapts to light & dark.',
    background: {
      type: 'css',
      css:
        'background-color: var(--bg); ' +
        'background-image: ' +
          'radial-gradient(1200px 500px at 8% -8%, color-mix(in srgb, var(--primary) 12%, transparent) 0%, transparent 60%), ' +
          'radial-gradient(1000px 460px at 100% 0%, color-mix(in srgb, var(--accent) 10%, transparent) 0%, transparent 60%);',
    },
  },
  {
    key: 'glass',
    label: 'Glass',
    description: 'Soft color backdrop that makes glass widgets pop.',
    background: {
      type: 'css',
      css:
        'background-color: var(--surface-2); ' +
        'background-image: ' +
          'radial-gradient(circle at 12% 8%, color-mix(in srgb, var(--primary) 45%, transparent) 0%, transparent 45%), ' +
          'radial-gradient(circle at 88% 15%, color-mix(in srgb, var(--accent) 40%, transparent) 0%, transparent 48%), ' +
          'radial-gradient(circle at 50% 95%, color-mix(in srgb, var(--primary) 30%, transparent) 0%, transparent 55%);',
    },
  },
]

export function findWidgetStylePreset(key) {
  return WIDGET_STYLE_PRESETS.find(p => p.key === key) ?? WIDGET_STYLE_PRESETS[0]
}

export function findDashboardStylePreset(key) {
  return DASHBOARD_STYLE_PRESETS.find(p => p.key === key) ?? DASHBOARD_STYLE_PRESETS[0]
}
