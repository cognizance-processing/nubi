/**
 * DateRangePicker.jsx — date-range input with a preset rail.
 *
 * Presets (Today, Yesterday, Last 7/30/90 days, MTD, QTD, YTD, Custom) are
 * stored as { preset: "last_30d" } so saved dashboards stay *relative*; the
 * concrete { from, to } is resolved at param-resolution time (resolvePreset in
 * helpers.js, unit-testable with an injectable clock).
 *
 * The rail + two native date inputs live in a portal popover so they escape the
 * grid's overflow-hidden clipping. The trigger shows a human summary.
 *
 * Value shape:
 *   { preset: "last_30d" }            relative
 *   { from: "2026-01-01", to: "…" }   absolute (preset === 'custom')
 *   null / { from:'', to:'' }         unset
 * A plain { from, to } object remains fully backward compatible.
 */

import { useId, useMemo, useRef, useState } from 'react'
import Popover from './Popover.jsx'
import {
  DEFAULT_PRESETS,
  PRESET_LABELS,
  resolvePreset,
  resolveDateRange,
} from './helpers.js'

const SIZES = {
  sm: 'text-xs px-2.5 py-1.5',
  md: 'text-sm px-3 py-2',
  lg: 'text-base px-3.5 py-2.5',
}

/**
 * @param {{
 *   label?: string,
 *   value: unknown,                          // {preset} | {from,to} | null
 *   onChange: (next: unknown) => void,
 *   presets?: string[],
 *   size?: 'sm' | 'md' | 'lg',
 *   styleVars?: Record<string, string>,
 * }} props
 */
export default function DateRangePicker({
  label,
  value,
  onChange,
  presets = DEFAULT_PRESETS,
  size = 'md',
  styleVars,
}) {
  const uid = useId()
  const [open, setOpen] = useState(false)
  const triggerRef = useRef(null)

  const activePreset = value && typeof value === 'object' && value.preset ? value.preset : null
  const resolved = useMemo(() => resolveDateRange(value), [value])

  const summary = useMemo(() => {
    if (activePreset && activePreset !== 'custom') return PRESET_LABELS[activePreset] ?? activePreset
    if (resolved.from && resolved.to) return `${resolved.from} → ${resolved.to}`
    if (resolved.from) return `From ${resolved.from}`
    if (resolved.to) return `Until ${resolved.to}`
    return null
  }, [activePreset, resolved])

  function pickPreset(preset) {
    if (preset === 'custom') {
      // Seed the custom inputs from the currently-resolved range.
      onChange({ from: resolved.from, to: resolved.to })
    } else {
      onChange({ preset })
      setOpen(false)
      triggerRef.current?.focus()
    }
  }

  function setFrom(from) {
    onChange({ from, to: resolved.to })
  }
  function setTo(to) {
    onChange({ from: resolved.from, to })
  }

  const isCustom = !activePreset || activePreset === 'custom'

  return (
    <div className="flex flex-col gap-1 h-full px-5 py-4">
      {label && (
        <label htmlFor={uid} className="text-xs font-semibold text-muted uppercase tracking-wider">
          {label}
        </label>
      )}
      <div className="relative">
        <button
          id={uid}
          ref={triggerRef}
          type="button"
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen(o => !o)}
          className={[
            'w-full flex items-center justify-between gap-2 rounded-lg border border-border bg-surface text-fg',
            SIZES[size] ?? SIZES.md,
            'focus:outline-none focus:ring-2 focus:ring-brand-teal/40 cursor-pointer',
          ].join(' ')}
        >
          <span className={summary ? 'truncate' : 'truncate text-muted'}>
            {summary ?? 'Any time'}
          </span>
          <span className="flex items-center gap-1 shrink-0">
            {summary && (
              <span
                role="button"
                tabIndex={0}
                aria-label="Clear date range"
                onClick={e => { e.stopPropagation(); onChange({ from: '', to: '' }) }}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); onChange({ from: '', to: '' }) } }}
                className="text-[11px] text-muted hover:text-fg px-1 rounded"
              >
                ✕
              </span>
            )}
            <span aria-hidden="true" className="text-muted">▾</span>
          </span>
        </button>

        <Popover
          anchorRef={triggerRef}
          open={open}
          onClose={() => setOpen(false)}
          styleVars={styleVars}
          matchWidth={false}
          role="dialog"
          ariaLabel={label ? `${label} date range` : 'Date range'}
        >
          <div className="flex">
            {/* Preset rail */}
            <div className="flex flex-col py-1 border-r border-border min-w-[9rem]" role="listbox" aria-label="Presets">
              {presets.map(p => {
                const isActive = p === 'custom' ? isCustom : activePreset === p
                return (
                  <button
                    key={p}
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    onClick={() => pickPreset(p)}
                    className={[
                      'text-left px-3 py-1.5 text-sm transition-colors',
                      isActive ? 'bg-brand-teal/10 text-brand-teal font-medium' : 'text-fg hover:bg-surface-2/70',
                    ].join(' ')}
                  >
                    {PRESET_LABELS[p] ?? p}
                  </button>
                )
              })}
            </div>

            {/* Custom range inputs */}
            <div className="p-3 flex flex-col gap-2 min-w-[12rem]">
              <label className="flex flex-col gap-1 text-xs text-muted">
                From
                <input
                  type="date"
                  value={resolved.from || ''}
                  onChange={e => setFrom(e.target.value)}
                  className="rounded-md border border-border bg-surface text-fg text-sm px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-teal/40"
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted">
                To
                <input
                  type="date"
                  value={resolved.to || ''}
                  min={resolved.from || undefined}
                  onChange={e => setTo(e.target.value)}
                  className="rounded-md border border-border bg-surface text-fg text-sm px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-teal/40"
                />
              </label>
              <div className="flex justify-end pt-1">
                <button
                  type="button"
                  onClick={() => { setOpen(false); triggerRef.current?.focus() }}
                  className="rounded-md bg-brand-teal text-white text-xs font-medium px-3 py-1.5 hover:bg-brand-teal/90 transition-colors"
                >
                  Apply
                </button>
              </div>
            </div>
          </div>
        </Popover>
      </div>
    </div>
  )
}

// Re-export so callers can resolve presets without importing helpers directly.
export { resolvePreset, resolveDateRange }
