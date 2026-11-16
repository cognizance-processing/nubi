/**
 * Starting the in-browser query engine and handing results to it, without
 * ever failing silently.
 */

/** How long the engine may go without reporting any progress before it counts as stuck. */
export const ENGINE_STALL_MS = 60_000

/**
 * Start the in-browser engine, turning the two ways it can go wrong silently
 * into a rejection: a worker that errors, and a load that stops making
 * progress (a blocked or dropped module download never answers at all).
 * A slow download keeps reporting progress, so it is never mistaken for a stall.
 *
 * Kept free of browser-only imports so it can be tested directly; the app
 * reaches it through `initDuckDB` in wasmRuntime.
 */
export async function startEngine({ load, createWorker, stallMs }: {
  load: () => Promise<any>
  createWorker: (url: string) => any
  stallMs: number
}) {
  let worker = null
  let watchdog = null
  try {
    const { duckdb, bundles } = await load()
    const bundle = await duckdb.selectBundle(bundles)

    // Same-origin worker URL → classic Worker (the browser blocks constructing a
    // Worker from a cross-origin URL, which is why the CDN bundle failed).
    worker = createWorker(bundle.mainWorker)
    const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker)

    let lastProgress = Date.now()
    const failed = new Promise((_, reject) => {
      worker.addEventListener('error', (event) => {
        reject(new Error(event?.message || 'its worker script failed to load'))
      })
      watchdog = setInterval(() => {
        if (Date.now() - lastProgress >= stallMs) {
          reject(new Error(`no progress for ${Math.round(stallMs / 1000)} seconds`))
        }
      }, Math.min(1000, stallMs))
    })

    await Promise.race([
      db.instantiate(bundle.mainModule, bundle.pthreadWorker, () => { lastProgress = Date.now() }),
      failed,
    ])
    return db
  } catch (err) {
    try { worker?.terminate() } catch { /* already gone */ }
    throw new Error(
      `The in-browser query engine could not start (${err?.message ?? err}). ` +
      'Check your connection and try again.',
    )
  } finally {
    if (watchdog) clearInterval(watchdog)
  }
}

/**
 * A queue that hands results to the engine in the background, in order.
 * `share` never rejects and never makes its caller wait; `settled` resolves
 * once everything shared so far has been handed over (or has failed).
 */
export function createShareQueue(register: (name: string, table: any) => Promise<void>) {
  let pending: Promise<void> = Promise.resolve()
  return {
    share(names: Array<string | null | undefined>, table: any, onError?: (err: Error) => void) {
      const [primary, ...aliases] = names.filter(Boolean)
      if (!primary) return pending
      pending = pending.then(async () => {
        try {
          await register(primary, table)
        } catch (err) {
          onError?.(err)
          return
        }
        for (const alias of aliases) {
          await register(alias, table).catch(() => {})
        }
      })
      return pending
    },
    settled() {
      return pending
    },
  }
}
