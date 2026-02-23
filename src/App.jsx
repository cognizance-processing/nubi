import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './contexts/AuthContext'
import { OrgProvider } from './contexts/OrgContext'
import { HeaderProvider } from './contexts/HeaderContext'
import ProtectedRoute from './components/ProtectedRoute'
import MainLayout from './layouts/MainLayout'
import LandingPage from './pages/LandingPage'
import DocsPage from './pages/DocsPage'
import HomePage from './pages/HomePage'
import Login from './components/Login'
import BoardsList from './components/BoardsList'
import BoardEditor from './components/BoardEditor'
import QueryEditor from './components/QueryEditor'
import DatastoresPage from './pages/Datastores/DatastoresPage'
import DatastoreDetailPage from './pages/Datastores/DatastoreDetailPage'
import WidgetsPage from './pages/Widgets/WidgetsPage'
import WidgetDetailPage from './pages/Widgets/WidgetDetailPage'
import UsagePage from './pages/UsagePage'

export default function App() {
  return (
    <HeaderProvider>
      <AuthProvider>
        <OrgProvider>
        <BrowserRouter>
          <Routes>
            {/* Public Routes */}
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<Login />} />
            <Route path="/docs" element={<DocsPage />} />
            <Route path="/docs/*" element={<DocsPage />} />

            {/* Legacy redirects */}
            <Route path="/terms" element={<Navigate to="/docs/terms" replace />} />
            <Route path="/privacy" element={<Navigate to="/docs/privacy" replace />} />

            {/* Protected Routes with Main Layout */}
            <Route element={<ProtectedRoute />}>
              <Route element={<MainLayout />}>
                <Route path="/portal" element={<HomePage />} />
                <Route path="/boards" element={<BoardsList />} />
                <Route path="/board/:boardId" element={<BoardEditor />} />
                <Route path="/board/:boardId/query/:queryId" element={<QueryEditor />} />
                <Route path="/datastores" element={<DatastoresPage />} />
                <Route path="/datastores/:datastoreId" element={<DatastoreDetailPage />} />
                <Route path="/widgets" element={<WidgetsPage />} />
                <Route path="/widgets/:widgetId" element={<WidgetDetailPage />} />
                <Route path="/usage" element={<UsagePage />} />
              </Route>
            </Route>

            {/* Catch all */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
        </OrgProvider>
      </AuthProvider>
    </HeaderProvider>
  )
}
