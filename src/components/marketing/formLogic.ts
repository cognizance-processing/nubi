/**
 * Pure form logic for the public auth pages: validators and the password meter.
 * Kept free of React so it can be unit-tested.
 */

export type Validator = (value: string) => string | null

export const validators = {
  required: (label: string): Validator => v => (v.trim() ? null : `Enter ${label}.`),
  email: (v: string) => (!v.trim() ? 'Enter your email address.' : /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v.trim()) ? null : 'Enter a valid email address, like name@company.com.'),
  minLength: (n: number, label = 'This field'): Validator => v => (v.length >= n ? null : `${label} needs at least ${n} characters.`),
}

/** Run validators in order and return the first message. */
export const check = (value: string, ...vs: Validator[]) => {
  for (const v of vs) { const m = v(value); if (m) return m }
  return null
}

export function passwordScore(pw: string) {
  if (!pw) return 0
  let s = 0
  if (pw.length >= 8) s++
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++
  if (/[0-9]/.test(pw)) s++
  if (/[^A-Za-z0-9]/.test(pw) || pw.length >= 14) s++
  return s
}
