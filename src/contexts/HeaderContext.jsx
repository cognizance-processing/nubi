import { createContext, useContext, useState } from 'react'

const HeaderContext = createContext()

export function HeaderProvider({ children }) {
    const [headerContent, setHeaderContent] = useState(null)

    return (
        <HeaderContext.Provider value={{ headerContent, setHeaderContent }}>
            {children}
        </HeaderContext.Provider>
    )
}

export function useHeader() {
    const context = useContext(HeaderContext)
    if (!context) {
        throw new Error('useHeader must be used within a HeaderProvider')
    }
    return context
}
