/**
 * nubi-context.js — Shared client/context for Nubi React web components.
 *
 * createNubiContext(options) wraps createNubiClient from the SDK and returns:
 *   - A per-baseUrl singleton NubiClient (shared across co-mounted elements).
 *   - A cross-filter bus (subscribe/publish/getFilters).
 *   - A React Context + Provider + hook for consumption inside React trees
 *     mounted inside web component shadow roots.
 *
 * The client singleton is keyed by baseUrl so that multiple <nubi-*> elements
 * on the same page (pointing at the same backend) share a single client
 * instance and thus a single token-resolution pipeline.
 *
 * Cross-filter bus
 * ----------------
 * Widgets publish filter selections (column → values[]) via publish().
 * Other widgets subscribe to receive them. The bus is intentionally
 * synchronous and in-memory — it is NOT persisted or debounced here.
 * The host page can bridge to URL state or a server via the nubi:cross-filter
 * DOM event (see events.js).
 *
 * Usage (inside a custom element)
 * --------------------------------
 *   import { createNubiContext } from '../nubi-context.js'
 *
 *   const { NubiProvider, useNubi, crossFilterBus, client } =
 *     createNubiContext({ baseUrl, getToken })
 *
 *   // Mount React tree:
 *   ReactDOM.createRoot(mountPoint).render(
 *     <NubiProvider client={client} crossFilterBus={crossFilterBus}>
 *       <MyWidget />
 *     </NubiProvider>
 *   )
 *
 *   // Inside MyWidget:
 *   const { client, crossFilterBus } = useNubi()
 */

import { createContext, useContext, useState, useEffect } from 'react'
import { createElement } from 'react'
import { createNubiClient } from '../sdk/src/index.js'

// ---------------------------------------------------------------------------
// Per-baseUrl client singleton map
// ---------------------------------------------------------------------------

/** @type {Map<string, ReturnType<typeof createNubiClient>>} */
const _clientRegistry = new Map()

/**
 * Return a shared NubiClient for the given baseUrl+getToken pair.
 * If two components share the same baseUrl, they share one client.
 *
 * @param {string} baseUrl
 * @param {string | (() => Promise<string> | string)} getToken
 * @returns {ReturnType<typeof createNubiClient>}
 */
function getOrCreateClient(baseUrl, getToken) {
  // Key on baseUrl only — callers with the same backend share one client.
  // getToken is set from the first registration and not updated on subsequent
  // calls (the token function itself can be replaced by the host at any time
  // because we hold a reference, not a snapshot).
  const key = baseUrl.replace(/\/+$/, '').replace(/\/api\/v1$/, '')
  if (!_clientRegistry.has(key)) {
    _clientRegistry.set(key, createNubiClient({ baseUrl: key, getToken }))
  }
  return _clientRegistry.get(key)
}

// ---------------------------------------------------------------------------
// Cross-filter bus factory
// ---------------------------------------------------------------------------

/**
 * Create a lightweight in-memory pub/sub cross-filter bus.
 *
 * @returns {{ subscribe, publish, getFilters }}
 */
function createCrossFilterBus() {
  /** @type {Map<string, any[]>} */
  const _filters = new Map()

  /** @type {Set<(filters: Map<string,any[]>) => void>} */
  const _listeners = new Set()

  return {
    /**
     * Subscribe to filter changes.
     * @param {(filters: Map<string, any[]>) => void} cb
     * @returns {() => void} unsubscribe function
     */
    subscribe(cb) {
      _listeners.add(cb)
      return () => _listeners.delete(cb)
    },

    /**
     * Publish a filter update from a widget.
     * `values: []` means "clear this filter".
     *
     * @param {{ filterId: string, values: any[] }} filter
     */
    publish({ filterId, values }) {
      if (values.length === 0) {
        _filters.delete(filterId)
      } else {
        _filters.set(filterId, values)
      }
      const snapshot = new Map(_filters)
      _listeners.forEach(cb => cb(snapshot))
    },

    /**
     * Get the current filter state (snapshot).
     * @returns {Map<string, any[]>}
     */
    getFilters() {
      return new Map(_filters)
    },
  }
}

// ---------------------------------------------------------------------------
// NubiContext + NubiProvider + useNubi
// ---------------------------------------------------------------------------

/**
 * @typedef {object} NubiContextValue
 * @property {ReturnType<typeof createNubiClient> | null} client
 * @property {ReturnType<typeof createCrossFilterBus> | null} crossFilterBus
 */

/** @type {import('react').Context<NubiContextValue>} */
const NubiContext = createContext({ client: null, crossFilterBus: null })

/**
 * Provider for the Nubi React context. Wrap your component tree with this
 * inside the web component's shadow root React mount.
 *
 * @param {{ client: object, crossFilterBus: object, children: import('react').ReactNode }} props
 */
function NubiProvider({ client, crossFilterBus, children }) {
  return createElement(
    NubiContext.Provider,
    { value: { client, crossFilterBus } },
    children,
  )
}

/**
 * Hook: access the Nubi client and cross-filter bus from any component
 * mounted inside a NubiProvider.
 *
 * @returns {NubiContextValue}
 */
function useNubi() {
  return useContext(NubiContext)
}

/**
 * Hook: subscribe to cross-filter changes and return the current filter map.
 * Re-renders the consumer when filters change.
 *
 * @returns {Map<string, any[]>}
 */
function useCrossFilters() {
  const { crossFilterBus } = useNubi()
  const [filters, setFilters] = useState(() =>
    crossFilterBus ? crossFilterBus.getFilters() : new Map(),
  )

  useEffect(() => {
    if (!crossFilterBus) return
    const unsub = crossFilterBus.subscribe(setFilters)
    return unsub
  }, [crossFilterBus])

  return filters
}

// ---------------------------------------------------------------------------
// createNubiContext — main export
// ---------------------------------------------------------------------------

/**
 * Create a full Nubi context bundle for use inside a web component.
 *
 * @param {object} options
 * @param {string} options.baseUrl
 * @param {string | (() => Promise<string> | string)} options.getToken
 * @param {string} [options.instanceId]
 *   Optional override key for the client singleton. When two elements share
 *   the same instanceId they share a client even if their baseUrl differs.
 *   Defaults to baseUrl.
 * @returns {{ client, crossFilterBus, NubiContext, NubiProvider, useNubi, useCrossFilters }}
 */
export function createNubiContext({ baseUrl, getToken, instanceId } = {}) {
  const client = baseUrl && getToken
    ? getOrCreateClient(instanceId || baseUrl, getToken)
    : null

  const crossFilterBus = createCrossFilterBus()

  return {
    client,
    crossFilterBus,
    NubiContext,
    NubiProvider,
    useNubi,
    useCrossFilters,
  }
}

export { NubiContext, NubiProvider, useNubi, useCrossFilters }
