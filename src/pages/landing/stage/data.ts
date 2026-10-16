/**
 * The landing's sample data (storyboard §7.1): neutral retail figures for "Acme".
 * Single source for every scene. Totals, KPI strings and splits are DERIVED here,
 * never hand-typed in a scene; data.test.ts checks the displayed strings.
 */

export const DAYS = 14
/** Revenue per day, $k. Σ 5,045 → "5.05M". */
export const REV_K = [318, 342, 355, 301, 366, 402, 415, 331, 348, 362, 296, 371, 410, 428]
/** Orders per day, thousands. Σ 79.5 → "79.5k". */
export const ORD_K = [4.9, 5.3, 5.6, 4.7, 5.8, 6.4, 6.6, 5.2, 5.5, 5.7, 4.6, 5.9, 6.5, 6.8]
/** Mean fulfilment time per day, seconds. Mean 224 → "3:44". */
export const FULFIL_S = [238, 231, 227, 235, 224, 219, 216, 229, 225, 221, 232, 220, 213, 209]
export const REGIONS = ['AMER', 'EMEA', 'APAC', 'LATAM'] as const
export type Region = typeof REGIONS[number]
export const REGION_SHARE = [0.40, 0.29, 0.21, 0.10]
export const FIELDS = ['store_id', 'region', 'day', 'hour', 'orders', 'items', 'revenue', 'margin']
export const FIELD_SHORT = ['ID', 'REG', 'DAY', 'HR', 'ORD', 'ITM', 'REV', 'MGN']
/** Bytes per value of each field; revenue = 8 / 28 = 28.6 %. */
export const FIELD_BYTES = [4, 1, 2, 1, 4, 4, 8, 4]
export const ROWS_TOTAL = 1_048_576
export const POLICY_ROWS = 2048
export const CACHE_KEY = 'sha256:9f2c…e41a'
export const VIEWERS_START = 1800
export const VIEWERS_STEP = 100
export const AGENT = {
  org: 'acme-retail', board: 'emea-review', revenueQ3: 4.12, marginPct: 31.8,
  byCountry: [['Germany', 1.21], ['United Kingdom', 0.98], ['France', 0.74], ['Spain', 0.52], ['Italy', 0.41], ['Netherlands', 0.26]] as [string, number][],
}
export const BOARD_URL = 'app.acme.example/d/store-performance'
export const BOARD_TITLE = 'Store performance'

/* ───────────── derived values ───────────── */

const sum = (a: number[]) => a.reduce((s, v) => s + v, 0)
const r1 = (v: number) => Math.round(v * 10) / 10

export const REV_TOTAL_K = sum(REV_K)                           // 5045
export const ORD_TOTAL_K = r1(sum(ORD_K))                       // 79.5
export const AVG_TICKET = REV_TOTAL_K / ORD_TOTAL_K             // 63.459…
export const FULFIL_MEAN_S = Math.round(sum(FULFIL_S) / FULFIL_S.length) // 224
export const REV_MAX_K = Math.max(...REV_K)
export const ORD_MAX_K = Math.max(...ORD_K)
/** 14-day revenue by region, $k (rounded). */
export const REGION_REV_K = REGION_SHARE.map(sh => Math.round(REV_TOTAL_K * sh))
export const FIELD_BYTES_TOTAL = sum(FIELD_BYTES)
export const POLICY_ROWS_PER_REGION = POLICY_ROWS / REGIONS.length

/* ───────────── formatting (one implementation for every string the page shows) ───────────── */

/** $k → "5.05M" (≥ 1,000k) or "362k". */
export function fmtMoneyK(k: number): string {
  if (k >= 1000) return `${(Math.round(k / 10) / 100).toFixed(2)}M` // round half up on $10k, not on the float
  return `${Math.round(k)}k`
}
/** thousands → "79.5k" / "5.7k". */
export const fmtThousands = (k: number) => `${r1(k).toFixed(1)}k`
/** seconds → "3:44". */
export const fmtMinSec = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
/** 63.459 → "63.46". */
export const fmtTicket = (v: number) => v.toFixed(2)
/** 5045 → "$5,045,000" (full dollars from $k). */
export const fmtUsdFull = (k: number) => '$' + Math.round(k * 1000).toLocaleString('en-US')
/** 1900 → "1,900". */
export const fmtInt = (n: number) => Math.round(n).toLocaleString('en-US')
/** .2857 → "28.6%". */
export const fmtPct = (f: number, digits = 1) => `${(f * 100).toFixed(digits)}%`

/* ───────────── KPI sets ───────────── */

export interface Kpi { id: 'revenue' | 'orders' | 'ticket' | 'fulfil'; value: string; caption: string; full: string }

/** The board's four KPIs, for all 14 days or for one day (index 0..13). */
export function kpis(day: number | null = null): Kpi[] {
  const rev = day == null ? REV_TOTAL_K : REV_K[day]
  const ord = day == null ? ORD_TOTAL_K : ORD_K[day]
  const ful = day == null ? FULFIL_MEAN_S : FULFIL_S[day]
  const span = day == null ? '14 days' : `day ${day + 1}`
  return [
    { id: 'revenue', value: fmtMoneyK(rev), caption: 'Revenue · USD', full: `Revenue · ${span}: ${fmtUsdFull(rev)}` },
    { id: 'orders', value: fmtThousands(ord), caption: 'Orders', full: `Orders · ${span}: ${fmtInt(ord * 1000)}` },
    { id: 'ticket', value: fmtTicket(rev / ord), caption: 'Avg ticket · USD', full: `Avg ticket · ${span}: $${fmtTicket(rev / ord)}` },
    { id: 'fulfil', value: fmtMinSec(ful), caption: 'Fulfilment', full: `Fulfilment · ${span}: ${fmtMinSec(ful)}` },
  ]
}

/** Revenue by region, $k, for all days or one day (rounded). */
export function regionRevK(day: number | null = null): number[] {
  const rev = day == null ? REV_TOTAL_K : REV_K[day]
  return REGION_SHARE.map(sh => Math.round(rev * sh))
}

/** Share of bytes read when a chart reads one field. */
export const fieldByteShare = (i: number) => FIELD_BYTES[i] / FIELD_BYTES_TOTAL

/** Viewer count after `adds` presses of "Add 100 viewers". */
export const viewers = (adds: number) => VIEWERS_START + adds * VIEWERS_STEP
