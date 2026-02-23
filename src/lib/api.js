const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
const TOKEN_KEY = 'nubi_token'

function getToken() {
    return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
}

async function request(method, path, body = null, opts = {}) {
    const headers = { ...(opts.headers || {}) }
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`

    const config = { method, headers }

    if (body instanceof FormData) {
        config.body = body
    } else if (body !== null) {
        headers['Content-Type'] = 'application/json'
        config.body = JSON.stringify(body)
    }

    const res = await fetch(`${BACKEND_URL}${path}`, config)
    if (res.status === 401) {
        setToken(null)
        window.location.href = '/login'
        throw new Error('Session expired')
    }
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Request failed')
    return data
}

const api = {
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, body),
    patch: (path, body) => request('PATCH', path, body),
    delete: (path) => request('DELETE', path),

    auth: {
        signup: (email, password, full_name) => request('POST', '/auth/signup', { email, password, full_name }),
        signin: (email, password) => request('POST', '/auth/signin', { email, password }),
        google: (code, redirect_uri) => request('POST', '/auth/google', { code, redirect_uri }),
        me: () => request('GET', '/auth/me'),
    },

    organizations: {
        list: () => request('GET', '/organizations'),
        create: (name) => request('POST', '/organizations', { name }),
        update: (id, name) => request('PATCH', `/organizations/${id}`, { name }),
    },

    boards: {
        list: (orgId) => request('GET', `/boards?organization_id=${orgId}`),
        create: (data) => request('POST', '/boards', data),
        get: (id) => request('GET', `/boards/${id}`),
        update: (id, data) => request('PATCH', `/boards/${id}`, data),
        getCode: (id) => request('GET', `/boards/${id}/code`),
        saveCode: (id, code) => request('POST', `/boards/${id}/code`, { code }),
        listQueries: (id) => request('GET', `/boards/${id}/queries`),
        createQuery: (id, data) => request('POST', `/boards/${id}/queries`, data),
    },

    queries: {
        get: (id) => request('GET', `/queries/${id}`),
        update: (id, data) => request('PATCH', `/queries/${id}`, data),
        delete: (id) => request('DELETE', `/queries/${id}`),
    },

    datastores: {
        list: (orgId) => request('GET', orgId ? `/datastores?organization_id=${orgId}` : '/datastores'),
        create: (data) => request('POST', '/datastores', data),
        get: (id) => request('GET', `/datastores/${id}`),
        update: (id, data) => request('PATCH', `/datastores/${id}`, data),
        delete: (id) => request('DELETE', `/datastores/${id}`),
    },

    chats: {
        list: (boardId, orgId) => {
            const params = new URLSearchParams()
            if (boardId) params.set('board_id', boardId)
            if (orgId) params.set('organization_id', orgId)
            const qs = params.toString()
            return request('GET', qs ? `/chats?${qs}` : '/chats')
        },
        create: (data) => request('POST', '/chats', data),
        update: (id, title) => request('PATCH', `/chats/${id}`, { title }),
        listMessages: (id) => request('GET', `/chats/${id}/messages`),
        createMessage: (id, role, content) => request('POST', `/chats/${id}/messages`, { role, content }),
    },

    widgets: {
        list: (orgId) => request('GET', orgId ? `/widgets?organization_id=${orgId}` : '/widgets'),
        create: (data) => request('POST', '/widgets', data),
        get: (id) => request('GET', `/widgets/${id}`),
        update: (id, data) => request('PATCH', `/widgets/${id}`, data),
        delete: (id) => request('DELETE', `/widgets/${id}`),
    },

    models: {
        list: () => request('GET', '/models'),
    },

    usage: {
        summary: (days = 30) => request('GET', `/usage?days=${days}`),
        details: (days = 7, limit = 100) => request('GET', `/usage/details?days=${days}&limit=${limit}`),
        daily: (days = 30) => request('GET', `/usage/daily?days=${days}`),
    },

    stats: (orgId) => request('GET', `/stats?organization_id=${orgId}`),

    upload: async (file) => {
        const form = new FormData()
        form.append('file', file)
        const token = getToken()
        const headers = {}
        if (token) headers['Authorization'] = `Bearer ${token}`
        const res = await fetch(`${BACKEND_URL}/upload/keyfile`, { method: 'POST', headers, body: form })
        if (!res.ok) { const d = await res.json(); throw new Error(d.detail || 'Upload failed') }
        return res.json()
    },
}

export default api

export async function invokeBoardHelper(options) {
    const { code = '', user_prompt, chat = [], gemini_api_key, context = 'board', datastore_id, exploration_id } = options
    try {
        const response = await fetch(`${BACKEND_URL}/board-helper`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                code, user_prompt, chat, context,
                ...(datastore_id && { datastore_id }),
                ...(exploration_id && { exploration_id }),
                ...(gemini_api_key && { gemini_api_key })
            })
        })
        const data = await response.json()
        if (!response.ok) return { error: new Error(data.detail || 'AI helper request failed'), data: null }
        return { data, error: null }
    } catch (err) {
        return { error: err, data: null }
    }
}

export async function getSchema(options) {
    const { datastore_id, connector_id, database, table } = options
    try {
        const response = await fetch(`${BACKEND_URL}/get-schema`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                datastore_id: datastore_id || connector_id,
                connector_id: connector_id || datastore_id,
                database, table
            })
        })
        const data = await response.json()
        if (!response.ok) return { error: new Error(data.detail || 'Schema request failed'), data: null }
        return { data, error: null }
    } catch (err) {
        return { error: err, data: null }
    }
}

export const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || ''
