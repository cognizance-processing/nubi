/**
 * Nubi Design System — Tailwind Config
 *
 * ── SEMANTIC TOKEN CLASSES (light + dark automatic) ──────────────────────────
 *
 *  Backgrounds:
 *    bg-bg          → page background        light:#f6f8fb      dark:#0a1020
 *    bg-surface     → card / panel           light:#ffffff      dark:#111a2e
 *    bg-surface-2   → inset / hover surface  light:#eef2f7      dark:#16223b
 *
 *  Text:
 *    text-fg        → primary body text      light:#0e1729      dark:#e7edf6
 *    text-muted     → secondary / captions   light:#566377      dark:#93a4bd
 *
 *  Border:
 *    border-border  → default border         light:#e2e8f0      dark:#21304a
 *
 *  Accent / Interactive:
 *    bg-primary     → CTA button bg          light:#2456a6      dark:#4d8de0
 *    text-primary   → link / icon            light:#2456a6      dark:#4d8de0
 *    text-primary-fg → text on primary bg    light:#ffffff      dark:#06101f
 *    bg-accent      → teal accent            light:#0f9e90      dark:#2dd4bf
 *    ring-ring      → focus ring             light:#17b3a3      dark:#2dd4bf
 *
 *  Brand constants (always same):
 *    text-brand-navy  #1b2363
 *    text-brand-blue  #2456a6
 *    text-brand-teal  #17b3a3
 *    text-brand-cyan  #2dd4bf
 *
 *  Gradient utility:
 *    bg-brand-gradient   → linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)
 *
 *  Typography:
 *    font-display  → Space Grotesk (headings, brand wordmarks)
 *    font-sans     → Inter (body copy)
 *
 * ─────────────────────────────────────────────────────────────────────────────
 */

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',

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
        primary:   'var(--primary)',
        'primary-fg': 'var(--primary-fg)',
        accent:    'var(--accent)',
        ring:      'var(--ring)',

        // ── Semantic status colors ────────────────────────────────────────────
        success:      'var(--success)',
        'success-bg': 'var(--success-bg)',
        'success-fg': 'var(--success-fg)',
        warning:      'var(--warning)',
        'warning-bg': 'var(--warning-bg)',
        'warning-fg': 'var(--warning-fg)',
        danger:       'var(--danger)',
        'danger-bg':  'var(--danger-bg)',
        'danger-fg':  'var(--danger-fg)',
        info:         'var(--info)',
        'info-bg':    'var(--info-bg)',
        'info-fg':    'var(--info-fg)',

        // ── Brand constants (not theme-dependent) ────────────────────────────
        brand: {
          navy: '#1b2363',
          blue: '#2456a6',
          teal: '#17b3a3',
          cyan: '#2dd4bf',
        },
      },

      fontFamily: {
        display: ['Space Grotesk', 'system-ui', 'sans-serif'],
        sans:    ['Inter', 'system-ui', 'sans-serif'],
        mono:    ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },

      borderColor: {
        DEFAULT: 'var(--border)',
      },

      // ── Shadow scale ───────────────────────────────────────────────────────
      boxShadow: {
        'nubi-sm':  'var(--shadow-sm)',
        'nubi-md':  'var(--shadow-md)',
        'nubi-lg':  'var(--shadow-lg)',
        'nubi-xl':  'var(--shadow-xl)',
      },

      // ── Border radius ──────────────────────────────────────────────────────
      borderRadius: {
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
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },

      // ── Animation utilities ────────────────────────────────────────────────
      keyframes: {
        'fade-in':    { from: { opacity: '0' }, to: { opacity: '1' } },
        'scale-in':   { from: { opacity: '0', transform: 'scale(0.95)' }, to: { opacity: '1', transform: 'scale(1)' } },
        'slide-up':   { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        'toast-in':   { from: { opacity: '0', transform: 'translateX(100%)' }, to: { opacity: '1', transform: 'translateX(0)' } },
      },
      animation: {
        'fade-in':    'fade-in 150ms ease forwards',
        'scale-in':   'scale-in 160ms cubic-bezier(0.34, 1.2, 0.64, 1) forwards',
        'slide-up':   'slide-up 180ms cubic-bezier(0.22, 1, 0.36, 1) forwards',
        'toast-in':   'toast-in 280ms cubic-bezier(0.22, 1, 0.36, 1) forwards',
      },
    },
  },

  plugins: [
    // ── .bg-brand-gradient + .text-brand-gradient utilities ───────────────
    function ({ addUtilities }) {
      addUtilities({
        '.bg-brand-gradient': {
          background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)',
        },
        '.text-brand-gradient': {
          background: 'var(--brand-text-gradient, linear-gradient(135deg, #1b2363, #2456a6, #17b3a3))',
          '-webkit-background-clip': 'text',
          '-webkit-text-fill-color': 'transparent',
          'background-clip': 'text',
        },
      })
    },
  ],
}
