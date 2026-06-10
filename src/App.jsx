/**
 * App — root route configuration.
 *
 * Route structure:
 *
 *   PUBLIC (MainLayout — Navbar + Footer)
 *   /               → LandingPage
 *   /login          → Login
 *   /register       → Register
 *   /docs           → DocsPage
 *   /docs/:slug     → DocsPage
 *   /compare        → ComparePage
 *
 *   AUTHENTICATED (AppShell — sidebar + topbar + chat panel)
 *   Wrapped in ProtectedRoute > UiProvider > OrgProvider
 *
 *   /home               → HomePage
 *   /connectors         → ConnectorsPage
 *   /queries            → QueriesPage
 *   /queries/:id        → QueriesPage
 *   /dashboards         → DashboardsPage
 *   /editor             → EditorPage  (existing)
 *   /editor/:id         → EditorPage  (existing)
 *   /playground         → redirect → /queries  (Playground merged into Queries)
 *   /settings           → redirect → /settings/profile  (SettingsLayout grouped sidebar)
 *   /settings/profile   → ProfileSettings   (Account)
 *   /settings/organization → OrgSettings    (Organization › General)
 *   /settings/members   → MembersSettings   (Organization › Members)
 *   /settings/security  → SecuritySettings  (Organization › Security — JWT issuers / JWKS)
 *   /settings/project   → ProjectSettings   (Project › General)
 *   /secrets            → SecretsPage
 *   /billing            → EE-only; rendered only when billing feature is enabled
 *                         and the EE module is loaded. Absent in OSS builds.
 *
 *   STANDALONE (no layout)
 *   /onboarding     → OnboardingPage (ProtectedRoute, full viewport) — forced
 *                     onboarding for authed users with zero org memberships
 *   /d/:id          → DashboardViewPage (ProtectedRoute, full viewport)
 *   /dashboard      → redirect → /home
 *   /dev/illustrations → IllustrationGallery
 *   *               → NotFound
 *
 * EE mount
 * --------
 * On startup we attempt a dynamic import of src/ee/index.js.  This fails
 * silently when the EE tree is absent (OSS build).  When it loads, it calls
 * registerEe() which fills the slot registry so EE components become available
 * without core ever statically importing src/ee.
 */

import { Suspense, createElement } from 'react'
import { Navigate, Routes, Route, useLocation } from 'react-router-dom'
import { AuthProvider } from './contexts/AuthContext.jsx'
import { UiProvider } from './contexts/UiContext.jsx'
import { OrgProvider, useOrg } from './contexts/OrgContext.jsx'
import { ProjectProvider } from './contexts/ProjectContext.jsx'
import { EnvProvider } from './contexts/EnvContext.jsx'

// Layouts
import MainLayout from './layouts/MainLayout.jsx'
import AppShell from './layouts/AppShell.jsx'

// Guards
import ProtectedRoute from './components/ProtectedRoute.jsx'

// Public pages
import LandingPage from './pages/LandingPage.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import OnboardingPage from './pages/OnboardingPage.jsx'
import DocsPage from './pages/DocsPage.jsx'
import ComparePage from './pages/ComparePage.jsx'
import PricingPage from './pages/PricingPage.jsx'
import LegalPage from './pages/LegalPage.jsx'
import NotFound from './pages/NotFound.jsx'

// Existing authed pages (do not edit these files)
import DashboardViewPage from './pages/DashboardViewPage.jsx'
import EditorPage from './pages/EditorPage.jsx'

// New stub app pages
import HomePage from './pages/app/HomePage.jsx'
import InviteAcceptPage from './pages/app/InviteAcceptPage.jsx'
import ConnectorsPage from './pages/app/ConnectorsPage.jsx'
import DataBrowser from './pages/app/DataBrowser.jsx'
import QueriesPage from './pages/app/QueriesPage.jsx'
import BlendBuilder from './pages/app/BlendBuilder.jsx'
import DashboardsPage from './pages/app/DashboardsPage.jsx'
import FlowsPage from './pages/app/FlowsPage.jsx'
import AutomationsPage from './pages/app/AutomationsPage.jsx'
import SettingsLayout from './pages/app/settings/SettingsLayout.jsx'
import ProfileSettings from './pages/app/settings/ProfileSettings.jsx'
import OrgSettings from './pages/app/settings/OrgSettings.jsx'
import MembersSettings from './pages/app/settings/MembersSettings.jsx'
import ProjectSettings from './pages/app/settings/ProjectSettings.jsx'
import SecuritySettings from './pages/app/settings/SecuritySettings.jsx'
import SecretsPage from './pages/app/SecretsPage.jsx'
import DataExplorerPage from './pages/app/DataExplorerPage.jsx'

