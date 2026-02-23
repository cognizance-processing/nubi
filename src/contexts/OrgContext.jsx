import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import api from '../lib/api'
import { useAuth } from './AuthContext'

const OrgContext = createContext({})

const ORG_KEY = 'nubi_current_org'

export const useOrg = () => {
    const context = useContext(OrgContext)
    if (!context) throw new Error('useOrg must be used within an OrgProvider')
    return context
}

export const OrgProvider = ({ children }) => {
    const { user } = useAuth()
    const [organizations, setOrganizations] = useState([])
    const [currentOrg, setCurrentOrgState] = useState(null)
    const [loading, setLoading] = useState(true)

    const setCurrentOrg = useCallback((org) => {
        setCurrentOrgState(org)
        if (org) localStorage.setItem(ORG_KEY, org.id)
        else localStorage.removeItem(ORG_KEY)
    }, [])

    const fetchOrgs = useCallback(async () => {
        try {
            const orgs = await api.organizations.list()
            setOrganizations(orgs)

            const savedId = localStorage.getItem(ORG_KEY)
            const saved = orgs.find((o) => o.id === savedId)
            setCurrentOrg(saved || orgs[0] || null)
        } catch (err) {
            console.error('Failed to load organizations:', err)
        } finally {
            setLoading(false)
        }
    }, [setCurrentOrg])

    useEffect(() => {
        if (user) fetchOrgs()
        else {
            setOrganizations([])
            setCurrentOrgState(null)
            setLoading(false)
        }
    }, [user, fetchOrgs])

    const createOrg = useCallback(async (name) => {
        const org = await api.organizations.create(name)
        setOrganizations((prev) => [...prev, org])
        return org
    }, [])

    const renameOrg = useCallback(async (id, name) => {
        await api.organizations.update(id, name)
        setOrganizations((prev) =>
            prev.map((o) => (o.id === id ? { ...o, name } : o))
        )
        setCurrentOrgState((prev) => (prev?.id === id ? { ...prev, name } : prev))
    }, [])

    return (
        <OrgContext.Provider value={{
            organizations,
            currentOrg,
            setCurrentOrg,
            loading,
            createOrg,
            renameOrg,
            refetchOrgs: fetchOrgs,
        }}>
            {children}
        </OrgContext.Provider>
    )
}
