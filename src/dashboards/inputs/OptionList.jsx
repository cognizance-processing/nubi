/**
 * OptionList.jsx — windowed, accessible option list for the input primitives.
 *
 * Renders only the visible rows (VirtualList) so 10k+ option sets stay smooth.
 * Each row can render as a plain row, a radio (single-select) or a checkbox
 * (multi-select). Supports search-match highlighting, an active (keyboard)
 * cursor, and loading / empty / "type to search" states.
 *
 * Options are passed pre-normalised to { v, l } (value, label). See
 * normOption in index.js.
 */

import { useEffect, useRef, useState } from 'react'

export const ROW_HEIGHT = 32        // px per option row
const LIST_MAX_HEIGHT = 240         // px viewport for the dropdown list
const OVERSCAN = 4                  // extra rows above/below the viewport

// ---------------------------------------------------------------------------
// VirtualList — windowed list (no deps). Exported so other primitives can use it.
// ---------------------------------------------------------------------------

export function VirtualList({ items, renderRow, activeIndex, maxHeight = LIST_MAX_HEIGHT }) {
  const [scrollTop, setScrollTop] = useState(0)
  const containerRef = useRef(null)
  const total = items.length
  const viewportH = Math.min(maxHeight, Math.max(ROW_HEIGHT, total * ROW_HEIGHT))

  const startIndex = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
  const visibleCount = Math.ceil(viewportH / ROW_HEIGHT) + OVERSCAN * 2
  const endIndex = Math.min(total, startIndex + visibleCount)
  const padTop = startIndex * ROW_HEIGHT
  const padBottom = (total - endIndex) * ROW_HEIGHT

  // Keep the keyboard cursor in view.
  useEffect(() => {
    if (activeIndex == null || activeIndex < 0) return
    const el = containerRef.current
    if (!el) return
    const top = activeIndex * ROW_HEIGHT
    const bottom = top + ROW_HEIGHT
    if (top < el.scrollTop) el.scrollTop = top
    else if (bottom > el.scrollTop + viewportH) el.scrollTop = bottom - viewportH
  }, [activeIndex, viewportH])

  return (
    <div
      ref={containerRef}
      className="overflow-y-auto overscroll-contain"
      style={{ height: viewportH }}
      onScroll={e => setScrollTop(e.currentTarget.scrollTop)}
    >
      <div style={{ height: padTop }} />
      {items.slice(startIndex, endIndex).map((item, i) => renderRow(item, startIndex + i))}
      <div style={{ height: padBottom }} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Search-match highlight
// ---------------------------------------------------------------------------

function Highlight({ text, query }) {
  const q = query?.trim().toLowerCase()
  if (!q) return text
  const lower = text.toLowerCase()
  const i = lower.indexOf(q)
  if (i === -1) return text
  return (
    <>
      {text.slice(0, i)}
      <mark className="bg-brand-teal/20 text-brand-teal rounded-sm px-0.5">
        {text.slice(i, i + q.length)}
      </mark>
      {text.slice(i + q.length)}
    </>
  )
}

// ---------------------------------------------------------------------------
// Row markers
// ---------------------------------------------------------------------------

function CheckBox({ checked }) {
  return (
    <span
      aria-hidden="true"
      className={[
        'inline-flex items-center justify-center w-4 h-4 rounded border shrink-0 text-[10px] transition-colors',
        checked ? 'bg-brand-teal border-brand-teal text-white' : 'border-border',
      ].join(' ')}
    >
      {checked ? '✓' : ''}
    </span>
  )
}

function Radio({ checked }) {
  return (
    <span
      aria-hidden="true"
      className={[
        'inline-flex items-center justify-center w-4 h-4 rounded-full border shrink-0 transition-colors',
        checked ? 'border-brand-teal' : 'border-border',
      ].join(' ')}
    >
      {checked ? <span className="w-2 h-2 rounded-full bg-brand-teal" /> : null}
    </span>
  )
}

// ---------------------------------------------------------------------------
// OptionList
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   options: Array<{v: string, l: string, icon?: string}>,
 *   selected: Set<string>,
 *   mode: 'plain' | 'radio' | 'checkbox',
 *   onSelect: (v: string) => void,
 *   activeIndex?: number,
 *   onActiveChange?: (i: number) => void,
 *   query?: string,
 *   loading?: boolean,
 *   emptyText?: string,
 *   typeToSearch?: boolean,
 *   listboxId?: string,
 *   optionIdPrefix?: string,
 * }} props
 */
export default function OptionList({
  options,
  selected,
  mode = 'plain',
  onSelect,
  activeIndex = -1,
  onActiveChange,
  query = '',
  loading = false,
  emptyText = 'No matches',
  typeToSearch = false,
  listboxId,
  optionIdPrefix,
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 px-3 py-3 text-xs text-muted" role="status">
        <span className="inline-block w-3 h-3 rounded-full border-2 border-border border-t-brand-teal animate-spin" />
        Loading…
      </div>
    )
  }

  if (typeToSearch && options.length === 0 && !query) {
    return <div className="px-3 py-3 text-xs text-muted italic">Type to search…</div>
  }

  if (options.length === 0) {
    return <div className="px-3 py-3 text-xs text-muted italic">{emptyText}</div>
  }

  return (
    <div role="listbox" id={listboxId} aria-multiselectable={mode === 'checkbox' || undefined}>
      <VirtualList
        items={options}
        activeIndex={activeIndex}
        renderRow={(o, idx) => {
          const isSelected = selected.has(o.v)
          const isActive = idx === activeIndex
          return (
            <div
              key={o.v || '__empty__'}
              id={optionIdPrefix ? `${optionIdPrefix}-${idx}` : undefined}
              role="option"
              aria-selected={isSelected}
              tabIndex={-1}
              onMouseEnter={() => onActiveChange?.(idx)}
              onClick={() => onSelect(o.v)}
              style={{ height: ROW_HEIGHT }}
              className={[
                'w-full flex items-center gap-2 text-left px-3 text-sm cursor-pointer transition-colors',
                isActive ? 'bg-surface-2' : 'hover:bg-surface-2/70',
                isSelected && mode === 'plain'
                  ? 'text-brand-teal font-medium'
                  : 'text-fg',
              ].join(' ')}
            >
              {mode === 'checkbox' && <CheckBox checked={isSelected} />}
              {mode === 'radio' && <Radio checked={isSelected} />}
              {o.icon && <span aria-hidden="true" className="shrink-0">{o.icon}</span>}
              <span className="truncate flex-1">
                <Highlight text={o.l} query={query} />
              </span>
              {mode === 'plain' && isSelected && (
                <span aria-hidden="true" className="text-brand-teal text-xs shrink-0">✓</span>
              )}
            </div>
          )
        }}
      />
    </div>
  )
}
