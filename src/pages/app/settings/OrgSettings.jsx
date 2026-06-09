/**
 * OrgSettings — organisation "General" section.
 *
 * Rename the organisation, set its avatar, jump to Members / Billing, and
 * optionally delete it.  Member management lives in its own section
 * (/settings/members — MembersSettings.jsx).
 *
 * Delete rules (unchanged):
 *   - First fetch GET /orgs/{id}/deletion-impact.
 *   - If can_delete is false (org has projects), show the blocker message
 *     and DISABLE the delete button with a clear explanation.
 *   - If can_delete is true, open DangerDeleteDialog with the impact list
 *     and type-the-name confirmation.
 */

import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  Trash2,
  Users,
  CreditCard,
  ChevronRight,
} from 'lucide-react'
import { useOrg } from '../../../contexts/OrgContext.jsx'
import { useFeature } from '../../../lib/features.js'
import AvatarField from '../../../components/app/AvatarField.jsx'
import DangerDeleteDialog from '../../../components/app/DangerDeleteDialog.jsx'
import { updateOrg, deleteOrg, getOrgDeletionImpact } from '../../../lib/settings.js'
import {
  SettingsPageHeader,
  SettingsCard,
  Field,
  PrimaryButton,
  SavedBadge,
  ErrorText,
  DangerZone,
  DangerRow,
  DangerButton,
  inputCls,
} from './SettingsUI.jsx'

// ---------------------------------------------------------------------------
// Quick-link card (Members / Billing entry points)
// ---------------------------------------------------------------------------

function QuickLink({ to, Icon, title, description }) {
  return (
    <Link
      to={to}
      className="group flex items-center gap-3 px-4 py-3.5 rounded-2xl border border-border bg-surface hover:border-primary/40 hover:bg-surface-2/50 transition-colors"
    >
      <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-primary/10 text-primary shrink-0">
        <Icon size={16} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-fg">{title}</p>
        <p className="text-xs text-muted truncate">{description}</p>
      </div>
      <ChevronRight size={15} className="text-muted/50 group-hover:text-muted shrink-0 transition-colors" />
    </Link>
  )
}

export default function OrgSettings() {
  const { activeOrg, orgs, setActiveOrg } = useOrg()
  const billingEnabled = useFeature('billing')
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
    <div className="space-y-6">
      <SettingsPageHeader
        title="General"
        description="Your organisation's name and branding. Changes affect all members."
      />

      {isPersonal ? (
        <SettingsCard>
          <p className="text-sm text-muted">
            The personal workspace cannot be renamed or deleted. Create an organisation to
            collaborate with a team.
          </p>
        </SettingsCard>
      ) : (
        <>
          {/* Profile card — owner/admin only */}
          {canManage ? (
            <form onSubmit={handleSave}>
              <SettingsCard
                title="Organisation profile"
                description="The name and avatar shown across the app and to invited members."
                footer={
                  <>
                    <PrimaryButton type="submit" busy={saving} disabled={saving}>
                      Save changes
                    </PrimaryButton>
                    <SavedBadge show={saved} />
                    <ErrorText>{saveError}</ErrorText>
                  </>
                }
              >
                <div className="space-y-5 max-w-md">
                  <Field label="Organisation avatar">
                    <AvatarField
                      value={avatarUrl}
                      onChange={setAvatarUrl}
                      fallbackName={orgName || activeOrg?.name || '?'}
                    />
                  </Field>
                  <Field label="Organisation name" htmlFor="org-name">
                    <input
                      id="org-name"
                      type="text"
                      value={orgName}
                      onChange={(e) => setOrgName(e.target.value)}
                      placeholder="My Organisation"
                      className={inputCls}
                    />
                  </Field>
                </div>
              </SettingsCard>
            </form>
          ) : (
            <SettingsCard title="Organisation profile">
              <p className="text-sm text-muted">
                You have read-only access to this organisation&apos;s settings.
              </p>
            </SettingsCard>
          )}

          {/* Entry points: members + billing */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <QuickLink
              to="/settings/members"
              Icon={Users}
              title="Members"
              description="Invite teammates and manage roles"
            />
            {billingEnabled && (
              <QuickLink
                to="/billing"
                Icon={CreditCard}
                title="Billing"
                description="Plan, usage, and invoices"
              />
            )}
          </div>

          {/* Danger zone — owner/admin only */}
          {canManage && (
            <DangerZone>
              <DangerRow
                title="Delete this organisation"
                description="Permanently deletes the organisation and all of its resources. This cannot be undone."
                extra={
                  <>
                    {projectsBlocker && (
                      <div className="mt-3 flex items-start gap-2 px-3 py-2.5 rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 text-xs text-amber-700 dark:text-amber-300">
                        <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                        <span>
                          This organisation has {projectsBlocker.count} project
                          {projectsBlocker.count !== 1 ? 's' : ''}. Delete all projects first
                          before deleting the organisation.
                        </span>
                      </div>
                    )}
                    {impactError && (
                      <p className="mt-2 text-xs text-red-600 dark:text-red-400">{impactError}</p>
                    )}
                  </>
                }
              >
                <DangerButton
                  onClick={() => setDialogOpen(true)}
                  disabled={impactLoading || !canDelete}
                  busy={impactLoading}
                  title={!canDelete && projectsBlocker ? 'Delete all projects first' : undefined}
                >
                  {!impactLoading && <Trash2 size={15} />}
                  Delete organisation
                </DangerButton>
              </DangerRow>
            </DangerZone>
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
