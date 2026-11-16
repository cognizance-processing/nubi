/**
 * shared/inspectorPrimitives.jsx — Low-level UI building blocks for widget
 * inspector panels. Shared editor primitives so the
 * editors render identical inspector chrome without diverging.
 *
 * Exports:
 *   inputCls        string  — Tailwind class string for <input> controls
 *   selectCls       string  — Tailwind class string for <select> controls
 *   FieldLabel      React component
 *   SectionLabel    React component
 *   Section         React component  (collapsible <details>)
 *   ToggleRow       React component  (label + toggle switch)
 *   ChipRow         React component  (preset chip row)
 *   ColumnSelect    React component  (column-picker <select> with label)
 *   ColorSwatch     React component  (a lone <input type="color">)
 *   ColorField      React component  (swatch + free-text colour input)
 */

import { useEffect, useMemo, useRef, useState, type ReactNode, type ComponentType } from 'react'
import { resolveSwatchHex } from './colorValue.js'
import { useLiveEdit } from '../liveEdit.jsx'
import '../../dashboards/widgets/chrome.css'

/**
 * Upstream commit interval while a colour is being dragged, in ms.
 *
 * The browser fires an `input` event on every pointer move inside the colour
 * spectrum (~60/s). Each one rewrites the board spec and re-renders the canvas,
 * so committing them all is what made the picker feel laggy. ~12 board updates
 * a second still reads as live feedback while costing a fifth of the work — and
 * the swatch itself keeps redrawing at full rate from local state.
 */
const LIVE_COMMIT_MS = 80

/** Quiet period after the last pointer move that closes the drag gesture. */
const GESTURE_SETTLE_MS = 350

// Shared control styling. Targets a consistent ~32px control height across
// inputs and selects, with calm focus rings and the app's design tokens.
// Uses nubi-input / nubi-select CSS classes from the design system for parity.
export const inputCls = 'nubi-input nubi-input-sm'

// Native <select> with the design system's square chevron (the .nubi-select
// background image), so it matches the inputs exactly in both themes.
export const selectCls = 'nubi-input nubi-input-sm nubi-select'

/** A field label used above inputs/selects. Consistent weight + spacing. */
export function FieldLabel({ children, className = '' }) {
  return (
    <label className={`block text-[12px] font-medium text-muted mb-1 leading-tight ${className}`}>
      {children}
    </label>
  )
}

/** A small all-caps section header. */
export function SectionLabel({ children }) {
  return (
    <p className="nbc-label m-0">
      {children}
    </p>
  )
}

/**
 * A collapsible <details> section with a consistent header.
 */
export function Section({ title, icon: Icon, children, defaultOpen = true, right = null }: {
  title: ReactNode
  icon?: ComponentType<{ size?: number; strokeWidth?: number; className?: string }>
  children: ReactNode
  defaultOpen?: boolean
  right?: ReactNode
}) {
  return (
    <details open={defaultOpen} className="nbc-section group">
      <summary>
        <span className="flex min-w-0 items-center gap-2">
          {Icon && <Icon size={14} strokeWidth={1.5} className="shrink-0 text-muted" />}
          <span className="truncate">{title}</span>
        </span>
        <span className="flex items-center gap-2">
          {right}
          <svg className="nbc-chev h-3 w-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d="M4.5 3l3 3-3 3" strokeLinecap="square" strokeLinejoin="miter" />
          </svg>
        </span>
      </summary>
      <div className="nbc-section-body">
        {children}
      </div>
    </details>
  )
}

/** A labelled toggle switch row. */
export function ToggleRow({ label, checked, onChange, hint }: {
  label: ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  hint?: ReactNode
}) {
  return (
    <label className="flex items-center justify-between gap-3 cursor-pointer py-0.5">
      <span className="text-[13px] font-medium text-fg leading-snug">
        {label}
        {hint && <span className="block text-[12px] text-muted font-normal mt-0.5 leading-snug">{hint}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className="nbc-switch"
      />
    </label>
  )
}

/**
 * A compact preset-chip row (e.g. column-count picker, row-height picker).
 * Props: options (array), value (current), onChange(option), suffix (string appended to each chip label)
 */
export function ChipRow({ options, value, onChange, suffix = '' }) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map(opt => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          aria-pressed={value === opt}
          className="nbc-chip"
        >
          {opt}{suffix}
        </button>
      ))}
    </div>
  )
}

/**
 * A colour swatch — a lone `<input type="color">` with consistent chrome.
 *
 * The swatch is a VIEW of the stored value, never the source of truth: `value`
 * is resolved for display only (see resolveSwatchHex), so a field holding a
 * named colour still shows that colour, while one holding 'inherit' or
 * 'var(--fg)' shows `fallback` instead of silently painting the chip black.
 *
 * Props: value, onChange(hex), fallback, title, size ('sm' | 'md')
 */
