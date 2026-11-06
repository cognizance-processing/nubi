/**
 * Retry a request that was rate limited. A board loads many tiles at once, so a brief
 * 429 should delay a tile rather than fail it. The last 429 is returned to the caller.
 */

const MAX_ATTEMPTS = 5
const DEFAULT_DELAY_MS = 1000
const MAX_DELAY_MS = 10_000

/** Milliseconds to wait from a Retry-After header in seconds, falling back to backoff. */
export function retryDelayMs(retryAfter: string | null | undefined, attempt: number): number {
  const seconds = Number(retryAfter)
  const base = retryAfter != null && retryAfter !== '' && Number.isFinite(seconds) && seconds >= 0
    ? seconds * 1000
    : DEFAULT_DELAY_MS * 2 ** attempt
  return Math.min(base, MAX_DELAY_MS)
}

export async function fetchRetryingRateLimit(
  send: () => Promise<Response>,
  sleep: (ms: number) => Promise<unknown> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  random: () => number = Math.random,
): Promise<Response> {
  let response = await send()
  for (let attempt = 0; response.status === 429 && attempt < MAX_ATTEMPTS - 1; attempt++) {
    // Jitter, so tiles rejected together do not all come back in the same instant.
    await sleep(retryDelayMs(response.headers.get('Retry-After'), attempt) + random() * 500)
    response = await send()
  }
  return response
}
