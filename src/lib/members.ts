/**
 * members.js — API client for org members + invites.
 *
 * Mirrors backend/app/routes/orgs.py:
 *   GET    /orgs/{id}/members
 *   PUT    /orgs/{id}/members/{userId}        { role }
 *   DELETE /orgs/{id}/members/{userId}
 *   GET    /orgs/{id}/invites
 *   POST   /orgs/{id}/invites                 { email, role }
 *   DELETE /orgs/{id}/invites/{inviteId}
 *   GET    /orgs/invites/{token}              (preview)
 *   POST   /orgs/invites/{token}/accept
 *
 * `get/post/put/del` (from api.js) prepend /api/v1 and attach the auth + org
 * headers. Mutations let errors propagate so the UI can surface 403 / 409 /
 * last-owner messages; list calls return safe empties on error.
 */

import { get, post, put, del } from './api.js'

export const ORG_ROLES = ['owner', 'admin', 'member', 'viewer']

export async function listMembers(orgId) {
  try {
    const data = await get(`/orgs/${orgId}/members`)
    return Array.isArray(data?.members) ? data.members : []
  } catch {
    return []
  }
}

export function updateMemberRole(orgId, userId, role) {
  return put(`/orgs/${orgId}/members/${userId}`, { role })
}

export function removeMember(orgId, userId) {
  return del(`/orgs/${orgId}/members/${userId}`)
}

export async function listInvites(orgId) {
  try {
    const data = await get(`/orgs/${orgId}/invites`)
    return Array.isArray(data?.invites) ? data.invites : []
  } catch {
    return []
  }
}

export function createInvite(orgId, email, role) {
  return post(`/orgs/${orgId}/invites`, { email, role })
}

export function revokeInvite(orgId, inviteId) {
  return del(`/orgs/${orgId}/invites/${inviteId}`)
}

export function getInvite(token) {
  return get(`/orgs/invites/${token}`)
}

export function acceptInvite(token) {
  return post(`/orgs/invites/${token}/accept`, {})
}

/** Build the shareable accept link for an invite token (absolute URL). */
export function inviteLink(token) {
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  return `${origin}/invite/${token}`
}
