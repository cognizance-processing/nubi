/**
 * SliderField — a labeled range input used by the marketing pricing calculators.
 *
 * Extracted to its own module so it survives independently of any single
 * calculator (it was previously exported from the removed LakehouseCalculator).
 */

export function SliderField({ id, label, display, min, max, step, value, onChange, lo, hi, ariaLabel }) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <label htmlFor={id} className="text-sm font-semibold text-fg">{label}</label>
        <span className="font-mono text-[13px] font-bold text-brand-teal tabular-nums bg-brand-teal/[0.08] border border-brand-teal/25 rounded-lg px-2.5 py-0.5">
          {display}
        </span>
      </div>
      <input
        id={id} type="range" min={min} max={max} step={step} value={value}
        onChange={onChange}
        className="lp-range w-full"
        aria-label={ariaLabel || label}
      />
      <div className="flex justify-between font-mono text-[10px] text-muted mt-1.5">
        <span>{lo}</span><span>{hi}</span>
      </div>
    </div>
  )
}
