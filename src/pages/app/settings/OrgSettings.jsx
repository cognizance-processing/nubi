/**
 * OrgSettings — rename your organisation, set its avatar, and optionally
 * delete it.
 *
 * Delete rules:
 *   - First fetch GET /orgs/{id}/deletion-impact.
 *   - If can_delete is false (org has projects), show the blocker message
 *     and DISABLE the delete button with a clear explanation.
 *   - If can_delete is true, open DangerDeleteDialog with the impact list
 *     and type-the-name confirmation.
 */

import { useEffect, useState, useCallback } from 'react'
import { Building2, Loader2, CheckCircle, AlertTriangle, Trash2, Users, UserPlus, Copy, Check, Mail } from 'lucide-react'
import { useOrg } from '../../../contexts/OrgContext.jsx'
import { useAuth } from '../../../contexts/AuthContext.jsx'
import AvatarField from '../../../components/app/AvatarField.jsx'
import DangerDeleteDialog from '../../../components/app/DangerDeleteDialog.jsx'
import { updateOrg, deleteOrg, getOrgDeletionImpact } from '../../../lib/settings.js'
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

const MANAGE_ROLES = ['owner', 'admin']

// ---------------------------------------------------------------------------
// MembersSection — list/manage org members + pending invites (under Org settings)
// ---------------------------------------------------------------------------

