/**
 * react-wc.js — React-to-Custom-Element wrapper factory.
 *
 * `defineNubiElement(tagName, ReactComponent, options)` creates and registers
 * a custom element that mounts a React component into its shadow DOM. This is
 * the thin infrastructure layer that makes the Nubi SPA's React widgets
 * consumable as framework-agnostic web components ("the app is its own SDK").
 *
 * Security
 * --------
 * Any HTML surface (e.g. slot content) is sanitized through DOMPurify before
 * being written to the DOM. The shadow root itself never receives raw HTML.
 *
 * API
 * ---
 *   defineNubiElement(tagName, ReactComponent, options?) → CustomElementClass
 *
 * options
 * -------
 *   observedAttributes: string[]
 *     HTML attribute names to observe. Changes trigger a re-render.
 *   propTypes: Record<string, 'string'|'number'|'boolean'|'json'>
 *     Coercion rules: how attribute strings are converted to prop values.
 *   defaultTheme: Record<string, string>
 *     CSS custom-property overrides injected into the shadow root at mount.
 *
 * Attribute → prop coercions
 * --------------------------
 *   'string'  — passed through as-is (the default)
 *   'number'  — Number(value); NaN → null
 *   'boolean' — true when attr is present and not exactly "false"
 *   'json'    — JSON.parse(value); parse errors → null (logged)
 *
 * Special props
 * -------------
 *   get-token  (attribute) / getToken (JS property)
 *     When set, passed to NubiProvider so inner components can call the SDK.
 *   base-url   (attribute)
 *     Backend base URL forwarded to the context client.
 *
 * Token resolution order
 * ----------------------
 *   1. `getToken` JS property (function or string)
 *   2. `get-token` attribute — name of a function on `window`
 *   3. `token` attribute — static JWT string (for dev/demo use only)
 */

import ReactDOM from 'react-dom/client'
import { createElement } from 'react'
import DOMPurify from 'dompurify'
import { injectTheme } from './theme.js'
import { createNubiContext } from './nubi-context.js'

// ---------------------------------------------------------------------------
// Attribute coercion helpers
// ---------------------------------------------------------------------------

/**
 * Coerce a string attribute value to a typed prop value.
 *
 * @param {string|null} value
 * @param {'string'|'number'|'boolean'|'json'} type
 * @param {string} attrName  — used only in error log
 * @returns {*}
 */
function coerceAttr(value, type, attrName) {
  if (value === null) {
    // Attribute absent
    if (type === 'boolean') return false
    return null
  }

  switch (type) {
    case 'number': {
      const n = Number(value)
      return Number.isNaN(n) ? null : n
    }
    case 'boolean':
      return value !== 'false' && value !== '0'
    case 'json':
      try {
        return JSON.parse(value)
      } catch {
        console.warn(`[nubi-wc] ${attrName}: JSON.parse failed on "${value}"`)
        return null
      }
    case 'string':
    default:
      return value
  }
}

// ---------------------------------------------------------------------------
// defineNubiElement
// ---------------------------------------------------------------------------

/**
 * Create and register a custom element that wraps a React component.
 *
 * @param {string} tagName
 *   e.g. "nubi-kpi-react"
 * @param {import('react').ComponentType<any>} ReactComponent
 *   The React component to render.
 * @param {object} [options]
 * @param {string[]} [options.observedAttributes]
 *   Attributes to observe. Always includes 'get-token', 'base-url', 'token'.
 * @param {Record<string, 'string'|'number'|'boolean'|'json'>} [options.propTypes]
 *   Coercion map for attribute → prop conversion.
 * @param {Record<string, string>} [options.defaultTheme]
 *   CSS custom-property overrides for the shadow root theme.
 * @returns {typeof HTMLElement}
 */
