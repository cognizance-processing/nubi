import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import api, { setToken } from '../lib/api'

const AuthContext = createContext({})

export const useAuth = () => {
    const context = useContext(AuthContext)
    if (!context) {
        throw new Error('useAuth must be used within an AuthProvider')
    }
    return context
}

export const AuthProvider = ({ children }) => {
    const [user, setUser] = useState(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        const token = localStorage.getItem('nubi_token')
        if (!token) {
            setLoading(false)
            return
        }
        api.auth.me()
            .then(({ user: u }) => setUser(u))
            .catch(() => setToken(null))
            .finally(() => setLoading(false))
    }, [])

    const signUp = useCallback(async (email, password) => {
        const { token, user: u } = await api.auth.signup(email, password)
        setToken(token)
        setUser(u)
    }, [])

    const signIn = useCallback(async (email, password) => {
        const { token, user: u } = await api.auth.signin(email, password)
        setToken(token)
        setUser(u)
    }, [])

    const signInWithGoogle = useCallback(async (code, redirectUri) => {
        const { token, user: u } = await api.auth.google(code, redirectUri)
        setToken(token)
        setUser(u)
    }, [])

    const signOut = useCallback(() => {
        setToken(null)
        setUser(null)
    }, [])

    const value = {
        user,
        loading,
        signUp,
        signIn,
        signInWithGoogle,
        signOut,
    }

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
