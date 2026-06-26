import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ThemeProvider } from './contexts/ThemeContext.jsx'
import App from './App.jsx'
import { Toaster } from './components/ui/Toast.jsx'
import './index.css'

// ---------------------------------------------------------------------------
// Embed component registration — register the shared web components so the
// SPA can consume them (dogfood: the app is its own SDK).  Import is
// side-effect-only; registration is idempotent.
// ---------------------------------------------------------------------------
import '../embed/widgets/authoring-index.js'
import '../embed/widgets/nubi-explain.js'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <App />
        <Toaster />
      </ThemeProvider>
    </BrowserRouter>
  </StrictMode>,
)
