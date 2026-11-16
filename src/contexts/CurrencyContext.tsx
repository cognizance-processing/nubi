/**
 * CurrencyContext — display-currency selection + live USD→X rates.
 *
 * Nubi prices are anchored in USD and billed in ZAR. This context drives the
 * DISPLAY currency only (a visitor convenience): it picks an initial currency
 * from the browser locale, persists the user's choice, and exposes helpers to
 * render a USD price in the chosen currency plus the authoritative "billed in
 * ZAR" amount.
 *
 * State
 *   currency   — selected display currency code (default: detected, else USD)
 *   rates      — USD→X map (indicative fallback, refreshed live on mount)
 *
 * Helpers
 *   setCurrency(code)
 *   format(usd)     → price string in the selected currency
 *   billedZar(usd)  → the actual ZAR charge (ceil-to-R10, 2% buffer) — matches the invoice
 *   isBilling       → true when the selected currency IS ZAR (no extra note needed)
 */

import { createContext, useContext, useEffect, useMemo, useState, useCallback, type ReactNode } from 'react'
import {
  CURRENCIES, DEFAULT_CURRENCY, BILLING_CURRENCY, CURRENCY_STORAGE_KEY,
  FALLBACK_RATES, detectCurrency, formatMoney,
} from '../lib/currency.js'
import { computeZar, formatZar } from '../lib/pricing.js'

export interface CurrencyContextValue {
  currency: string
  setCurrency: (code: string) => void
  currencies: Record<string, any>
  rates: Record<string, number>
  isBilling: boolean
  format: (usd: number) => string
  billedZar: (usd: number) => string
}

const CurrencyContext = createContext<CurrencyContextValue | null>(null)

// Keyless FX provider that covers ALL our display currencies (incl. NGN/KES/AED,
// which the ECB-based providers omit). Same source the backend falls back to.
const FX_URL = 'https://open.er-api.com/v6/latest/USD'

function getInitialCurrency(): string {
  try {
    const stored = localStorage.getItem(CURRENCY_STORAGE_KEY)
    if (stored && CURRENCIES[stored]) return stored
  } catch {
    // localStorage blocked — fall through to detection
  }
  return detectCurrency() || DEFAULT_CURRENCY
}

export function CurrencyProvider({ children }: { children: ReactNode }) {
  const [currency, setCurrencyState] = useState(getInitialCurrency)
  const [rates, setRates] = useState<Record<string, number>>(FALLBACK_RATES)

  // Refresh live USD→X rates once on mount (display-only; failure keeps the
  // indicative fallback so the UI never blocks or shifts).
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await fetch(FX_URL)
        if (!res.ok) return
        const data = await res.json()
        if (cancelled || data?.result !== 'success' || !data?.rates) return
        const next = { ...FALLBACK_RATES }
        for (const code of Object.keys(CURRENCIES)) {
          const r = data.rates[code]
          if (typeof r === 'number' && r > 0) next[code] = r
        }
        next.USD = 1
        setRates(next)
      } catch {
        // Network blocked — keep fallback rates.
      }
    })()
    return () => { cancelled = true }
  }, [])

  const setCurrency = useCallback((code: string) => {
    if (!CURRENCIES[code]) return
    setCurrencyState(code)
    try {
      localStorage.setItem(CURRENCY_STORAGE_KEY, code)
    } catch {
      // Ignore storage errors
    }
  }, [])

  const value = useMemo(() => {
    const zarRate = rates[BILLING_CURRENCY] ?? FALLBACK_RATES[BILLING_CURRENCY]
    return {
      currency,
      setCurrency,
      currencies: CURRENCIES,
      rates,
      isBilling: currency === BILLING_CURRENCY,
      /** Format a USD anchor amount in the selected display currency. */
      format: (usd: number) => formatMoney(usd, currency, rates),
      /** The actual ZAR charge for a USD anchor (matches the invoice math). */
      billedZar: (usd: number) => formatZar(computeZar(usd, zarRate)),
    }
  }, [currency, rates, setCurrency])

  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>
}

/**
 * Access the display-currency context. Returns a safe no-op default when used
 * outside a provider so core components (used in OSS contexts/tests) never crash.
 */
export function useCurrency(): CurrencyContextValue {
  const ctx = useContext(CurrencyContext)
  if (ctx) return ctx
  return {
    currency: DEFAULT_CURRENCY,
    setCurrency: () => {},
    currencies: CURRENCIES,
    rates: FALLBACK_RATES,
    isBilling: false,
    format: (usd: number) => formatMoney(usd, DEFAULT_CURRENCY, FALLBACK_RATES),
    billedZar: (usd: number) => formatZar(computeZar(usd, FALLBACK_RATES[BILLING_CURRENCY])),
  }
}
