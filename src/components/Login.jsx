import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { GOOGLE_CLIENT_ID } from '../lib/api'

export default function Login() {
    const navigate = useNavigate()
    const { user, signUp, signIn, signInWithGoogle } = useAuth()
    const [searchParams] = useSearchParams()
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [isSignUp, setIsSignUp] = useState(false)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState(null)
    const [message, setMessage] = useState(null)

    useEffect(() => {
        if (user) navigate('/portal', { replace: true })
    }, [user, navigate])

    useEffect(() => {
        const code = searchParams.get('code')
        if (code && !loading) {
            setLoading(true)
            const redirectUri = `${window.location.origin}/login`
            signInWithGoogle(code, redirectUri)
                .catch((err) => setError(err.message))
                .finally(() => setLoading(false))
        }
    }, [searchParams])

    const handleAuth = async (e) => {
        e.preventDefault()
        setLoading(true)
        setError(null)
        setMessage(null)

        try {
            if (isSignUp) {
                await signUp(email, password)
                setMessage('Account created! You are now logged in.')
            } else {
                await signIn(email, password)
            }
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    const handleGoogleSignIn = () => {
        if (!GOOGLE_CLIENT_ID) {
            setError('Google OAuth is not configured')
            return
        }
        const redirectUri = `${window.location.origin}/login`
        const params = new URLSearchParams({
            client_id: GOOGLE_CLIENT_ID,
            redirect_uri: redirectUri,
            response_type: 'code',
            scope: 'email profile',
            access_type: 'offline',
            prompt: 'select_account',
        })
        window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params}`
    }

    return (
        <div className="relative min-h-screen flex items-center justify-center p-4 bg-slate-950 overflow-hidden">
            {/* Background glow */}
            <div className="absolute inset-0 pointer-events-none">
                <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-indigo-600/[0.07] blur-[150px] rounded-full" />
                <div className="absolute top-0 left-0 w-[400px] h-[400px] bg-purple-600/[0.05] blur-[120px] rounded-full" />
                <div className="absolute bottom-0 right-0 w-[300px] h-[300px] bg-indigo-500/[0.04] blur-[100px] rounded-full" />
            </div>

            <div className="w-full max-w-[400px] relative z-10 animate-fade-in">
                {/* Logo + heading */}
                <div className="flex flex-col items-center mb-8">
                    <div className="w-20 h-20 rounded-2xl overflow-hidden shadow-2xl shadow-indigo-500/20 ring-1 ring-white/10 mb-5">
                        <img
                            src="/nubi.png"
                            alt="Nubi"
                            className="w-full h-full object-cover"
                        />
                    </div>
                    <h1 className="text-3xl font-bold text-white tracking-tight mb-1">
                        {isSignUp ? 'Create an account' : 'Welcome back'}
                    </h1>
                    <p className="text-sm text-slate-500">
                        {isSignUp
                            ? 'Get started with Nubi for free'
                            : 'Sign in to your Nubi account'}
                    </p>
                </div>

                {/* Card */}
                <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] backdrop-blur-sm p-8 shadow-2xl shadow-black/30">
                    {/* Google button first for prominence */}
                    <button
                        type="button"
                        className="w-full flex items-center justify-center gap-3 px-4 py-2.5 rounded-xl bg-white/[0.05] border border-white/[0.08] text-sm font-medium text-slate-200 transition hover:bg-white/[0.1] hover:border-white/[0.14] active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                        onClick={handleGoogleSignIn}
                        disabled={loading}
                    >
                        <svg width="18" height="18" viewBox="0 0 24 24">
                            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
                            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
                            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
                            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
                        </svg>
                        Continue with Google
                    </button>

                    {/* Divider */}
                    <div className="relative my-6">
                        <div className="absolute inset-0 flex items-center">
                            <div className="w-full border-t border-white/[0.06]" />
                        </div>
                        <div className="relative flex justify-center">
                            <span className="px-3 text-[11px] uppercase tracking-widest font-semibold text-slate-600 bg-[#0b0f1a]">
                                or
                            </span>
                        </div>
                    </div>

                    {/* Alerts */}
                    {error && (
                        <div className="mb-5 px-4 py-3 bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl flex items-start gap-2.5 text-[13px] animate-fade-in">
                            <svg className="w-4 h-4 mt-0.5 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" />
                            </svg>
                            {error}
                        </div>
                    )}

                    {message && (
                        <div className="mb-5 px-4 py-3 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl flex items-start gap-2.5 text-[13px] animate-fade-in">
                            <svg className="w-4 h-4 mt-0.5 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" />
                            </svg>
                            {message}
                        </div>
                    )}

                    {/* Form */}
                    <form onSubmit={handleAuth} className="flex flex-col gap-4">
                        <div className="flex flex-col gap-1.5">
                            <label className="text-xs font-medium text-slate-400" htmlFor="email">
                                Email address
                            </label>
                            <input
                                id="email"
                                className="w-full px-3.5 py-2.5 bg-white/[0.03] border border-white/[0.08] rounded-xl text-sm text-slate-200 transition placeholder:text-slate-600 hover:border-white/[0.12] focus:border-indigo-500/50 focus:bg-white/[0.04] focus:outline-none focus:ring-1 focus:ring-indigo-500/20"
                                type="email"
                                placeholder="you@example.com"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                required
                                disabled={loading}
                                autoComplete="email"
                            />
                        </div>

                        <div className="flex flex-col gap-1.5">
                            <label className="text-xs font-medium text-slate-400" htmlFor="password">
                                Password
                            </label>
                            <input
                                id="password"
                                className="w-full px-3.5 py-2.5 bg-white/[0.03] border border-white/[0.08] rounded-xl text-sm text-slate-200 transition placeholder:text-slate-600 hover:border-white/[0.12] focus:border-indigo-500/50 focus:bg-white/[0.04] focus:outline-none focus:ring-1 focus:ring-indigo-500/20"
                                type="password"
                                placeholder="••••••••"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                required
                                disabled={loading}
                                autoComplete={isSignUp ? 'new-password' : 'current-password'}
                            />
                        </div>

                        <button
                            type="submit"
                            className="w-full mt-1 px-4 py-2.5 rounded-xl bg-indigo-600 text-white text-sm font-semibold shadow-lg shadow-indigo-600/20 transition hover:bg-indigo-500 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                            disabled={loading}
                        >
                            {loading ? (
                                <div className="flex items-center justify-center">
                                    <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                </div>
                            ) : (
                                isSignUp ? 'Create account' : 'Sign in'
                            )}
                        </button>
                    </form>
                </div>

                {/* Toggle */}
                <div className="mt-6 text-center">
                    <span className="text-sm text-slate-600">
                        {isSignUp ? 'Already have an account?' : "Don't have an account?"}{' '}
                    </span>
                    <button
                        type="button"
                        className="text-sm font-medium text-indigo-400 hover:text-indigo-300 transition-colors cursor-pointer"
                        onClick={() => {
                            setIsSignUp(!isSignUp)
                            setError(null)
                            setMessage(null)
                        }}
                        disabled={loading}
                    >
                        {isSignUp ? 'Sign in' : 'Sign up'}
                    </button>
                </div>
            </div>
        </div>
    )
}
