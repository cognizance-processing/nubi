/**
 * SettingsPage — three-tab settings hub: Profile / Organisation / Project.
 *
 * Each tab is self-contained and explains its purpose so the user always
 * knows exactly what they are configuring.
 *
 * Tabs:
 *   Profile      — your personal name and avatar (overrides the Google picture)
 *   Organisation — org name, avatar, and danger zone (delete with impact list)
 *   Project      — project name, git sync, and danger zone
 */

import { useState } from 'react'
import { Settings, User, Building2, FolderGit2 } from 'lucide-react'
import ProfileSettings from './settings/ProfileSettings.jsx'
import OrgSettings from './settings/OrgSettings.jsx'
import ProjectSettings from './settings/ProjectSettings.jsx'

const TABS = [
  {
    id: 'profile',
    label: 'Profile',
    icon: User,
    description: 'Your name and avatar',
  },
  {
    id: 'organisation',
    label: 'Organisation',
    icon: Building2,
    description: 'Name, branding, and deletion',
  },
  {
    id: 'project',
    label: 'Project',
    icon: FolderGit2,
    description: 'Name, git sync, and deletion',
  },
]

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState('profile')

  return (
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-8">
      {/* Page header */}
      <header className="flex items-center gap-3">
        <div
          className="flex items-center justify-center w-11 h-11 rounded-2xl shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <Settings size={22} className="text-white" />
        </div>
        <div>
          <h1 className="font-display font-semibold text-2xl text-fg">Settings</h1>
          <p className="text-muted text-sm">
            Manage your profile, organisation, and project configuration.
          </p>
        </div>
      </header>

      {/* Tab bar */}
      <nav className="flex gap-1 border-b border-border">
        {TABS.map(({ id, label, icon: Icon, description }) => {
          const active = activeTab === id
          return (
            <button
              key={id}
              type="button"
              onClick={() => setActiveTab(id)}
              className={[
                'group flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-xl transition-colors',
                'border-b-2 -mb-px',
                active
                  ? 'border-primary text-fg bg-surface'
                  : 'border-transparent text-muted hover:text-fg hover:bg-surface/60',
              ].join(' ')}
              title={description}
            >
              <Icon
                size={15}
                className={active ? 'text-primary' : 'text-muted group-hover:text-fg'}
              />
              {label}
            </button>
          )
        })}
      </nav>

      {/* Tab content */}
      <div className="pt-2">
        {activeTab === 'profile' && <ProfileSettings />}
        {activeTab === 'organisation' && <OrgSettings />}
        {activeTab === 'project' && <ProjectSettings />}
      </div>
    </div>
  )
}
