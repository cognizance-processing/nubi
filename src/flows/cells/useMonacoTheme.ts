import { useContext } from 'react'
import { ThemeContext } from '../../contexts/ThemeContext.jsx'

/** Monaco theme that follows the app theme (a light page must not hold a dark editor). */
export default function useMonacoTheme(): 'vs-dark' | 'light' {
  const theme = useContext(ThemeContext)?.theme ?? 'light'
  return theme === 'dark' ? 'vs-dark' : 'light'
}
