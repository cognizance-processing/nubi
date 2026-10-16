/**
 * CurrencyPicker — display-currency select for the pricing page.
 *
 * Display only: every plan is priced in USD. A native <select> keeps it
 * keyboard- and screen-reader-friendly and gives mobile its native picker.
 */
import { useCurrency } from '../../contexts/CurrencyContext.jsx'
import { CURRENCY_ORDER, BILLING_CURRENCY } from '../../lib/currency.js'

export default function CurrencyPicker({ id = 'mk-currency' }: { id?: string }) {
  const { currency, setCurrency, currencies } = useCurrency()
  return (
    <div className="mk-pick">
      <label htmlFor={id} className="mk-label">Show prices in</label>
      <select id={id} className="mk-select" value={currency} onChange={e => setCurrency(e.target.value)}>
        {CURRENCY_ORDER.map((code: string) => {
          // The billing-currency option is only offered to visitors whose locale already selected it.
          if (code === BILLING_CURRENCY && code !== currency) return null
          const c = currencies[code]
          return c ? <option key={code} value={code}>{c.code} · {c.name}</option> : null
        })}
      </select>
    </div>
  )
}
