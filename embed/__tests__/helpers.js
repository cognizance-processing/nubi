/**
 * helpers.js — Shared test utilities for embed component tests.
 */

/**
 * Build a syntactically valid (but unsigned) JWT with the given scopes.
 * The decodeScopes() function only base64-decodes the payload — it never
 * verifies the signature — so these tokens work correctly for UI gating tests.
 *
 * @param {string[]} scopes
 * @param {object} [extraClaims]
 * @returns {string}
 */
export function makeToken(scopes = [], extraClaims = {}) {
  const header  = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')

  const payload = btoa(JSON.stringify({
    sub:  'test-user-1',
    iss:  'nubi-test',
    aud:  'nubi:test',
    exp:  Math.floor(Date.now() / 1000) + 3600,
    iat:  Math.floor(Date.now() / 1000),
    scope: scopes.join(' '),
    ...extraClaims,
  })).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')

  return `${header}.${payload}.fakesig`
}

/**
 * Wait for the next microtask queue flush (Promise.resolve()) plus a
 * configurable number of additional event-loop ticks.
 *
 * @param {number} [ticks=1]
 */
export async function nextTick(ticks = 1) {
  for (let i = 0; i < ticks; i++) {
    await new Promise(resolve => setTimeout(resolve, 0))
  }
}

/**
 * Dispatch a connect / disconnect cycle by appending to document.body
 * and removing immediately after the test.  Returns the element.
 *
 * @param {HTMLElement} el
 * @returns {HTMLElement}
 */
export function mount(el) {
  document.body.appendChild(el)
  return el
}

export function unmount(el) {
  el.remove()
}
