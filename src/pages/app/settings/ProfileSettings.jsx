/**
 * ProfileSettings — manage your personal profile (account scope).
 *
 * Allows updating display name and avatar.
 * The avatar defaults to the Google OAuth picture; the user can override
 * it by providing a custom URL or uploading a file (handled by AvatarField).
 *
 * Calls: PATCH /auth/me via settings.js#updateMe
 */

import { useState } from 'react'
import { useAuth } from '../../../contexts/AuthContext.jsx'
import AvatarField from '../../../components/app/AvatarField.jsx'
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
  const { user } = useAuth()

  const [name, setName] = useState(user?.name ?? '')
  const [avatarUrl, setAvatarUrl] = useState(user?.avatar_url ?? '')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setSaved(false)
    setError(null)
    try {
      await updateMe({
        name: name.trim() || undefined,
        avatar_url: avatarUrl || undefined,
      })
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
              <PrimaryButton type="submit" busy={saving} disabled={saving}>
                Save profile
              </PrimaryButton>
              <SavedBadge show={saved} />
              <ErrorText>{error}</ErrorText>
            </>
          }
        >
          <FieldRowGroup>
            <FieldRow
              label="Avatar"
              description="Shown next to your name across the app."
              hint={
                !avatarUrl && user?.avatar_url
                  ? 'Using your Google profile picture. Set a custom URL or upload a file to override it.'
                  : undefined
              }
            >
              <AvatarField
                value={avatarUrl || user?.avatar_url || ''}
                onChange={setAvatarUrl}
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
