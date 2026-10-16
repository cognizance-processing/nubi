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

import { useRef, useState } from 'react'
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
import { useAsyncLoad } from '../../../hooks/useAsyncLoad.js'
import { SettingsPageHeader, SettingsCard, PrimaryButton } from './SettingsUI.jsx'
import { isValidEmail } from '../../../shell/shellLogic.js'
import Button from '../../../components/ui/Button.jsx'
import Tag from '../../../components/ui/Tag.jsx'
import Input, { Select } from '../../../components/ui/Input.jsx'
import EmptyState from '../../../components/ui/EmptyState.jsx'
import Skeleton from '../../../components/ui/Skeleton.jsx'

const MANAGE_ROLES = ['owner', 'admin']

function Avatar({ name = undefined, email = undefined }) {
  const initial = (name || email || '?').trim().charAt(0).toUpperCase()
  return (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-frame-line bg-surface-2 text-xs font-semibold text-muted" aria-hidden="true">
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

  const { data: membersData, loading, error: loadErrorObj, reload: refreshMembers } = useAsyncLoad(
    async () => {
      if (!orgId || orgId === 'personal') return { members: [], invites: [] }
      const [m, inv] = await Promise.all([
        listMembers(orgId),
        canManage ? listInvites(orgId) : Promise.resolve([]),
      ])
      return { members: m, invites: inv }
    },
    [orgId, canManage]
  )
  const members = membersData?.members ?? []
  const invites = membersData?.invites ?? []
  const loadError = !loading && loadErrorObj ? (loadErrorObj?.message ?? 'Failed to load members.') : null

  const [err, setErr] = useState(null)
  const [busyRow, setBusyRow] = useState(null)

  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('member')
  const [inviting, setInviting] = useState(false)
  const [copiedToken, setCopiedToken] = useState(null)
  const inviteEmailRef = useRef(null)

  function focusInvite() {
    inviteEmailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    inviteEmailRef.current?.focus()
  }

  const ownerCount = members.filter((m) => m.role === 'owner').length

  async function changeRole(m, role) {
    if (role === m.role) return
    setErr(null); setBusyRow(m.user_id)
    try { await updateMemberRole(orgId, m.user_id, role); refreshMembers() }
    catch (e) { setErr(e?.message ?? 'Failed to update role.') }
    finally { setBusyRow(null) }
  }

  async function handleRemove(m) {
    setErr(null); setBusyRow(m.user_id)
    try { await removeMember(orgId, m.user_id); refreshMembers() }
    catch (e) { setErr(e?.message ?? 'Failed to remove member.') }
    finally { setBusyRow(null) }
  }

  async function sendInvite(e) {
    e.preventDefault()
    setErr(null); setInviting(true)
    try { await createInvite(orgId, inviteEmail.trim(), inviteRole); setInviteEmail(''); refreshMembers() }
    catch (e2) { setErr(e2?.message ?? 'Failed to create invite.') }
    finally { setInviting(false) }
  }

  async function handleRevoke(inv) {
    setErr(null); setBusyRow(inv.id)
    try { await revokeInvite(orgId, inv.id); refreshMembers() }
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

  const selectCls = 'nubi-input nubi-select nubi-input-sm w-auto'

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
        <p className="nubi-notice nubi-notice-error m-0 text-xs" role="alert">
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
              <label htmlFor="invite-email" className="sr-only">Teammate email address</label>
              <Input
                id="invite-email"
                ref={inviteEmailRef}
                type="email"
                required
                icon={<Mail size={14} />}
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="teammate@example.com"
              />
            </div>
            <Select
              className="w-full sm:w-36"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
              aria-label="Invite role"
              options={ORG_ROLES.filter((r) => r !== 'owner' || currentRole === 'owner')}
            />
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
          <div className="space-y-2" aria-hidden="true">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : loadError ? (
          <div className="flex items-center justify-between gap-3 py-2">
            <p className="text-xs text-danger" role="alert">{loadError}</p>
            <Button variant="secondary" size="xs" onClick={refreshMembers}>Retry</Button>
          </div>
        ) : members.length === 0 ? (
          <EmptyState
            compact
            icon={<Users size={18} />}
            title="No members yet"
            description="Members can open and edit boards. Viewers are free and do not need a seat."
            action={canManage ? <Button variant="secondary" onClick={focusInvite}>Invite a member</Button> : undefined}
          />
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
                    <p className="text-sm text-fg truncate" title={m.name || m.email}>
                      {m.name || m.email}
                      {isSelf && <span className="text-muted"> (you)</span>}
                    </p>
                    {m.name && <p className="text-xs text-muted truncate" title={m.email}>{m.email}</p>}
                  </div>
                  {canManage ? (
                    <select
                      className={selectCls}
                      value={m.role}
                      disabled={rowBusy || isLastOwner}
                      title={isLastOwner ? 'The last owner cannot be demoted' : undefined}
                      aria-label={`Change role for ${m.name || m.email}`}
                      onChange={(e) => changeRole(m, e.target.value)}
                    >
                      {ORG_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  ) : (
                    <Tag variant={m.role === 'owner' ? 'ink' : 'neutral'}>{m.role}</Tag>
                  )}
                  {canManage && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => handleRemove(m)}
                      disabled={rowBusy || isLastOwner}
                      title={isLastOwner ? 'The last owner cannot be removed' : 'Remove member'}
                      aria-label={isLastOwner ? 'The last owner cannot be removed' : `Remove ${m.name || m.email}`}
                    >
                      {rowBusy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Trash2 size={14} aria-hidden="true" />}
                    </Button>
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
                  <p className="text-sm text-fg truncate" title={inv.email}>{inv.email}</p>
                  <p className="text-[11px] text-muted capitalize">role: {inv.role}</p>
                </div>
                <Button
                  variant="secondary"
                  size="xs"
                  onClick={() => copyLink(inv.token)}
                  aria-label={copiedToken === inv.token ? 'Invite link copied' : `Copy invite link for ${inv.email}`}
                >
                  {copiedToken === inv.token ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
                  {copiedToken === inv.token ? 'Copied' : 'Copy link'}
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => handleRevoke(inv)}
                  disabled={busyRow === inv.id}
                  title="Revoke invite"
                  aria-label={`Revoke invite for ${inv.email}`}
                >
                  {busyRow === inv.id ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <Trash2 size={13} aria-hidden="true" />}
                </Button>
              </li>
            ))}
          </ul>
        </SettingsCard>
      )}
    </div>
  )
}
