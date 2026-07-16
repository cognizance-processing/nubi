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

import { useMemo } from 'react'
import { resolveSwatchHex } from './colorValue.js'

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
 * Props: title, icon (optional Lucide component), defaultOpen, right (optional node)
 */
export function Section({ title, icon: Icon, children, defaultOpen = true, right = null }) {
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

/**
 * A labelled toggle switch row.
 * Props: label, checked, onChange(bool), hint (optional sub-label)
 */
export function ToggleRow({ label, checked, onChange, hint }) {
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
  return (
    <input
      type="color"
      title={title}
      value={hex}
      onChange={e => onChange(e.target.value)}
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
  return (
    <div className="flex items-center gap-2">
      <ColorSwatch value={value} onChange={onChange} fallback={fallback} title="Pick a colour" />
      <input
        type="text"
        className={`${inputCls} flex-1`}
        placeholder={placeholder}
        value={value ?? ''}
        onChange={e => onChange(clearable ? (e.target.value || undefined) : e.target.value)}
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
