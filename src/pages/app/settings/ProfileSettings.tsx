/**
 * ProfileSettings — manage your personal profile (account scope).
 *
 * Allows updating display name and avatar.
 * The avatar defaults to the Google OAuth picture; the user can override
 * it by providing a custom URL or uploading a file (handled by AvatarField).
 *
 * Calls: PATCH /auth/me via settings.js#updateMe
 */

import { useEffect, useState } from 'react'
import { useAuth } from '../../../contexts/AuthContext.jsx'
import AvatarField from '../../../components/app/AvatarField.jsx'
import { useAvatarUpload } from '../../../components/app/useAvatarUpload.js'
import { updateMe } from '../../../lib/settings.js'
import {
  SettingsPageHeader,
  SettingsCard,
  FieldRowGroup,
  FieldRow,
  PrimaryButton,
  SavedBadge,
  ErrorText,
  inputCls,
} from './SettingsUI.jsx'

export default function ProfileSettings() {
  const { user, updateUser } = useAuth()

  const [name, setName] = useState(user?.name ?? '')
  const [avatarUrl, setAvatarUrl] = useState(user?.avatar_url ?? '')
  const avatarUpload = useAvatarUpload(avatarUrl, setAvatarUrl)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  // Sync local state when the signed-in user changes (e.g. session restore).
  useEffect(() => {
    setName(user?.name ?? '')
    setAvatarUrl(user?.avatar_url ?? '')
  }, [user?.id, user?.name, user?.avatar_url])

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setSaved(false)
    setError(null)
    try {
      // Only send the avatar when it changed; null clears it server-side.
      const nextAvatar = avatarUrl || null
      const data = await updateMe({
        name: name.trim() || undefined,
        avatar_url: nextAvatar !== (user?.avatar_url ?? null) ? nextAvatar : undefined,
      })
      if (data?.user) updateUser(data.user)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err) {
      setError(err?.message ?? 'Failed to save profile.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <SettingsPageHeader
        title="Profile"
        description="Your display name and the avatar other members see."
      />

      <form onSubmit={handleSave}>
        <SettingsCard
          title="Your profile"
          footer={
            <>
              <PrimaryButton type="submit" busy={saving} disabled={saving || avatarUpload.uploading}>
                Save profile
              </PrimaryButton>
              <SavedBadge show={saved} />
              <ErrorText>{error ?? avatarUpload.uploadError}</ErrorText>
            </>
          }
        >
          <FieldRowGroup>
            <FieldRow
              label="Avatar"
              description="Shown next to your name across the app."
            >
              <AvatarField
                value={avatarUrl}
                onChange={avatarUpload.onChange}
                uploading={avatarUpload.uploading}
                fallbackName={name || user?.email || '?'}
              />
            </FieldRow>

            <FieldRow
              label="Display name"
              htmlFor="profile-name"
              description="How other members see you in this workspace."
            >
              <input
                id="profile-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
                className={inputCls}
              />
            </FieldRow>

            <FieldRow
              label="Email address"
              description="Used to sign in and receive notifications."
              hint="Your email address cannot be changed here."
            >
              <div
                className="px-3 py-2 rounded-xl bg-bg/60 border border-border text-sm text-muted select-all truncate"
                title={user?.email ?? undefined}
              >
                {user?.email ?? '—'}
              </div>
            </FieldRow>
          </FieldRowGroup>
        </SettingsCard>
      </form>
    </div>
  )
}
