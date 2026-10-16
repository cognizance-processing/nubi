/**
 * Nubi Design System — Tailwind Config
 *
 * ── SEMANTIC TOKEN CLASSES (light + dark automatic) ──────────────────────────
 *
 *  Backgrounds:
 *  Swiss tokens: see reports/design-system-tokens.md and src/index.css.
 *    bg-bg          → frame                  light:#eceef1      dark:#0d0e10
 *    bg-surface     → sheet / card / menu    light:#ffffff      dark:#141518
 *    bg-surface-2   → sunk / row hover       light:#f5f6f8      dark:#1a1b1e
 *    bg-surface-3   → pressed / skeleton     light:#e7e9ed      dark:#202227
 *
 *    text-fg        → ink                    light:#0b0c0e      dark:#f4f5f7
 *    text-muted     → secondary copy         light:#4f545e      dark:#a7abb4
 *    text-subtle    → captions (AA)          light:#646974      dark:#8f949e
 *
 *    border-border  → hairline               border-frame-line → stronger hairline
 *    border-control → form-control edge (3:1)
 *
 *    bg-ink / text-ink-fg / hover:bg-ink-hover → primary BUTTON (ink)
 *    text-accent / bg-accent / ring-ring       → selection, focus, links (blue)
 *    bg-primary stays blue: links and focus, NOT button fills.
 *
 *  Deprecated flat aliases (no gradients in Swiss):
 *    bg-brand-gradient   → flat ink fill;  text-brand-gradient → accent colour
 *
 *  Typography:
 *    font-display  → Space Grotesk (headings, brand wordmarks)
 *    font-sans     → Inter (body copy)
 *
 * ─────────────────────────────────────────────────────────────────────────────
 */

