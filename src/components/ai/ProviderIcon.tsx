/**
 * ProviderIcon — a small on-theme mark for an LLM provider (Anthropic /
 * OpenAI / Google Gemini), used by ModelPicker and the AI providers settings
 * page wherever a provider needs a recognisable, at-a-glance logo.
 *
 * `public/logos/` has no AI-provider SVGs (only data-connector logos), so
 * these are hand-drawn, on-theme abstract marks rather than image files —
 * self-contained (no network fetch / 404 risk), work in light + dark, and
 * scale cleanly at icon sizes. They're deliberately abstract rather than a
 * pixel copy of each vendor's trademark, in the same spirit as ConnectorLogo's
 * generic-icon fallback.
 */

import { useId } from 'react'

const MARKS = {
  anthropic: {
    label: 'Anthropic',
    bg: '#D97757',
    render: (id) => (
      <svg viewBox="0 0 24 24" width="62%" height="62%" fill="none" aria-hidden="true">
        <path d="M12 5l4.6 14h-2.9l-.94-3H11.2l-.94 3H7.36L12 5zm0 4.4l-1.5 4.8h3l-1.5-4.8z" fill={`url(#${id}-a)`} />
        <defs>
          <linearGradient id={`${id}-a`} x1="7" y1="5" x2="17" y2="19" gradientUnits="userSpaceOnUse">
            <stop stopColor="#fff" />
            <stop offset="1" stopColor="#ffe8de" />
          </linearGradient>
        </defs>
      </svg>
    ),
  },
  openai: {
    label: 'OpenAI',
    bg: '#10121A',
    render: (id) => (
      <svg viewBox="0 0 24 24" width="64%" height="64%" fill="none" aria-hidden="true">
        <g stroke={`url(#${id}-o)`} strokeWidth="1.6" strokeLinecap="round">
          <circle cx="12" cy="7" r="2.15" fill={`url(#${id}-o)`} stroke="none" />
          <circle cx="17.2" cy="10" r="2.15" fill={`url(#${id}-o)`} stroke="none" />
          <circle cx="17.2" cy="15.5" r="2.15" fill={`url(#${id}-o)`} stroke="none" />
          <circle cx="12" cy="18.5" r="2.15" fill={`url(#${id}-o)`} stroke="none" />
          <circle cx="6.8" cy="15.5" r="2.15" fill={`url(#${id}-o)`} stroke="none" />
          <circle cx="6.8" cy="10" r="2.15" fill={`url(#${id}-o)`} stroke="none" />
          <path d="M12 7l5.2 3M17.2 10v5.5M17.2 15.5l-5.2 3M12 18.5l-5.2-3M6.8 15.5V10M6.8 10L12 7" />
        </g>
        <defs>
          <linearGradient id={`${id}-o`} x1="6" y1="6" x2="18" y2="19" gradientUnits="userSpaceOnUse">
            <stop stopColor="#fff" />
            <stop offset="1" stopColor="#c9ccd6" />
          </linearGradient>
        </defs>
      </svg>
    ),
  },
  gemini: {
    label: 'Google Gemini',
    bg: '#FFFFFF',
    border: true,
    render: (id) => (
      <svg viewBox="0 0 24 24" width="66%" height="66%" fill="none" aria-hidden="true">
        <path
          d="M12 2c.6 4.9 2.2 8 5.1 9.3.5.2.5.9 0 1.1-2.9 1.3-4.5 4.4-5.1 9.3-.05.4-.6.4-.7 0-.6-4.9-2.2-8-5.1-9.3-.5-.2-.5-.9 0-1.1C9.2 10 10.8 6.9 11.3 2c.05-.4.65-.4.7 0z"
          fill={`url(#${id}-g)`}
        />
        <defs>
          <linearGradient id={`${id}-g`} x1="4" y1="2" x2="20" y2="22" gradientUnits="userSpaceOnUse">
            <stop stopColor="#4C8DF6" />
            <stop offset="0.5" stopColor="#9168C0" />
            <stop offset="1" stopColor="#F16C6C" />
          </linearGradient>
        </defs>
      </svg>
    ),
  },
}

/**
 * @param {{
 *   provider: string,        // 'anthropic' | 'openai' | 'gemini' | any other id
 *   size?: number,
 *   className?: string,
 * }} props
 */
export default function ProviderIcon({ provider, size = 20, className = '' }) {
  const mark = MARKS[provider]
  const box = size + 12
  // Stable per-instance id for gradient defs (avoids collisions when the same
  // provider renders multiple times on one page, e.g. a picker list).
  const gid = 'provider-icon' + useId().replace(/[^a-zA-Z0-9-]/g, '')

  if (!mark) {
    return (
      <span
        className={`inline-flex items-center justify-center rounded-lg border border-border bg-surface-2 shrink-0 ${className}`}
        style={{ width: box, height: box }}
        aria-hidden="true"
      >
        <span className="text-[10px] font-bold text-muted uppercase">{(provider || '?').slice(0, 1)}</span>
      </span>
    )
  }

  return (
    <span
      className={`inline-flex items-center justify-center rounded-lg shrink-0 ${mark.border ? 'border border-border' : ''} ${className}`}
      style={{ width: box, height: box, background: mark.bg }}
      role="img"
      aria-label={`${mark.label} logo`}
    >
      {mark.render(gid)}
    </span>
  )
}
