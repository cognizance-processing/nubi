/**
 * MembersSettings — manage organisation members and pending invites.
 *
 * Org-scoped (lives in the "Organization" group of the settings sidebar).
 * Extracted from OrgSettings so members get a first-class, prominent section.
 *
 * Calls (unchanged): lib/members.js
 *   listMembers / updateMemberRole / removeMember
 *   listInvites / createInvite / revokeInvite / inviteLink
 */

import { useEffect, useState, useCallback } from 'react'
import {
  Loader2,
  Trash2,
  UserPlus,
  Copy,
  Check,
  Mail,
  Users,
} from 'lucide-react'
import { useOrg } from '../../../contexts/OrgContext.jsx'
import { useAuth } from '../../../contexts/AuthContext.jsx'
import {
  ORG_ROLES,
  listMembers,
  updateMemberRole,
  removeMember,
  listInvites,
  createInvite,
  revokeInvite,
  inviteLink,
} from '../../../lib/members.js'
import { SettingsPageHeader, SettingsCard, PrimaryButton, inputCls } from './SettingsUI.jsx'
import { isValidEmail } from '../../../shell/shellLogic.js'

const MANAGE_ROLES = ['owner', 'admin']

const ROLE_BADGE = {
  owner: 'bg-brand-blue/10 text-brand-blue dark:bg-blue-500/15 dark:text-blue-300',
  admin: 'bg-brand-teal/10 text-brand-teal dark:bg-teal-500/15 dark:text-teal-300',
  member: 'bg-surface-2 text-muted',
  viewer: 'bg-surface-2 text-muted',
}

function Avatar({ name, email }) {
  const initial = (name || email || '?').trim().charAt(0).toUpperCase()
  return (
    <div className="w-8 h-8 rounded-full bg-surface-2 border border-border flex items-center justify-center text-xs font-semibold text-muted shrink-0">
      {initial}
    </div>
  )
}

