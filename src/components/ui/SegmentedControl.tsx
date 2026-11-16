/**
 * SegmentedControl — Nubi mutually-exclusive option selector primitive.
 *
 * Visually: a square hairline group; the active segment is an ink fill.
 * Semantically: a radiogroup (arrow keys move and select, roving tabindex).
 *
 * Usage:
 *   <SegmentedControl
 *     value={view}
 *     onChange={setView}
 *     options={[
 *       { value: 'day',   label: 'Day' },
 *       { value: 'week',  label: 'Week' },
 *       { value: 'month', label: 'Month' },
 *     ]}
 *   />
 *
 *   // With icons
 *   <SegmentedControl
 *     value={layout}
 *     onChange={setLayout}
 *     options={[
 *       { value: 'grid', label: <LayoutGrid size={14} />, ariaLabel: 'Grid' },
 *       { value: 'list', label: <List size={14} />,       ariaLabel: 'List' },
 *     ]}
 *     size="sm"
 *   />
 *
 * Props:
 *   value     string — currently selected value
 *   onChange  (value: string) => void
 *   options   Array<{ value, label, ariaLabel?, disabled? }>
 *   size      'sm' 28px | 'md' 32px (default) | 'lg' 36px
 *   className
 *
 * Keyboard: Arrow keys navigate, Enter/Space select.
 */

import { useRef } from 'react'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

export default function SegmentedControl({
  value,
  onChange,
  options = [],
  size = 'md',
  className,
}: {
  value?: any
  onChange?: (value: any) => void
  options?: { value: any; label: React.ReactNode; ariaLabel?: string; disabled?: boolean }[]
  size?: 'sm' | 'md' | 'lg'
  className?: string
}) {
  const containerRef = useRef(null)

  const handleKeyDown = (e, idx) => {
    const active = options.filter((o) => !o.disabled)
    const activeIdx = active.findIndex((o) => o.value === options[idx].value)
    let next = null
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      next = active[(activeIdx + 1) % active.length]
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      next = active[(activeIdx - 1 + active.length) % active.length]
    }
    if (next) {
      e.preventDefault()
      onChange?.(next.value)
      // Move focus to the newly selected button
      const btns = containerRef.current?.querySelectorAll('[role="radio"]')
      const newIdx = options.findIndex((o) => o.value === next.value)
      btns?.[newIdx]?.focus()
    }
  }

  return (
    <div
      ref={containerRef}
      role="radiogroup"
      className={cx('nubi-segmented', size === 'sm' && 'nubi-segmented-sm', size === 'lg' && 'nubi-segmented-lg', className)}
    >
      {options.map((opt, idx) => {
        const isActive = value === opt.value
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={isActive}
            aria-label={opt.ariaLabel}
            disabled={opt.disabled}
            tabIndex={isActive ? 0 : -1}
            onClick={() => !opt.disabled && onChange?.(opt.value)}
            onKeyDown={(e) => handleKeyDown(e, idx)}
            className={cx(
              'nubi-segment nubi-focus-inset',
              isActive && 'active',
              opt.disabled && 'opacity-40 pointer-events-none',
            )}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
