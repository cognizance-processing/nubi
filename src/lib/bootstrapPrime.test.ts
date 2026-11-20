/**
 * bootstrapPrime.test.ts — the refresh-delivered shell bootstrap.
 *
 * Run with:
 *   node --import tsx --test src/lib/bootstrapPrime.test.ts
 */
import { beforeEach, describe, it } from 'node:test'
import assert from 'node:assert/strict'

const store = new Map<string, string>()
;(globalThis as any).localStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => void store.set(k, String(v)),
  removeItem: (k: string) => void store.delete(k),
  clear: () => store.clear(),
}

const {
  ACTIVE_ORG_KEY,
  activeProjectKey,
  bootstrapRefreshQuery,
  clearPrimedBootstrap,
  stashPrimedBootstrap,
  takePrimedBootstrap,
} = await import('./bootstrapPrime.js')

const BOOT = {
  orgs: [{ id: 'o1' }],
  active_org_id: 'o1',
  projects: [{ id: 'p1' }],
  active_project_id: 'p1',
  environments: [],
  features: [],
}

describe('refresh-delivered bootstrap', () => {
  beforeEach(() => {
    store.clear()
    clearPrimedBootstrap()
  })

  it('builds the refresh query from the saved org and project', () => {
    assert.equal(bootstrapRefreshQuery(), 'bootstrap=1')
    localStorage.setItem(ACTIVE_ORG_KEY, 'o1')
    localStorage.setItem(activeProjectKey('o1'), 'p1')
    assert.equal(bootstrapRefreshQuery(), 'bootstrap=1&org_id=o1&project_id=p1')
  })

  it('hands the payload over once, for the same hints', () => {
    localStorage.setItem(ACTIVE_ORG_KEY, 'o1')
    localStorage.setItem(activeProjectKey('o1'), 'p1')
    stashPrimedBootstrap(BOOT)
    assert.equal(takePrimedBootstrap('o1', 'p1'), BOOT)
    assert.equal(takePrimedBootstrap('o1', 'p1'), null)
  })

  it('refuses it when the hints changed, and forgets it', () => {
    localStorage.setItem(ACTIVE_ORG_KEY, 'o1')
    stashPrimedBootstrap(BOOT)
    assert.equal(takePrimedBootstrap('o2', null), null)
    assert.equal(takePrimedBootstrap('o1', null), null)
  })

  it('clear drops it (sign-out / another sign-in)', () => {
    stashPrimedBootstrap(BOOT)
    clearPrimedBootstrap()
    assert.equal(takePrimedBootstrap(null, null), null)
  })

  it('ignores a malformed payload', () => {
    assert.equal(stashPrimedBootstrap({ nope: true }), null)
    assert.equal(takePrimedBootstrap(null, null), null)
  })
})
