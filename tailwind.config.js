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
        fg:        'var(--text)',
        muted:     'var(--text-muted)',
        border:    'var(--border)',
        primary:   'var(--primary)',
        'primary-fg': 'var(--primary-fg)',
        accent:    'var(--accent)',
        ring:      'var(--ring)',

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
    },
  },

  plugins: [
    // ── .bg-brand-gradient utility ────────────────────────────────────────
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
