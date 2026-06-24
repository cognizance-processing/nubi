/**
 * react-wc.js — React-to-Web-Component factory.
 *
 * defineNubiElement(tag, ReactComponent, options) → CustomElementClass
 *
 * The returned class:
 *  - Attaches an open shadow root.
 *  - Injects Nubi theme CSS custom properties into the shadow.
 *  - Mounts the React tree with ReactDOM.createRoot() inside the shadow.
 *  - Bridges observed HTML attributes to React props (with optional type coercion).
 *  - Calls root.unmount() on disconnectedCallback.
 *
 * React and ReactDOM are peer dependencies — they must be available in the
 * host bundle.  This file does not bundle them.
 *
 * @param {string} tag                 — custom element tag name, e.g. 'nubi-kpi-react'
 * @param {Function} ReactComponent    — React function or class component
 * @param {object}  [options]
 * @param {string[]}  [options.observedAttributes]  — HTML attributes to observe
 * @param {Record<string,'string'|'boolean'|'number'|'json'>} [options.propTypes]
 *   — coercion rules for observed attributes; unmapped attributes are passed as strings
 * @param {'dark'|'light'} [options.defaultTheme]  — default theme preset (default 'dark')
 * @returns {typeof HTMLElement}       — class ready for customElements.define()
 */

import * as React from 'react'
import { createRoot } from 'react-dom/client'
import { NUBI_THEME_CSS, NUBI_THEME_PRESETS } from './theme.js'

/**
 * Coerce an attribute string to the declared type.
 *
 * @param {string|null} value
 * @param {'string'|'boolean'|'number'|'json'|undefined} type
 * @returns {unknown}
 */
function coerce(value, type) {
  if (value === null) {
    // Absent attribute
    if (type === 'boolean') return false
    return null
  }
  switch (type) {
    case 'boolean': return value !== 'false' && value !== '0'
    case 'number':  return parseFloat(value)
    case 'json':    try { return JSON.parse(value) } catch { return null }
    default:        return value
  }
}

/**
 * Build a CSS string that sets all tokens as :host custom properties.
 * Merged from base theme + host `theme` attribute.
 */
function buildShadowThemeCss(preset = 'dark') {
  const tokens = NUBI_THEME_PRESETS[preset] ?? NUBI_THEME_PRESETS.dark
  const decls = Object.entries(tokens).map(([k, v]) => `  ${k}: ${v};`).join('\n')
  return `:host {\n  display: block;\n  box-sizing: border-box;\n${decls}\n}\n`
}

/**
 * @param {string} tag
 * @param {Function} ReactComponent
 * @param {{ observedAttributes?: string[], propTypes?: Record<string,string>, defaultTheme?: string }} [options]
 * @returns {typeof HTMLElement}
 */
export function defineNubiElement(tag, ReactComponent, options = {}) {
  const {
    observedAttributes = [],
    propTypes = {},
    defaultTheme = 'dark',
  } = options

  // Include 'theme' in observed list so preset changes re-render
  const allObserved = [...new Set([...observedAttributes, 'theme'])]

  class NubiReactElement extends HTMLElement {
    static get observedAttributes() { return allObserved }

    constructor() {
      super()
      this._shadow = this.attachShadow({ mode: 'open' })
      this._reactRoot = null
      this._styleEl = null
    }

    connectedCallback() {
      this._ensureStyle()
      this._mount()
    }

    disconnectedCallback() {
      if (this._reactRoot) {
        this._reactRoot.unmount()
        this._reactRoot = null
      }
    }

    attributeChangedCallback(name, oldVal, newVal) {
      if (oldVal === newVal) return
      if (name === 'theme') {
        // Rebuild theme CSS
        if (this._styleEl) {
          this._styleEl.textContent = buildShadowThemeCss(newVal || defaultTheme)
        }
      }
      if (this.isConnected) this._render()
    }

    _ensureStyle() {
      if (this._styleEl) return
      const style = document.createElement('style')
      const preset = this.getAttribute('theme') || defaultTheme
      style.textContent = buildShadowThemeCss(preset)
      this._shadow.insertBefore(style, this._shadow.firstChild)
      this._styleEl = style
    }

    _getProps() {
      const props = {}
      for (const attr of allObserved) {
        const val = this.getAttribute(attr)
        props[attr] = coerce(val, propTypes[attr])
      }
      return props
    }

    _mount() {
      if (!this._reactRoot) {
        // Container div inside the shadow root (React renders here)
        const container = document.createElement('div')
        container.style.cssText = 'display:contents'
        this._shadow.appendChild(container)
        this._reactRoot = createRoot(container)
      }
      this._render()
    }

    _render() {
      if (!this._reactRoot) return
      const props = this._getProps()
      this._reactRoot.render(React.createElement(ReactComponent, props))
    }
  }

  // Register the element (idempotent)
  if (!customElements.get(tag)) {
    customElements.define(tag, NubiReactElement)
  }

  return NubiReactElement
}
