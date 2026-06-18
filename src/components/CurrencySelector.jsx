/**
 * CurrencySelector — topbar display-currency picker with flags.
 *
 * Shows the active currency's flag + code; opens a dropdown of supported
 * currencies. Selection updates the shared CurrencyContext (prices across the
 * marketing pages re-render in the chosen currency). Billing is always ZAR —
 * a footnote in the dropdown makes that explicit.
 *
 * Styled to match the other topbar controls (h-11, rounded-lg, bordered).
 */

import { useState, useEffect, useRef } from 'react'
import { ChevronDown, Check } from 'lucide-react'
import { useCurrency } from '../contexts/CurrencyContext.jsx'
import { CURRENCY_ORDER, BILLING_CURRENCY } from '../lib/currency.js'

export default function CurrencySelector() {
  const { currency, setCurrency, currencies } = useCurrency()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  const active = currencies[currency] ?? currencies.USD

  useEffect(() => {
    if (!open) return
    function onDown(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={`Display currency: ${active.name}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={`Prices shown in ${active.name} — billed in ZAR`}
        className="
          flex items-center gap-1.5 h-11 px-2.5 rounded-lg
          text-sm font-medium text-fg
          bg-surface-2 hover:bg-surface
          border border-border
          transition-colors duration-150
          focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1
        "
      >
        <span className="text-base leading-none" aria-hidden="true">{active.flag}</span>
        <span className="font-mono text-xs tracking-wide">{active.code}</span>
        <ChevronDown size={12} className="text-muted" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            role="listbox"
            aria-label="Select display currency"
            className="
              absolute right-0 top-full mt-1 z-40
              w-60 p-1 rounded-xl
              bg-surface border border-border shadow-lg
            "
          >
            <div className="max-h-72 overflow-y-auto">
              {CURRENCY_ORDER.map((code) => {
                const c = currencies[code]
                if (!c) return null
                const selected = code === currency
                return (
                  <button
                    key={code}
                    role="option"
                    aria-selected={selected}
                    onClick={() => { setCurrency(code); setOpen(false) }}
                    className={`
                      flex items-center gap-2.5 w-full px-2.5 py-2 rounded-lg text-sm text-left
                      transition-colors min-h-[40px]
                      ${selected ? 'bg-surface-2 text-fg' : 'text-fg hover:bg-surface-2'}
                    `}
                  >
                    <span className="text-base leading-none shrink-0" aria-hidden="true">{c.flag}</span>
                    <span className="font-mono text-xs w-9 shrink-0 text-muted">{c.code}</span>
                    <span className="flex-1 truncate text-[13px]">{c.name}</span>
                    {selected && <Check size={14} className="text-brand-teal shrink-0" />}
                  </button>
                )
              })}
            </div>
            <p className="px-2.5 py-2 mt-0.5 border-t border-border text-[11px] leading-snug text-muted">
              Display only — all plans are <span className="font-semibold text-fg">billed in {BILLING_CURRENCY}</span> (South African Rand).
            </p>
          </div>
        </>
      )}
    </div>
  )
}
