import { Routes, Route } from 'react-router-dom'
import { AuthProvider } from './contexts/AuthContext.jsx'
import MainLayout from './layouts/MainLayout.jsx'
import LandingPage from './pages/LandingPage.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Playground from './pages/Playground.jsx'
import DashboardViewPage from './pages/DashboardViewPage.jsx'
import EditorPage from './pages/EditorPage.jsx'
import DocsPage from './pages/DocsPage.jsx'
import ComparePage from './pages/ComparePage.jsx'
import NotFound from './pages/NotFound.jsx'
import ProtectedRoute from './components/ProtectedRoute.jsx'

/**
 * Route structure:
 *
 *   /            → LandingPage          (public, inside MainLayout)
 *   /login       → Login                (public)
 *   /register    → Register             (public)
 *   /docs        → DocsPage             (public)
 *   /docs/:slug  → DocsPage             (public)
 *   /compare     → ComparePage          (public)
 *   /dashboard   → ProtectedRoute > Dashboard
 *   /playground  → ProtectedRoute > Playground
 *   /d/:id       → ProtectedRoute > DashboardViewPage  (spec + HTML fallback)
 *   /editor      → ProtectedRoute > EditorPage         (new board)
 *   /editor/:id  → ProtectedRoute > EditorPage         (edit board)
 *   *            → NotFound             (outside MainLayout)
 */
export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route element={<MainLayout />}>
          <Route index element={<LandingPage />} />
          <Route path="login" element={<Login />} />
          <Route path="register" element={<Register />} />

          {/* Public content routes */}
          <Route path="docs" element={<DocsPage />} />
          <Route path="docs/:slug" element={<DocsPage />} />
          <Route path="compare" element={<ComparePage />} />

          {/* Protected routes */}
          <Route
            path="dashboard"
            element={
              <ProtectedRoute>
                <Dashboard />
              </ProtectedRoute>
            }
          />
          <Route
            path="playground"
            element={
              <ProtectedRoute>
                <Playground />
              </ProtectedRoute>
            }
          />
          <Route
            path="d/:id"
            element={
              <ProtectedRoute>
                <DashboardViewPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="editor"
            element={
              <ProtectedRoute>
                <EditorPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="editor/:id"
            element={
              <ProtectedRoute>
                <EditorPage />
              </ProtectedRoute>
            }
          />
        </Route>

        {/* Catch-all — outside MainLayout so 404 has full viewport */}
        <Route path="*" element={<NotFound />} />
      </Routes>
    </AuthProvider>
  )
}
