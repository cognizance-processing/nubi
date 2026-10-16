/**
 * SliderField — a labelled range input for the marketing calculators.
 * Ink track, square accent thumb, a mono tabular readout.
 */

export function SliderField({
  id, label, display, min, max, step = undefined, value, onChange, lo, hi, ariaLabel,
}: {
  id: string
  label: string
  display: string | number
  min: string
  max: string
  step?: string
  value: number
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  lo: string
  hi: string
  ariaLabel?: string
}) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, marginBottom: 8 }}>
        <label htmlFor={id} className="mk-flabel">{label}</label>
        <output htmlFor={id} className="mk-mono" style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{display}</output>
      </div>
      <input
        id={id} type="range" min={min} max={max} step={step} value={value}
        onChange={onChange}
        className="mk-range"
        aria-label={ariaLabel || label}
        aria-valuetext={String(display)}
      />
      <div className="mk-mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
        <span>{lo}</span><span>{hi}</span>
      </div>
    </div>
  )
}
