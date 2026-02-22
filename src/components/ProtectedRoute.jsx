import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

export default function ProtectedRoute() {
    const { user, loading } = useAuth()

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-screen flex-col gap-4 bg-slate-950">
                <div className="spinner" />
                <p className="text-slate-400 text-sm">Loading...</p>
            </div>
        )
    }

    return user ? <Outlet /> : <Navigate to="/login" replace />
}
