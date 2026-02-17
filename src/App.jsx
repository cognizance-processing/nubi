import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './contexts/AuthContext'
import { HeaderProvider } from './contexts/HeaderContext'
import ProtectedRoute from './components/ProtectedRoute'
import MainLayout from './layouts/MainLayout'
import LandingPage from './pages/LandingPage'
import Login from './components/Login'
import BoardsList from './components/BoardsList'
import BoardEditor from './components/BoardEditor'
import QueryEditor from './components/QueryEditor'
import DatastoresPage from './pages/Datastores/DatastoresPage'
import DatastoreDetailPage from './pages/Datastores/DatastoreDetailPage'

export default function App() {
  return (
    <HeaderProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            {/* Public Routes */}
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<Login />} />

            {/* Protected Routes with Main Layout */}
            <Route element={<ProtectedRoute />}>
              <Route element={<MainLayout />}>
                <Route path="/portal" element={<BoardsList />} />
                <Route path="/board/:boardId" element={<BoardEditor />} />
                <Route path="/board/:boardId/query/:queryId" element={<QueryEditor />} />
                <Route path="/datastores" element={<DatastoresPage />} />
                <Route path="/datastores/:datastoreId" element={<DatastoreDetailPage />} />
              </Route>
            </Route>

            {/* Catch all */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </HeaderProvider>
  )
}