export function ColorSwatch({ value, onChange, fallback = '#6366f1', title, size = 'md' }) {
  const dims = size === 'sm' ? 'h-6 w-6 rounded-sm' : 'h-8 w-10 rounded-sm'
  const hex = useMemo(() => resolveSwatchHex(value, fallback), [value, fallback])

  // `draft` is the shade under the pointer RIGHT NOW; it is what the chip shows
  // while a drag is in flight, so the swatch tracks the spectrum at full frame
  // rate no matter how rarely we commit upstream. null = not dragging, show the
  // stored value.
  const [draft, setDraft] = useState(null)
  const live = useLiveEdit()

  const pending = useRef(null)      // newest value not yet sent upstream
  const throttle = useRef(null)     // open => a commit is already scheduled
  const settle = useRef(null)       // fires once the pointer stops moving
  const gesture = useRef(false)     // is a live-edit gesture currently open
  // Synced in an effect rather than during render: handlers only ever run after
  // the commit, so they always observe the latest value.
  const onChangeRef = useRef(onChange)
  const liveRef = useRef(live)
  useEffect(() => { onChangeRef.current = onChange }, [onChange])
  useEffect(() => { liveRef.current = live }, [live])

  // Every timer must die with the component, or a commit lands on an unmounted
  // widget after the user has selected a different one.
  useEffect(() => () => {
    if (throttle.current) clearTimeout(throttle.current)
    if (settle.current) clearTimeout(settle.current)
    if (gesture.current) liveRef.current?.end()
  }, [])

  const flush = () => {
    if (pending.current == null) return
    const next = pending.current
    pending.current = null
    onChangeRef.current(next)
  }

  const endGesture = () => {
    if (throttle.current) { clearTimeout(throttle.current); throttle.current = null }
    if (settle.current) { clearTimeout(settle.current); settle.current = null }
    flush()
    setDraft(null)
    if (gesture.current) { gesture.current = false; liveRef.current?.end() }
  }

  const handleInput = (e) => {
    const next = e.target.value
    setDraft(next)
    pending.current = next

    // First value of a drag: commit immediately, so the board responds at once,
    // and open the gesture so every LATER commit in this drag collapses into the
    // same undo step. (The provider makes the first commit inside a gesture a
    // real history entry regardless of ordering, so this is safe either way.)
    if (!gesture.current) {
      gesture.current = true
      liveRef.current?.begin()
      flush()
    } else if (!throttle.current) {
      // Mid-drag: at most one board update per LIVE_COMMIT_MS. The input event
      // fires on every pointer move; re-rendering the whole canvas that often is
      // what made dragging in the spectrum feel laggy.
      throttle.current = setTimeout(() => { throttle.current = null; flush() }, LIVE_COMMIT_MS)
    }

    // A drag has no reliable end event across browsers (Chrome and Firefox
    // disagree about when `change` fires for a colour input), so the gesture is
    // closed by the pointer going quiet instead.
    if (settle.current) clearTimeout(settle.current)
    settle.current = setTimeout(endGesture, GESTURE_SETTLE_MS)
  }

  return (
    <input
      type="color"
      title={title}
      value={draft ?? hex}
      onChange={handleInput}
      onBlur={endGesture}
      className={`${dims} shrink-0 border border-frame-line bg-surface cursor-pointer p-0.5 transition-colors hover:border-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`}
    />
  )
}

/**
 * A colour field — swatch + free-text input, the inspector's most-repeated
 * control. The text side accepts any CSS colour (or blank to inherit); the
 * swatch is a convenience picker over it.
 *
 * `onChange` receives the raw string. When `clearable` (the default) an empty
 * text box reports `undefined`, which is how callers express "inherit".
 *
 * Props: value, onChange(str|undefined), placeholder, fallback, clearable
 */
export function ColorField({ value, onChange, placeholder, fallback = '#6366f1', clearable = true }) {
  const live = useLiveEdit()
  const typing = useRef(false)
  // Typing a colour emits one commit per keystroke for what is one decision.
  // Bracketing the whole edit in a gesture collapses it to a single undo step,
  // exactly as a spectrum drag is collapsed.
  useEffect(() => () => { if (typing.current) live?.end() }, [live])
  return (
    <div className="flex items-center gap-2">
      <ColorSwatch value={value} onChange={onChange} fallback={fallback} title="Pick a colour" />
      <input
        type="text"
        className={`${inputCls} flex-1`}
        placeholder={placeholder}
        value={value ?? ''}
        onChange={e => onChange(clearable ? (e.target.value || undefined) : e.target.value)}
        onFocus={() => { if (!typing.current) { typing.current = true; live?.begin() } }}
        onBlur={() => { if (typing.current) { typing.current = false; live?.end() } }}
      />
    </div>
  )
}

/**
 * A labelled column <select> for data-encoding fields.
 * Props: label, value, onChange(col), columns (string[]), optional (adds "— none —" option)
 */
export function ColumnSelect({ label, value, onChange, columns, optional = false }) {
  return (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <select className={selectCls} value={value || ''} onChange={e => onChange(e.target.value)}>
        {optional && <option value="">— none —</option>}
        {!optional && !value && <option value="">Select column…</option>}
        {columns.map(col => <option key={col} value={col}>{col}</option>)}
      </select>
    </div>
  )
}
