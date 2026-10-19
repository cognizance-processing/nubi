/**
 * numberInput.ts — turn an `<input type="number">` value into a spec number.
 *
 * The editor used `parseInt(v, 10) || undefined`, which treats a typed 0 as
 * "cleared": a y-axis max of 0 could never be saved. This keeps 0, clears on an
 * empty or unparseable input, and lets a field that genuinely needs a floor
 * (a count, a font size) say so explicitly via `min`.
 */
export interface NumberInputOptions {
  /** Parse as an integer (parseInt) rather than a float. */
  integer?: boolean
  /** Clamp the parsed value to at least this. */
  min?: number
  /** Clamp the parsed value to at most this. */
  max?: number
}

export function parseNumberInput(raw: unknown, opts: NumberInputOptions = {}): number | undefined {
  const s = raw == null ? '' : String(raw).trim()
  if (s === '') return undefined
  let n = opts.integer ? parseInt(s, 10) : parseFloat(s)
  if (!Number.isFinite(n)) return undefined
  if (opts.min != null && n < opts.min) n = opts.min
  if (opts.max != null && n > opts.max) n = opts.max
  return n
}
