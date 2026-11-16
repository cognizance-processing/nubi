/**
 * setup.js — Vitest jsdom environment polyfills.
 *
 * jsdom does not implement ResizeObserver or getBoundingClientRect fully.
 * We stub them here so custom elements that use these APIs don't crash in tests.
 */

// ResizeObserver stub
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class ResizeObserver {
    constructor(cb) { (this as any)._cb = cb }
    observe()    {}
    unobserve()  {}
    disconnect() {}
  }
}

// getBoundingClientRect returns zeros in jsdom — that's fine for our tests.
// Stub Element.prototype.getBoundingClientRect to avoid jsdom warnings.
if (!(Element.prototype.getBoundingClientRect as any)._nubi_stubbed) {
  const orig = Element.prototype.getBoundingClientRect
  Element.prototype.getBoundingClientRect = function () {
    try { return orig.call(this) } catch { return { top: 0, left: 0, width: 300, height: 200, right: 300, bottom: 200, x: 0, y: 0, toJSON: () => ({}) } }
  }
  ;(Element.prototype.getBoundingClientRect as any)._nubi_stubbed = true
}

// window.scrollX / scrollY (jsdom may not have them)
if (typeof window.scrollX === 'undefined') { Object.defineProperty(window, 'scrollX', { value: 0, writable: true }) }
if (typeof window.scrollY === 'undefined') { Object.defineProperty(window, 'scrollY', { value: 0, writable: true }) }