function MembersSection({ orgId, currentRole, currentUserId }) {
  const canManage = MANAGE_ROLES.includes(currentRole)

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

  const selectCls = 'px-2 py-1 rounded-lg bg-bg border border-border text-xs text-fg focus:outline-none focus:border-primary disabled:opacity-50 disabled:cursor-not-allowed'

  return (
    <div className="rounded-2xl border border-border overflow-hidden">
      <div className="px-5 py-4 bg-surface-2/40 border-b border-border flex items-center gap-2">
        <Users size={16} className="text-muted" />
        <h3 className="font-semibold text-sm text-fg">Members</h3>
        <span className="text-xs text-muted">({members.length})</span>
        {!canManage && (
          <span className="ml-auto text-[11px] text-muted">View only — ask an owner/admin to manage members.</span>
        )}
      </div>

      <div className="px-5 py-4 space-y-4">
        {err && (
          <p className="text-xs text-red-600 dark:text-red-400 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 px-3 py-2">{err}</p>
        )}

        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted py-3"><Loader2 size={13} className="animate-spin" /> Loading members…</div>
        ) : (
          <ul className="divide-y divide-border">
            {members.map((m) => {
              const isSelf = m.user_id === currentUserId
              const isLastOwner = m.role === 'owner' && ownerCount <= 1
              const rowBusy = busyRow === m.user_id
              return (
                <li key={m.user_id} className="flex items-center gap-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-fg truncate">
                      {m.name || m.email}{isSelf && <span className="text-muted"> (you)</span>}
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
                    <span className="text-xs font-medium text-muted capitalize">{m.role}</span>
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

        {/* Invite form + pending invites — managers only */}
        {canManage && (
          <div className="pt-3 border-t border-border space-y-4">
            <form onSubmit={sendInvite} className="flex flex-col sm:flex-row gap-2">
              <input
                type="email"
                required
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="teammate@example.com"
                className="flex-1 px-3 py-2 rounded-xl bg-bg border border-border text-sm text-fg placeholder:text-muted focus:outline-none focus:border-primary"
              />
              <select className={selectCls + ' !text-sm !py-2'} value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
                {ORG_ROLES.filter((r) => r !== 'owner' || currentRole === 'owner').map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
              <button
                type="submit"
                disabled={inviting || !inviteEmail.trim()}
                className="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-sm font-medium text-white disabled:opacity-50 shrink-0"
                style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
              >
                {inviting ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />}
                Invite
              </button>
            </form>
            <p className="text-[11px] text-muted -mt-2 flex items-center gap-1">
              <Mail size={11} /> An invite link is generated to share. Email is sent only if delivery is configured.
            </p>

            {invites.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-[11px] font-medium text-muted uppercase tracking-wider">Pending invites</p>
                <ul className="divide-y divide-border rounded-xl border border-border">
                  {invites.map((inv) => (
                    <li key={inv.id} className="flex items-center gap-2 px-3 py-2.5">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-fg truncate">{inv.email}</p>
                        <p className="text-[11px] text-muted">role: {inv.role}</p>
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
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default function OrgSettings() {
  const { activeOrg, orgs, setActiveOrg } = useOrg()
  const { user } = useAuth()
  const orgId = activeOrg?.id ?? null

  const [orgName, setOrgName] = useState(activeOrg?.name ?? '')
  const [avatarUrl, setAvatarUrl] = useState(activeOrg?.avatar_url ?? '')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState(null)

  // Impact state
  const [impact, setImpact] = useState(null)
  const [impactLoading, setImpactLoading] = useState(false)
  const [impactError, setImpactError] = useState(null)

  // Dialog
  const [dialogOpen, setDialogOpen] = useState(false)

  // Sync local state when active org changes
  useEffect(() => {
    setOrgName(activeOrg?.name ?? '')
    setAvatarUrl(activeOrg?.avatar_url ?? '')
    setImpact(null)
    setImpactError(null)
  }, [orgId, activeOrg?.name, activeOrg?.avatar_url])

  const loadImpact = useCallback(async () => {
    if (!orgId || orgId === 'personal') return
    setImpactLoading(true)
    setImpactError(null)
    try {
      const data = await getOrgDeletionImpact(orgId)
      setImpact(data)
    } catch (err) {
      setImpactError(err?.message ?? 'Could not load deletion impact.')
    } finally {
      setImpactLoading(false)
    }
  }, [orgId])

  // Load impact on mount / org change
  useEffect(() => {
    loadImpact()
  }, [loadImpact])

  async function handleSave(e) {
    e.preventDefault()
    if (!orgId || orgId === 'personal') return
    setSaving(true)
    setSaved(false)
    setSaveError(null)
    try {
      await updateOrg(orgId, {
        name: orgName.trim() || undefined,
        avatar_url: avatarUrl || undefined,
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err) {
      setSaveError(err?.message ?? 'Failed to save organisation settings.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    // DangerDeleteDialog only calls onConfirm when the typed name matches exactly,
    // so we can safely pass the known name as confirm_name to the API.
    const confirmName = impact?.name ?? activeOrg?.name ?? ''
    await deleteOrg(orgId, confirmName)
    // Switch away from the deleted org
    const remaining = orgs.filter((o) => o.id !== orgId)
    if (remaining.length > 0) {
      setActiveOrg(remaining[0].id)
    }
    setDialogOpen(false)
  }

  const isPersonal = !orgId || orgId === 'personal'

  // Determine blocker text
  const projectsBlocker = impact?.blockers?.find((b) => b.type === 'projects')
  const canDelete = impact?.can_delete === true
  // Org rename/branding/delete are owner/admin only (backend enforces via _require_manage).
  const canManage = ['owner', 'admin'].includes(activeOrg?.role)

  return (
    <div className="space-y-8">
      {/* Section header */}
      <div className="flex items-start gap-4 pb-5 border-b border-border">
        <div
          className="flex items-center justify-center w-10 h-10 rounded-xl shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <Building2 size={18} className="text-white" />
        </div>
        <div>
          <h2 className="font-display font-semibold text-base text-fg">Organisation settings</h2>
          <p className="text-sm text-muted mt-0.5">
            Manage the name and branding of your organisation. Changes affect all members.
          </p>
        </div>
      </div>

      {isPersonal ? (
        <p className="text-sm text-muted">
          The personal workspace cannot be renamed or deleted.
        </p>
      ) : (
        <>
          {/* Rename / avatar form — owner/admin only */}
          {!canManage && (
            <p className="text-sm text-muted">You have read-only access to this organisation&apos;s settings.</p>
          )}
          {canManage && (
          <form onSubmit={handleSave} className="space-y-6 max-w-md">
            {/* Avatar */}
            <div className="space-y-1.5">
              <label className="block text-xs font-medium text-muted">Organisation avatar</label>
              <AvatarField
                value={avatarUrl}
                onChange={setAvatarUrl}
                fallbackName={orgName || activeOrg?.name || '?'}
              />
            </div>

            {/* Name */}
            <div className="space-y-1.5">
              <label className="block text-xs font-medium text-muted" htmlFor="org-name">
                Organisation name
              </label>
              <input
                id="org-name"
                type="text"
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="My Organisation"
                className="w-full px-3 py-2 rounded-xl bg-bg border border-border text-sm text-fg placeholder:text-muted focus:outline-none focus:border-primary"
              />
            </div>

            {/* Save */}
            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={saving}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium text-white transition-opacity disabled:opacity-50"
                style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
              >
                {saving ? <Loader2 size={15} className="animate-spin" /> : null}
                Save changes
              </button>
              {saved && (
                <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400">
                  <CheckCircle size={15} />
                  Saved
                </span>
              )}
            </div>

            {saveError && (
              <p className="text-sm text-red-600 dark:text-red-400">{saveError}</p>
            )}
          </form>
          )}

          {/* Members */}
          <MembersSection orgId={orgId} currentRole={activeOrg?.role} currentUserId={user?.id} />

          {/* Danger zone — owner/admin only */}
          {canManage && (
          <div className="rounded-2xl border border-red-200 dark:border-red-900 overflow-hidden">
            <div className="px-5 py-4 bg-red-50 dark:bg-red-950/30 border-b border-red-200 dark:border-red-900">
              <h3 className="font-semibold text-sm text-red-700 dark:text-red-400">Danger zone</h3>
            </div>

            <div className="px-5 py-4 space-y-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-fg">Delete this organisation</p>
                  <p className="text-xs text-muted mt-0.5">
                    Permanently deletes the organisation and all of its resources. This cannot be
                    undone.
                  </p>

                  {/* Blocker message */}
                  {projectsBlocker && (
                    <div className="mt-3 flex items-start gap-2 px-3 py-2.5 rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 text-xs text-amber-700 dark:text-amber-300">
                      <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                      <span>
                        This organisation has {projectsBlocker.count} project
                        {projectsBlocker.count !== 1 ? 's' : ''}. Delete all projects first before
                        deleting the organisation.
                      </span>
                    </div>
                  )}

                  {impactError && (
                    <p className="mt-2 text-xs text-red-600 dark:text-red-400">{impactError}</p>
                  )}
                </div>

                <button
                  type="button"
                  onClick={() => setDialogOpen(true)}
                  disabled={impactLoading || !canDelete}
                  className="inline-flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
                  title={!canDelete && projectsBlocker ? 'Delete all projects first' : undefined}
                >
                  {impactLoading ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Trash2 size={15} />
                  )}
                  Delete organisation
                </button>
              </div>
            </div>
          </div>
          )}
        </>
      )}

      {/* Confirm dialog */}
      {dialogOpen && impact && (
        <DangerDeleteDialog
          resourceType="organisation"
          name={impact.name ?? activeOrg?.name ?? ''}
          impact={impact}
          onConfirm={handleDelete}
          onCancel={() => setDialogOpen(false)}
        />
      )}
    </div>
  )
}