export default function MembersSettings() {
  const { activeOrg } = useOrg()
  const { user } = useAuth()
  const orgId = activeOrg?.id ?? null
  const currentRole = activeOrg?.role
  const currentUserId = user?.id
  const canManage = MANAGE_ROLES.includes(currentRole)
  const isPersonal = !orgId || orgId === 'personal'

  const [members, setMembers] = useState([])
  const [invites, setInvites] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)
  const [busyRow, setBusyRow] = useState(null)

  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('member')
  const [inviting, setInviting] = useState(false)
  const [copiedToken, setCopiedToken] = useState(null)

  const refresh = useCallback(async () => {
    if (!orgId || orgId === 'personal') return
    setLoading(true)
    const [m, inv] = await Promise.all([
      listMembers(orgId),
      canManage ? listInvites(orgId) : Promise.resolve([]),
    ])
    setMembers(m)
    setInvites(inv)
    setLoading(false)
  }, [orgId, canManage])

  useEffect(() => { refresh() }, [refresh])

  const ownerCount = members.filter((m) => m.role === 'owner').length

  async function changeRole(m, role) {
    if (role === m.role) return
    setErr(null); setBusyRow(m.user_id)
    try { await updateMemberRole(orgId, m.user_id, role); await refresh() }
    catch (e) { setErr(e?.message ?? 'Failed to update role.') }
    finally { setBusyRow(null) }
  }

  async function handleRemove(m) {
    setErr(null); setBusyRow(m.user_id)
    try { await removeMember(orgId, m.user_id); await refresh() }
    catch (e) { setErr(e?.message ?? 'Failed to remove member.') }
    finally { setBusyRow(null) }
  }

  async function sendInvite(e) {
    e.preventDefault()
    setErr(null); setInviting(true)
    try { await createInvite(orgId, inviteEmail.trim(), inviteRole); setInviteEmail(''); await refresh() }
    catch (e2) { setErr(e2?.message ?? 'Failed to create invite.') }
    finally { setInviting(false) }
  }

  async function handleRevoke(inv) {
    setErr(null); setBusyRow(inv.id)
    try { await revokeInvite(orgId, inv.id); await refresh() }
    catch (e) { setErr(e?.message ?? 'Failed to revoke invite.') }
    finally { setBusyRow(null) }
  }

  function copyLink(token) {
    try {
      navigator.clipboard.writeText(inviteLink(token))
      setCopiedToken(token)
      setTimeout(() => setCopiedToken(null), 2000)
    } catch { /* clipboard blocked */ }
  }

  const selectCls =
    'px-2 py-1 rounded-lg bg-bg border border-border text-xs text-fg focus:outline-none focus:border-primary disabled:opacity-50 disabled:cursor-not-allowed'

  if (isPersonal) {
    return (
      <div>
        <SettingsPageHeader
          title="Members"
          description="Invite teammates and manage their roles."
        />
        <SettingsCard>
          <p className="text-sm text-muted">
            The personal workspace has no members. Create an organisation to collaborate
            with your team.
          </p>
        </SettingsCard>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="Members"
        description={`People with access to ${activeOrg?.name ?? 'this organisation'} and their roles.`}
      />

      {err && (
        <p className="text-xs text-red-600 dark:text-red-400 rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 px-3 py-2">
          {err}
        </p>
      )}

      {/* Invite — managers only */}
      {canManage && (
        <SettingsCard
          title="Invite a teammate"
          description="An invite link is generated to share. Email is sent only if delivery is configured."
        >
          <form onSubmit={sendInvite} className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1">
              <Mail size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
              <input
                type="email"
                required
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="teammate@example.com"
                className={inputCls + ' !pl-9'}
              />
            </div>
            <select
              className={selectCls + ' !text-sm !py-2'}
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
              aria-label="Invite role"
            >
              {ORG_ROLES.filter((r) => r !== 'owner' || currentRole === 'owner').map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
            <PrimaryButton type="submit" busy={inviting} disabled={inviting || !isValidEmail(inviteEmail)} className="shrink-0">
              {!inviting && <UserPlus size={14} />}
              Invite
            </PrimaryButton>
          </form>
        </SettingsCard>
      )}

      {/* Member list */}
      <SettingsCard
        title={`Members${loading ? '' : ` (${members.length})`}`}
        description={canManage
          ? 'Change roles or remove members. The last owner cannot be demoted or removed.'
          : 'View only — ask an owner or admin to manage members.'}
      >
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted py-2">
            <Loader2 size={13} className="animate-spin" /> Loading members…
          </div>
        ) : members.length === 0 ? (
          <div className="py-6 text-center">
            <Users size={24} className="mx-auto text-muted/40 mb-2" />
            <p className="text-sm text-muted">No members yet.</p>
          </div>
        ) : (
          <ul className="divide-y divide-border -my-2">
            {members.map((m) => {
              const isSelf = m.user_id === currentUserId
              const isLastOwner = m.role === 'owner' && ownerCount <= 1
              const rowBusy = busyRow === m.user_id
              return (
                <li key={m.user_id} className="flex items-center gap-3 py-3">
                  <Avatar name={m.name} email={m.email} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-fg truncate">
                      {m.name || m.email}
                      {isSelf && <span className="text-muted"> (you)</span>}
                    </p>
                    {m.name && <p className="text-xs text-muted truncate">{m.email}</p>}
                  </div>
                  {canManage ? (
                    <select
                      className={selectCls}
                      value={m.role}
                      disabled={rowBusy || isLastOwner}
                      title={isLastOwner ? 'The last owner cannot be demoted' : undefined}
                      onChange={(e) => changeRole(m, e.target.value)}
                    >
                      {ORG_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  ) : (
                    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-md capitalize ${ROLE_BADGE[m.role] ?? ROLE_BADGE.member}`}>
                      {m.role}
                    </span>
                  )}
                  {canManage && (
                    <button
                      type="button"
                      onClick={() => handleRemove(m)}
                      disabled={rowBusy || isLastOwner}
                      title={isLastOwner ? 'The last owner cannot be removed' : 'Remove member'}
                      className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
                    >
                      {rowBusy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </SettingsCard>

      {/* Pending invites — managers only */}
      {canManage && !loading && invites.length > 0 && (
        <SettingsCard
          title={`Pending invites (${invites.length})`}
          description="Invites that have been created but not yet accepted."
        >
          <ul className="divide-y divide-border -my-2">
            {invites.map((inv) => (
              <li key={inv.id} className="flex items-center gap-2 py-3">
                <Avatar email={inv.email} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-fg truncate">{inv.email}</p>
                  <p className="text-[11px] text-muted capitalize">role: {inv.role}</p>
                </div>
                <button
                  type="button"
                  onClick={() => copyLink(inv.token)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-muted hover:text-primary border border-border hover:border-primary/40 transition-colors shrink-0"
                >
                  {copiedToken === inv.token ? <Check size={12} /> : <Copy size={12} />}
                  {copiedToken === inv.token ? 'Copied' : 'Copy link'}
                </button>
                <button
                  type="button"
                  onClick={() => handleRevoke(inv)}
                  disabled={busyRow === inv.id}
                  title="Revoke invite"
                  className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-30 shrink-0"
                >
                  {busyRow === inv.id ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                </button>
              </li>
            ))}
          </ul>
        </SettingsCard>
      )}
    </div>
  )
}