// Admin portal (superadmin-only; AdminLayout wraps RequireSuperadmin which
// renders a 404-style view for non-superadmins so the portal stays hidden)
import AdminLayout from './pages/admin/AdminLayout.jsx'
import AdminOverviewPage from './pages/admin/AdminOverviewPage.jsx'
import AdminUsersPage from './pages/admin/AdminUsersPage.jsx'
import AdminOrgsPage from './pages/admin/AdminOrgsPage.jsx'
import AdminOrgDetailPage from './pages/admin/AdminOrgDetailPage.jsx'

// Dev
import IllustrationGallery from './pages/dev/IllustrationGallery.jsx'

// ---------------------------------------------------------------------------
// EE dynamic mount (open-core boundary — NO static import of src/ee)
//
// Core reads the billing page component from the slot registry at render time.
// If the EE module is absent (OSS build) or the billing feature is disabled,
// the /billing route simply renders null (effectively a 404).
// ---------------------------------------------------------------------------

import { getSlot } from './ee/registry.js'
import { useFeature } from './lib/features.js'

/**
 * Attempt to load the EE module at startup.  Failures are silent — the OSS
 * build continues normally.  When the module loads it calls registerEe() which
 * fills slots in registry.js; no further action needed here.
 */
async function _tryLoadEe() {
  try {
    const ee = await import('./ee/index.js')
    if (typeof ee.registerEe === 'function') {
      ee.registerEe()
    }
  } catch {
    // EE tree absent or failed to load — degrade silently.
  }
}

// Kick off EE load immediately (fire-and-forget; no await needed here).
_tryLoadEe()

/**
 * EeBillingSlot — renders the 'billing-page' slot component when available.
 * Rendered lazily inside a Suspense boundary so missing EE never blocks the app.
 * Returns null when EE is not loaded or billing is not enabled.
 */
function EeBillingSlot() {
  const billingEnabled = useFeature('billing')

  // Re-read the slot each render — registry.js notifies the parent EeRouteGuard
  // via onSlotRegistered, which forces a re-render when EE populates the slot.
  // Use createElement instead of JSX so the linter does not treat the slot
  // component reference as a "component created during render".
  const BillingPage = getSlot('billing-page')

  if (!billingEnabled || !BillingPage) return null
  return createElement(BillingPage)
}

// ---------------------------------------------------------------------------
// Provider wrapper for the authenticated shell
// ---------------------------------------------------------------------------

/**
 * RequireOrg — forced-onboarding guard inside the app shell.
 *
 * Reads OrgContext: while /orgs is loading it shows a full-viewport spinner
 * (no shell flash); when the fetch SUCCEEDED with zero memberships (hasNoOrgs)
 * it redirects to /onboarding. Transport errors keep the DEFAULT_ORG fallback
 * inside OrgContext, so offline/dev is unaffected.
 *
 * Exception: /invite/:token stays reachable for org-less users — accepting an
 * invite is one of the two ways OUT of onboarding.
 */
function RequireOrg({ children }) {
  const { loading, hasNoOrgs } = useOrg()
  const location = useLocation()

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg">
        <div
          className="h-8 w-8 rounded-full border-4 border-primary border-t-transparent animate-spin"
          role="status"
          aria-label="Loading"
        />
      </div>
    )
  }

  if (hasNoOrgs && !location.pathname.startsWith('/invite/')) {
    return <Navigate to="/onboarding" replace />
  }

  return children
}

