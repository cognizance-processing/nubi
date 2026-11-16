/**
 * connectionStatus.js — pure mapping from a connection `state` to Badge
 * variant/label, shared by every "is this thing reachable" surface in
 * Settings (Connectors, Bridges, MCP servers) so they all speak the same
 * visual language instead of three different (or absent) ones.
 *
 * `state` matches the backend's status shape:
 *   'online'  — reachable right now (bridge connected / test passed)
 *   'offline' — was reachable, isn't now (bridge dropped / test failed)
 *   'unknown' — never checked yet
 *   'checking' — a check is in flight (frontend-only, optimistic)
 */

const MAP = {
  online: { variant: 'success', label: 'Connected' },
  offline: { variant: 'danger', label: 'Offline' },
  checking: { variant: 'default', label: 'Checking…' },
  unknown: { variant: 'default', label: 'Not tested' },
}

/** @param {string} state @returns {{variant: string, label: string}} */
export function statusBadgeProps(state) {
  return MAP[state] || MAP.unknown
}
