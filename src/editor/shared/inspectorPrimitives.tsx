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
export const inputCls =
  'w-full h-8 text-sm border border-border rounded-lg px-2.5 bg-surface text-fg ' +
  'placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60 ' +
  'focus:border-ring/40 hover:border-border/80 transition-all duration-150'

// Native <select> with a custom chevron (appearance-none) so it matches the
// inputs exactly. The chevron is a STATIC, fully percent-encoded SVG data URL
// (quotes → %22, spaces → %20) so (a) Tailwind's static scanner picks up the
// arbitrary value and (b) the CSS minifier sees no raw quotes/parens in url().
export const selectCls =
  'w-full h-8 text-sm border border-border rounded-lg pl-2.5 pr-8 bg-surface text-fg appearance-none cursor-pointer ' +
  'focus:outline-none focus:ring-2 focus:ring-ring/60 focus:border-ring/40 hover:border-border/80 transition-all duration-150 ' +
  'bg-[length:14px] bg-[right_0.5rem_center] bg-no-repeat ' +
  'bg-[url(data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20viewBox=%220%200%2012%2012%22%20fill=%22none%22%20stroke=%22%238895a8%22%20stroke-width=%221.4%22%20stroke-linecap=%22round%22%20stroke-linejoin=%22round%22%3E%3Cpath%20d=%22M3%204.5%206%207.5%209%204.5%22/%3E%3C/svg%3E)]'

/** A field label used above inputs/selects. Consistent weight + spacing. */
export function FieldLabel({ children, className = '' }) {
  return (
    <label className={`block text-[11px] font-medium text-muted mb-1 leading-tight ${className}`}>
      {children}
    </label>
  )
}

/** A small all-caps section header. */
export function SectionLabel({ children }) {
  return (
    <p className="text-[10px] font-semibold text-muted/80 uppercase tracking-[0.08em] leading-none">
      {children}
    </p>
  )
}

/**
 * A collapsible <details> section with a consistent header.
 */
export function Section({ title, icon: Icon, children, defaultOpen = true, right = null }: {
  title: ReactNode
  icon?: ComponentType<{ size?: number; className?: string }>
  children: ReactNode
  defaultOpen?: boolean
  right?: ReactNode
}) {
  return (
    <details
      open={defaultOpen}
      className="group rounded-xl border border-border bg-surface-2/20 overflow-hidden transition-all"
    >
      <summary className="flex items-center justify-between gap-2 px-3 h-9 cursor-pointer select-none list-none hover:bg-surface-2/60 transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
        <span className="flex items-center gap-2 text-xs font-semibold text-fg">
          {Icon && <Icon size={13} className="text-primary/70 shrink-0" />}
          {title}
        </span>
        <span className="flex items-center gap-2">
          {right}
          <svg
            className="w-3 h-3 text-muted/50 transition-transform duration-200 group-open:rotate-90"
            viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6"
          >
            <path d="M4.5 3l3 3-3 3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </summary>
      <div className="px-3 pb-3 pt-2.5 space-y-3 border-t border-border/50">
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
    <label className="flex items-center justify-between gap-3 cursor-pointer py-0.5 group/toggle">
      <span className="text-xs font-medium text-fg leading-snug">
        {label}
        {hint && <span className="block text-[10px] text-muted/70 font-normal mt-0.5 leading-relaxed">{hint}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={[
          'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-all duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-1 focus-visible:ring-offset-surface',
          checked ? 'bg-primary shadow-sm' : 'bg-border hover:bg-border/80',
        ].join(' ')}
      >
        <span
          className={[
            'inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transform transition-transform duration-200',
            checked ? 'translate-x-[1.125rem]' : 'translate-x-0.5',
          ].join(' ')}
        />
      </button>
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
          className={[
            'px-2.5 py-1 text-xs font-medium rounded-lg border transition-all duration-150',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
            value === opt
              ? 'bg-primary text-primary-fg border-primary shadow-sm'
              : 'bg-surface text-muted border-border hover:border-primary/60 hover:text-fg hover:bg-surface-2',
          ].join(' ')}
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
  const dims = size === 'sm' ? 'h-6 w-6 rounded' : 'h-8 w-10 rounded-lg'
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
      className={`${dims} shrink-0 border border-border bg-surface cursor-pointer transition-colors hover:border-border/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60`}
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
