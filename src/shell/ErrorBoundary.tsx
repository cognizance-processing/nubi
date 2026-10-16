/**
 * ErrorBoundary — keeps a render exception from taking the whole app with it.
 *
 *   <RouteErrorBoundary>        inside AppShell around <Outlet/>: the sidebar and topbar stay,
 *                               only the page area shows the error. Clears itself when the
 *                               route changes, so "go elsewhere" always works.
 *   <AppErrorBoundary>          full-viewport fallback for layouts that have no shell (public
 *                               pages, auth). Wrap <Routes> with it.
 *
 * Both render an EmptyState (kind "error", role="alert") with the error art, a Retry action and a
 * Go home link. A failed lazy chunk (stale deploy, offline) cannot be fixed by re-rendering, so for
 * those Retry reloads the page. The error is logged once with its component stack.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import EmptyState from '../components/ui/EmptyState.jsx'
import Button from '../components/ui/Button.jsx'
import { ErrorFigure } from '../components/illustrations/EmptyFigures.jsx'

const CHUNK_ERROR = /Loading chunk|Loading CSS chunk|dynamically imported module|Importing a module script failed|error loading dynamically/i

interface Props {
  children: ReactNode
  /** Changing this value clears a caught error (the route path, for the route boundary). */
  resetKey?: string
  /** 'route' fills the page area; 'app' fills the viewport. */
  variant?: 'route' | 'app'
}

interface State { error: Error | null }

class Boundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null })
  }

  private retry = () => {
    if (this.state.error && CHUNK_ERROR.test(String(this.state.error.message))) {
      window.location.reload()
      return
    }
    this.setState({ error: null })
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const app = this.props.variant === 'app'
    const stale = CHUNK_ERROR.test(String(error.message))
    return (
      <div
        className={app ? 'flex min-h-screen items-center justify-center bg-bg text-fg px-4' : 'flex flex-1 min-h-0 items-center justify-center px-4 py-10'}
      >
        <EmptyState
          kind="error"
          art={<ErrorFigure />}
          title="Something broke on this page"
          description={
            stale
              ? 'A part of the app failed to load, probably because it was updated while this tab was open. Reload to get the latest version.'
              : 'The rest of the app is fine and nothing was changed. Try again, or head back home.'
          }
          action={<Button onClick={this.retry}>{stale ? 'Reload page' : 'Try again'}</Button>}
          secondaryAction={
            // A real link, not router navigation: the router tree itself may be what broke.
            <Button variant="ghost" asChild><a href={app ? '/' : '/home'}>Go home</a></Button>
          }
        />
      </div>
    )
  }
}

/** Page-area boundary for AppShell. Resets whenever the path changes. */
export function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  return <Boundary variant="route" resetKey={pathname}>{children}</Boundary>
}

/** Viewport-level boundary for layouts without the shell. */
export function AppErrorBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  return <Boundary variant="app" resetKey={pathname}>{children}</Boundary>
}

export default RouteErrorBoundary
