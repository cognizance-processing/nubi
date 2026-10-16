/**
 * Dev-only preview of the PageShell template (header, tabs, sections, toolbar, widths).
 * Open http://localhost:5173/src/shell/_preview/index.html?theme=dark . Not part of the app build.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import '../../index.css'
import { UiProvider } from '../../contexts/UiContext.jsx'
import PageShell, { PageSection, Toolbar, SearchBar } from '../../components/app/PageShell.jsx'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'

const width = (new URLSearchParams(location.search).get('width') as 'default' | 'narrow' | 'full') || 'default'

function Demo() {
  return (
    <div style={{ background: 'var(--bg)', minHeight: '100vh', padding: 8 }}>
      <div style={{ background: 'var(--surface)', border: '1px solid var(--frame-line)', borderRadius: 4, minHeight: 'calc(100vh - 16px)' }}>
        <PageShell
          eyebrow="11 DASHBOARDS"
          title="Dashboards"
          description="Boards your team publishes, with live data and per-viewer filters."
          width={width}
          actions={<><Button variant="secondary" size="sm">Import</Button><Button size="sm">New dashboard</Button></>}
          tabs={[{ id: 'all', label: 'All', count: 11, href: '/dashboards' }, { id: 'mine', label: 'Mine', count: 3, href: '/dashboards/mine' }, { id: 'shared', label: 'Shared' }]}
          activeTab="all"
          toolbar={<Toolbar><SearchBar value="" onChange={() => {}} placeholder="Search dashboards" /></Toolbar>}
        >
          <PageSection index="01" title="Members" description="Who can open this org." actions={<Button size="sm" variant="secondary">Invite</Button>}>
            <p style={{ margin: 0 }}>Section body <Tag variant="success" dot>Live</Tag></p>
          </PageSection>
          <PageSection index="02" title="Danger zone"><p style={{ margin: 0 }}>Another section.</p></PageSection>
        </PageShell>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><MemoryRouter initialEntries={['/dashboards']}><UiProvider><Demo /></UiProvider></MemoryRouter></StrictMode>,
)