export function defineNubiElement(tagName, ReactComponent, options = {}) {
  const {
    observedAttributes: extraAttrs = [],
    propTypes = {},
    defaultTheme = {},
  } = options

  // Core attributes always observed
  const CORE_ATTRS = ['get-token', 'base-url', 'token']
  const allObserved = [...new Set([...CORE_ATTRS, ...extraAttrs])]

  class NubiElement extends HTMLElement {
    // ---- Custom-element boilerplate ----------------------------------------

    static get observedAttributes() {
      return allObserved
    }

    constructor() {
      super()
      /** @type {ShadowRoot} */
      this._shadow = this.attachShadow({ mode: 'open' })
      /** @type {import('react-dom/client').Root | null} */
      this._root = null
      /** @type {HTMLDivElement | null} */
      this._mountPoint = null
      // JS property override for getToken (takes priority over attribute)
      this._getTokenProp = null
    }

    connectedCallback() {
      this._ensureMount()
      this._render()
    }

    disconnectedCallback() {
      if (this._root) {
        this._root.unmount()
        this._root = null
      }
    }

    attributeChangedCallback(_name, oldVal, newVal) {
      if (oldVal !== newVal && this.isConnected) {
        this._render()
      }
    }

    // ---- JS property bridge ------------------------------------------------

    /** @type {string | (() => Promise<string> | string) | null} */
    get getToken() {
      return this._getTokenProp
    }

    set getToken(value) {
      this._getTokenProp = value
      if (this.isConnected) this._render()
    }

    // ---- Internal helpers --------------------------------------------------

    _ensureMount() {
      if (this._mountPoint) return

      // Inject theme tokens as a :host { ... } block
      injectTheme(this._shadow, defaultTheme)

      // Create the React mount point (plain div inside shadow root)
      this._mountPoint = document.createElement('div')
      this._mountPoint.setAttribute('data-nubi-mount', '1')
      this._mountPoint.style.cssText = 'display:contents;width:100%;height:100%;'
      this._shadow.appendChild(this._mountPoint)

      this._root = ReactDOM.createRoot(this._mountPoint)
    }

    /**
     * Resolve the getToken callback from (in priority order):
     *   1. `this._getTokenProp` JS property
     *   2. `get-token` attribute → window[fnName]
     *   3. `token` attribute → static string wrapped in a function
     */
    _resolveGetToken() {
      if (this._getTokenProp) {
        return typeof this._getTokenProp === 'function'
          ? this._getTokenProp
          : () => Promise.resolve(this._getTokenProp)
      }

      const fnName = this.getAttribute('get-token')
      if (fnName) {
        const fn = window[fnName]
        if (typeof fn === 'function') return fn
        console.warn(`[nubi-wc] window.${fnName} is not a function`)
      }

      const staticToken = this.getAttribute('token')
      if (staticToken) {
        return () => Promise.resolve(staticToken)
      }

      return null
    }

    _render() {
      if (!this._root || !this._mountPoint) return

      // Build props from observed attributes
      const props = {}
      for (const attr of extraAttrs) {
        const raw = this.getAttribute(attr)
        const type = propTypes[attr] || 'string'
        const value = coerceAttr(raw, type, attr)
        if (value !== null) {
          // Convert kebab-case attr names to camelCase for React props
          const propName = attr.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
          props[propName] = value
        }
      }

      const getToken = this._resolveGetToken()
      const baseUrl = this.getAttribute('base-url') || null

      // Build a context for this render
      const { NubiProvider, client, crossFilterBus } =
        baseUrl && getToken
          ? createNubiContext({ baseUrl, getToken })
          : { NubiProvider: ({ children }) => children, client: null, crossFilterBus: null }

      this._root.render(
        createElement(
          NubiProvider,
          { client, crossFilterBus },
          createElement(ReactComponent, props),
        ),
      )
    }

    /**
     * Sanitize an HTML string with DOMPurify before any innerHTML use.
     * Exposed as a protected helper for subclasses.
     *
     * @param {string} html
     * @returns {string}
     */
    _sanitize(html) {
      return DOMPurify.sanitize(html, { RETURN_DOM: false, RETURN_DOM_FRAGMENT: false })
    }
  }

  // Guard against double-define (e.g. bundle loaded twice)
  if (!customElements.get(tagName)) {
    customElements.define(tagName, NubiElement)
  }

  return NubiElement
}