/** @type {import('tailwindcss').Config} */
export default {
  // `dark:` utilities follow the nearest light/dark scope, not just <html>:
  // a light board inside the dark app must not pick up dark variants.
  darkMode: ['variant', '&:is(.dark *):not(.nubi-light *)'],

  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],

  theme: {
    extend: {
      colors: {
        // ── Semantic tokens — map to CSS vars so light/dark flips automatically ──
        bg:        'var(--bg)',
        surface:   'var(--surface)',
        'surface-2': 'var(--surface-2)',
        'surface-3': 'var(--surface-3)',
        fg:        'var(--text)',
        muted:     'var(--text-muted)',
        subtle:    'var(--text-subtle)',
        border:    'var(--border)',
        'border-control': 'var(--border-control)',
        'frame-line': 'var(--frame-line)',
        'frame-hover': 'var(--frame-hover)',
        primary:   'var(--primary)',
        'primary-fg': 'var(--primary-fg)',
        accent:    'var(--accent)',
        'accent-soft': 'var(--accent-soft)',
        ring:      'var(--ring)',
        ink:       'var(--ink)',
        'ink-fg':  'var(--ink-fg)',
        'ink-hover': 'var(--ink-hover)',

        // ── Data-viz neutral palette (chrome, illustrations, marketing; never board charts) ──
        'viz-1': 'var(--viz-1)', 'viz-2': 'var(--viz-2)', 'viz-3': 'var(--viz-3)', 'viz-4': 'var(--viz-4)',
        'viz-5': 'var(--viz-5)', 'viz-6': 'var(--viz-6)', 'viz-7': 'var(--viz-7)', 'viz-8': 'var(--viz-8)',
        'viz-grid': 'var(--viz-grid)', 'viz-axis': 'var(--viz-axis)',
        'viz-pos': 'var(--viz-pos)', 'viz-neg': 'var(--viz-neg)',

        // ── Semantic status colors ────────────────────────────────────────────
        success:      'var(--success)',
        'success-bg': 'var(--success-bg)',
        'success-fg': 'var(--success-fg)',
        warning:      'var(--warning)',
        'warning-bg': 'var(--warning-bg)',
        'warning-fg': 'var(--warning-fg)',
        danger:       'var(--danger)',
        'danger-solid': 'var(--danger-solid)',
        'danger-solid-hover': 'var(--danger-solid-hover)',
        'danger-bg':  'var(--danger-bg)',
        'danger-fg':  'var(--danger-fg)',
        info:         'var(--info)',
        'info-bg':    'var(--info-bg)',
        'info-fg':    'var(--info-fg)',

        // ── Brand constants (not theme-dependent) ────────────────────────────
        // Swiss: teal was the old brand accent in chrome (pricing, billing, settings);
        // remap the whole scale onto the one blue so every teal-* utility follows.
        teal: {
          50: '#eef2fd', 100: '#dfe7fb', 200: '#c3d1f7', 300: '#9fb5f2', 400: '#7d9bff',
          500: '#4f74e3', 600: '#1f4fd1', 700: '#1a42b0', 800: '#16378f', 900: '#122c72', 950: '#0b1a45',
        },
        // Swiss: brand collapses to ink + one blue (navy→ink, blue/teal→accent).
        brand: {
          navy: '#0b0c0e',
          blue: '#1f4fd1',
          teal: '#1f4fd1',
          cyan: '#7d9bff',
        },
      },

      fontFamily: {
        display: ['Instrument Sans', 'Helvetica Neue', 'Arial', 'system-ui', 'sans-serif'],
        sans:    ['Instrument Sans', 'Helvetica Neue', 'Arial', 'system-ui', 'sans-serif'],
        mono:    ['Geist Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },

      borderColor: {
        DEFAULT: 'var(--border)',
      },

      // ── Elevation ─────────────────────────────────────────────────────────
      // Swiss: nothing has a shadow at rest. `pop` is the only real shadow
      // (popovers, menus, modals, toasts, Spotlight). nubi-sm/md resolve to
      // "no shadow"; nubi-lg/xl alias pop. Kept so existing classes keep working.
      boxShadow: {
        'pop':      'var(--shadow-pop)',
        'nubi-sm':  'var(--shadow-sm)',
        'nubi-md':  'var(--shadow-md)',
        'nubi-lg':  'var(--shadow-lg)',
        'nubi-xl':  'var(--shadow-xl)',
      },

      // ── Border radius ──────────────────────────────────────────────────────
      // Swiss: square corners everywhere. The default scale is pinned to 2–4px
      // so every existing rounded-* utility follows; `full` stays for dots/avatars.
      borderRadius: {
        sm:        '2px',
        DEFAULT:   '2px',
        md:        '2px',
        lg:        '2px',
        xl:        '3px',
        '2xl':     '4px',
        '3xl':     '4px',
        control:   'var(--radius-control)',
        sheet:     'var(--radius-sheet)',
        'nubi-sm':  'var(--radius-sm)',
        'nubi-md':  'var(--radius-md)',
        'nubi-lg':  'var(--radius-lg)',
        'nubi-xl':  'var(--radius-xl)',
        'nubi-2xl': 'var(--radius-2xl)',
      },

      // ── Z-index scale ──────────────────────────────────────────────────────
      zIndex: {
        'dropdown': 'var(--z-dropdown)',
        'sticky':   'var(--z-sticky)',
        'overlay':  'var(--z-overlay)',
        'modal':    'var(--z-modal)',
        'popover':  'var(--z-popover)',
        'toast':    'var(--z-toast)',
        'tooltip':  'var(--z-tooltip)',
      },

      // ── Typography scale ───────────────────────────────────────────────────
      // Roles: size + line-height + tracking from the type scale in index.css
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        'display': ['var(--t-display)', { lineHeight: 'var(--lh-display)', letterSpacing: 'var(--tr-display)', fontWeight: '600' }],
        'h2':      ['var(--t-h2)',      { lineHeight: 'var(--lh-h2)',      letterSpacing: 'var(--tr-h2)',      fontWeight: '600' }],
        'h3':      ['var(--t-h3)',      { lineHeight: 'var(--lh-h3)',      letterSpacing: 'var(--tr-h3)',      fontWeight: '600' }],
        'page':    ['var(--t-page)',    { lineHeight: 'var(--lh-page)',    letterSpacing: 'var(--tr-page)',    fontWeight: '600' }],
        'section': ['var(--t-section)', { lineHeight: 'var(--lh-section)', fontWeight: '600' }],
        'body':    ['var(--t-body)',    { lineHeight: 'var(--lh-body)' }],
        'small':   ['var(--t-small)',   { lineHeight: 'var(--lh-small)' }],
        'label':   ['var(--t-label)',   { lineHeight: 'var(--lh-label)',   letterSpacing: 'var(--tr-label)' }],
      },

      // ── Motion tokens ──────────────────────────────────────────────────────
      transitionDuration: {
        '1': 'var(--dur-1)', '2': 'var(--dur-2)', '3': 'var(--dur-3)', '4': 'var(--dur-4)',
      },
      transitionTimingFunction: {
        'out': 'var(--ease-out)',
        'in-out-nubi': 'var(--ease-in-out)',
      },

      // ── Animation utilities ────────────────────────────────────────────────
      keyframes: {
        'fade-in':    { from: { opacity: '0' }, to: { opacity: '1' } },
        'scale-in':   { from: { opacity: '0', transform: 'scale(0.96)' }, to: { opacity: '1', transform: 'scale(1)' } },
        'slide-up':   { from: { opacity: '0', transform: 'translateY(4px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        'toast-in':   { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
      },
      // Ease-out only, no overshoot. Global reduced-motion rule in index.css
      // collapses every duration.
      animation: {
        'fade-in':    'fade-in var(--dur-2) var(--ease-out) forwards',
        'scale-in':   'scale-in var(--dur-2) var(--ease-out) forwards',
        'slide-up':   'slide-up var(--dur-3) var(--ease-out) forwards',
        'toast-in':   'toast-in var(--dur-3) var(--ease-out) forwards',
      },
    },
  },

  plugins: [
    // ── DEPRECATED flat aliases. Swiss has no gradients; these keep old call
    // sites rendering ink/accent until each squad migrates (bg-ink, text-accent).
    function ({ addUtilities }) {
      addUtilities({
        '.bg-brand-gradient': { background: 'var(--ink)' },
        '.text-brand-gradient': { color: 'var(--accent)' },
      })
    },
  ],
}