function AppShellWithProviders() {
  return (
    <UiProvider>
      <OrgProvider>
        <RequireOrg>
          <ProjectProvider>
            <EnvProvider>
              <AppShell />
            </EnvProvider>
          </ProjectProvider>
        </RequireOrg>
      </OrgProvider>
    </UiProvider>
  )
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  return (
    <AuthProvider>
      <Routes>

        {/* ── Public routes — MainLayout (Navbar + optional Footer) ─────── */}
        <Route element={<MainLayout />}>
          <Route index element={<LandingPage />} />
          <Route path="docs" element={<DocsPage />} />
          <Route path="docs/:slug" element={<DocsPage />} />
          <Route path="compare" element={<ComparePage />} />
          <Route path="pricing" element={<PricingPage />} />
          <Route path="privacy" element={<LegalPage doc="privacy" />} />
          <Route path="terms" element={<LegalPage doc="terms" />} />
        </Route>

        {/* ── Auth routes — standalone full-viewport (no Navbar/Footer) ─── */}
        <Route path="login" element={<Login />} />
        <Route path="register" element={<Register />} />

        {/* ── Authenticated app shell — sidebar + topbar + chat ─────────── */}
        <Route
          element={
            <ProtectedRoute>
              <AppShellWithProviders />
            </ProtectedRoute>
          }
        >
          {/* Redirect legacy /dashboard → /home */}
          <Route path="dashboard" element={<Navigate to="/home" replace />} />

          <Route path="home" element={<HomePage />} />
          <Route path="invite/:token" element={<InviteAcceptPage />} />
          <Route path="connectors" element={<ConnectorsPage />} />
          <Route path="connectors/:id/data" element={<DataBrowser />} />
          <Route path="data" element={<DataExplorerPage />} />
          <Route path="queries" element={<QueriesPage />} />
          <Route path="queries/:id" element={<QueriesPage />} />
          <Route path="queries/blend" element={<BlendBuilder />} />
          <Route path="dashboards" element={<DashboardsPage />} />
          <Route path="flows" element={<FlowsPage />} />
          <Route path="flows/:id" element={<FlowsPage />} />
          <Route path="automations" element={<AutomationsPage />} />
          <Route path="editor" element={<EditorPage />} />
          <Route path="editor/:id" element={<EditorPage />} />
          {/* Playground merged into Queries — keep route as a redirect so old links work */}
          <Route path="playground" element={<Navigate to="/queries" replace />} />
          {/* Settings — sub-nav layout with per-section routes */}
          <Route path="settings" element={<SettingsLayout />}>
            {/* /settings → /settings/profile */}
            <Route index element={<Navigate to="profile" replace />} />
            <Route path="profile" element={<ProfileSettings />} />
            <Route path="organization" element={<OrgSettings />} />
            <Route path="members" element={<MembersSettings />} />
            <Route path="project" element={<ProjectSettings />} />
            <Route path="security" element={<SecuritySettings />} />
          </Route>
          {/* Secrets are flow-scoped — homed under the Flows section, not top-level nav. */}
          <Route path="flows/secrets" element={<SecretsPage />} />

          {/* Admin portal — superadmin only (non-admins see a 404-style view) */}
          <Route path="admin" element={<AdminLayout />}>
            <Route index element={<AdminOverviewPage />} />
            <Route path="users" element={<AdminUsersPage />} />
            <Route path="orgs" element={<AdminOrgsPage />} />
            <Route path="orgs/:id" element={<AdminOrgDetailPage />} />
          </Route>

          {/* EE-only: /billing — rendered only when EE module is loaded and
              billing feature is enabled.  Core never statically imports src/ee;
              EeBillingSlot reads the component from the slot registry at runtime.
              BillingFrontendAgent (Phase 2) fills the 'billing-page' slot. */}
          <Route
            path="billing"
            element={
              <Suspense fallback={null}>
                <EeBillingSlot />
              </Suspense>
            }
          />
        </Route>

        {/* ── Full-viewport authenticated routes (no AppShell) ─────────── */}
        {/* Forced onboarding for users with zero org memberships (e.g. new
            Google OAuth users). Outside the shell + OrgProvider on purpose:
            it fetches /orgs itself and bounces to /home when orgs exist. */}
        <Route
          path="onboarding"
          element={
            <ProtectedRoute>
              <OnboardingPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="d/:id"
          element={
            <ProtectedRoute>
              {/* Full-viewport, but still needs org/project context: the page
                  calls useCanWrite() and board fetches need the X-Org-Id /
                  X-Project-Id headers the providers install. */}
              <UiProvider>
                <OrgProvider>
                  <ProjectProvider>
                    <EnvProvider>
                      <DashboardViewPage />
                    </EnvProvider>
                  </ProjectProvider>
                </OrgProvider>
              </UiProvider>
            </ProtectedRoute>
          }
        />

        {/* ── Dev only ─────────────────────────────────────────────────── */}
        <Route path="dev/illustrations" element={<IllustrationGallery />} />

        {/* ── Catch-all ────────────────────────────────────────────────── */}
        <Route path="*" element={<NotFound />} />

      </Routes>
    </AuthProvider>
  )
}
