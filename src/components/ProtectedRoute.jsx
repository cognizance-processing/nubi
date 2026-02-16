import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

export default function ProtectedRoute() {
    const { user, loading } = useAuth()

    if (loading) {
        return (
            <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                minHeight: '100vh',
                flexDirection: 'column',
                gap: '1.5rem'
            }}>
                <div style={{
                    width: '48px',
                    height: '48px',
                    border: '3px solid var(--bg-tertiary)',
                    borderTopColor: 'var(--accent-primary)',
                    borderRadius: '50%',
                    animation: 'spin 0.8s linear infinite'
                }}></div>
                <p style={{ color: 'var(--text-secondary)' }}>Loading...</p>
            </div>
        )
    }

    return user ? <Outlet /> : <Navigate to="/login" replace />
}
