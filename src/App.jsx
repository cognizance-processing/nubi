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
 *   /settings           → SettingsPage
 *
 *   STANDALONE (no layout)
 *   /d/:id          → DashboardViewPage (ProtectedRoute, full viewport)
 *   /dashboard      → redirect → /home
 *   /dev/illustrations → IllustrationGallery
 *   *               → NotFound
 */

import { Navigate, Routes, Route } from 'react-router-dom'
import { AuthProvider } from './contexts/AuthContext.jsx'
import { UiProvider } from './contexts/UiContext.jsx'
import { OrgProvider } from './contexts/OrgContext.jsx'
import { ProjectProvider } from './contexts/ProjectContext.jsx'

// Layouts
import MainLayout from './layouts/MainLayout.jsx'
import AppShell from './layouts/AppShell.jsx'

// Guards
import ProtectedRoute from './components/ProtectedRoute.jsx'

// Public pages
import LandingPage from './pages/LandingPage.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import DocsPage from './pages/DocsPage.jsx'
import ComparePage from './pages/ComparePage.jsx'
import NotFound from './pages/NotFound.jsx'

// Existing authed pages (do not edit these files)
import DashboardViewPage from './pages/DashboardViewPage.jsx'
import EditorPage from './pages/EditorPage.jsx'

// New stub app pages
import HomePage from './pages/app/HomePage.jsx'
import ConnectorsPage from './pages/app/ConnectorsPage.jsx'
import QueriesPage from './pages/app/QueriesPage.jsx'
import BlendBuilder from './pages/app/BlendBuilder.jsx'
import DashboardsPage from './pages/app/DashboardsPage.jsx'
import FlowsPage from './pages/app/FlowsPage.jsx'
import AutomationsPage from './pages/app/AutomationsPage.jsx'
import SettingsPage from './pages/app/SettingsPage.jsx'
import DataExplorerPage from './pages/app/DataExplorerPage.jsx'

// Dev
import IllustrationGallery from './pages/dev/IllustrationGallery.jsx'

// ---------------------------------------------------------------------------
// Provider wrapper for the authenticated shell
// ---------------------------------------------------------------------------

function AppShellWithProviders() {
  return (
    <UiProvider>
      <OrgProvider>
        <ProjectProvider>
          <AppShell />
        </ProjectProvider>
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
          <Route path="connectors" element={<ConnectorsPage />} />
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
          <Route path="settings" element={<SettingsPage />} />
        </Route>

        {/* ── Full-viewport authenticated routes (no AppShell) ─────────── */}
        <Route
          path="d/:id"
          element={
            <ProtectedRoute>
              <DashboardViewPage />
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
