/**
 * Design-system gallery. DEV ONLY: open /src/components/ui/_gallery/index.html on
 * the vite dev server (add ?theme=dark for dark). Not part of the production
 * build (the build only reads the root index.html). Every primitive, every state.
 */
import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Search, Eye, Plus, Trash2, Settings, LayoutGrid, List, Inbox, Lock } from 'lucide-react'
import '../../../index.css'
import {
  Button, Tag, Badge, Input, Select, Textarea, Field, Checkbox, Card, CardHeader, CardTitle,
  CardDescription, CardBody, CardFooter, Spinner, Skeleton, BrandLoader, Modal, Tooltip, Toaster, toast,
  Table, EmptyState, Tabs, TabsList, TabsTrigger, TabsContent, SegmentedControl,
} from '../index'
import Logo, { NUBI_MARK_SVG } from '../../Logo'

const TOKENS = [
  ['--bg', 'frame'], ['--surface', 'sheet'], ['--surface-2', 'sunk'], ['--surface-3', 'pressed'],
  ['--border', 'hairline'], ['--frame-line', 'frame-line'], ['--border-control', 'control edge'],
  ['--text', 'ink'], ['--text-muted', 'muted'], ['--text-subtle', 'subtle'], ['--accent', 'accent'], ['--accent-soft', 'accent-soft'],
  ['--success', 'success'], ['--warning', 'warning'], ['--danger', 'danger'], ['--danger-solid', 'danger-solid'],
  ['--viz-1', 'viz-1'], ['--viz-2', 'viz-2'], ['--viz-3', 'viz-3'], ['--viz-4', 'viz-4'], ['--viz-5', 'viz-5'], ['--viz-6', 'viz-6'], ['--viz-7', 'viz-7'], ['--viz-8', 'viz-8'],
]

const ROWS = [
  { id: 1, name: 'Revenue by region', region: 'AMER', orders: 12480, amount: 1284930.5, status: 'Live' },
  { id: 2, name: 'Weekly retention', region: 'EMEA', orders: 8310, amount: 904118.0, status: 'Draft' },
  { id: 3, name: 'Basket size', region: 'APAC', orders: 15702, amount: 1530004.25, status: 'Live' },
  { id: 4, name: 'Inventory turns', region: 'LATAM', orders: 2210, amount: 211003.9, status: 'Error' },
]

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="border-t-2 border-ink pt-3 mt-12">
      <div className="flex items-baseline gap-3 mb-5">
        <span className="t-label">{id}</span>
        <h2 className="t-section">{title}</h2>
      </div>
      {children}
    </section>
  )
}
const Row = ({ label, children }: { label?: string; children: React.ReactNode }) => (
  <div className="mb-4">
    {label && <div className="t-label mb-2">{label}</div>}
    <div className="flex flex-wrap items-center gap-3">{children}</div>
  </div>
)

function Gallery() {
  const [open, setOpen] = useState(() => new URLSearchParams(location.search).get('modal') === '1')
  const [seg, setSeg] = useState('grid')
  const [seg2, setSeg2] = useState('week')
  const [sel, setSel] = useState<any[]>([2])
  const [sortKey, setSortKey] = useState('amount')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [tab, setTab] = useState('all')
  const rows = [...ROWS].sort((a: any, b: any) => (sortDir === 'asc' ? 1 : -1) * (a[sortKey] < b[sortKey] ? -1 : 1))
  const fmt = (n: number) => n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })

  return (
    <div className="mx-auto max-w-[1100px] px-4 md:px-8 pb-24 pt-8">
      <header className="flex items-center justify-between gap-4">
        <Logo size={26} />
        <span className="t-label">Design system / v1</span>
      </header>

      <Section id="01" title="Colour tokens">
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
          {TOKENS.map(([v, n]) => (
            <div key={v} className="border border-border">
              <div style={{ background: `var(${v})`, height: 40, borderBottom: '1px solid var(--border)' }} />
              <div className="p-2"><div className="text-xs font-medium">{n}</div><div className="t-label" style={{ textTransform: 'none' }}>{v}</div></div>
            </div>
          ))}
        </div>
        <Row label="Text on sheet / frame">
          <span className="text-fg">ink</span><span className="text-muted">muted</span><span className="text-subtle">subtle</span><span className="text-accent">accent link</span>
          <span className="px-2 py-1 bg-bg"><span className="text-subtle">subtle on frame</span></span>
        </Row>
      </Section>

      <Section id="02" title="Type scale">
        <div className="space-y-4">
          <div className="t-display">BI that runs in your browser.</div>
          <div className="t-h2">Viewers are free.</div>
          <div className="t-h3">Rules, not boxes. Type, not decoration.</div>
          <p className="t-lead">Open-source BI for teams that put dashboards in front of a lot of people. The query engine runs in each viewer’s tab.</p>
          <div className="t-page">Dashboards</div>
          <div className="t-section">Recent boards</div>
          <p className="t-body">Body 14/1.5. The quick brown fox jumps over the lazy dog. 1,284,930 orders across 4 regions.</p>
          <p className="t-small text-muted">Small 13/1.45 secondary copy.</p>
          <div className="t-label">Mono label 11 / tracking +0.06em</div>
          <div className="t-num text-lg">1,284,930.50 &nbsp; 0123456789</div>
        </div>
      </Section>

      <Section id="03" title="Button">
        {(['primary', 'secondary', 'ghost', 'outline', 'danger'] as const).map((v) => (
          <Row key={v} label={v}>
            <Button variant={v} size="xs">Label</Button>
            <Button variant={v} size="sm">Label</Button>
            <Button variant={v} size="md">Label</Button>
            <Button variant={v} size="lg">Label</Button>
            <Button variant={v} size="md"><Plus size={16} /> With icon</Button>
            <Button variant={v} size="md" disabled>Disabled</Button>
            <Button variant={v} size="md" loading>Loading</Button>
            <Button variant={v} size="icon" aria-label="Settings"><Settings size={16} /></Button>
            <Button variant={v} size="icon-sm" aria-label="Delete"><Trash2 size={16} /></Button>
          </Row>
        ))}
        <Row label="xl (marketing)"><Button size="xl">Start free</Button><Button size="xl" variant="secondary">Run it in this tab</Button></Row>
        <Row label="forced states (hover / active / focus captured by script)">
          <Button id="btn-hover">Hover me</Button>
          <Button id="btn-focus" variant="secondary">Focus me</Button>
          <Button id="btn-active" variant="outline">Press me</Button>
        </Row>
      </Section>

      <Section id="04" title="Tag and Badge">
        <Row label="Tag variants">
          <Tag>Neutral</Tag><Tag variant="ink">Most popular</Tag><Tag variant="accent">Accent</Tag>
          <Tag variant="success" dot>Live</Tag><Tag variant="warning" dot>Stale</Tag><Tag variant="danger" dot>Not in prod</Tag><Tag dot>Draft</Tag>
        </Row>
        <Row label="md chips (removable, user-authored names)">
          <Tag size="md" caps={false} onRemove={() => {}}>Region is EMEA</Tag>
          <Tag size="md" caps={false} onRemove={() => {}}>Last 30 days</Tag>
          <Tag size="md" caps={false} as="button">Retail</Tag>
        </Row>
        <Row label="Badge (legacy wrapper)"><Badge>default</Badge><Badge variant="success" dot>success</Badge><Badge variant="danger" size="sm">danger</Badge><Badge variant="primary">primary</Badge><Badge variant="warning" size="lg">warning</Badge></Row>
      </Section>

      <Section id="05" title="Form fields">
        <div className="grid md:grid-cols-2 gap-6">
          <div className="space-y-4">
            <Field label="Email" hint="We never share this."><Input type="email" placeholder="you@example.com" /></Field>
            <Field label="Search" ><Input icon={<Search size={16} />} placeholder="Search dashboards" /></Field>
            <Field label="Password" ><Input type="password" defaultValue="hunter2hunter2" trailing={<Button variant="ghost" size="icon-xs" aria-label="Show password"><Eye size={16} /></Button>} /></Field>
            <Field label="With error" error="Enter a valid email address."><Input defaultValue="not-an-email" error /></Field>
            <Field label="Disabled"><Input disabled defaultValue="Read only value" /></Field>
            <Field label="Select"><Select options={['Last 7 days', 'Last 30 days', 'Last 90 days']} /></Field>
            <Field label="Notes"><Textarea rows={3} placeholder="Notes…" /></Field>
          </div>
          <div className="space-y-4">
            {(['xs', 'sm', 'md', 'lg', 'xl'] as const).map((s) => (
              <div key={s} className="flex items-center gap-2">
                <span className="t-label w-8">{s}</span>
                <Input size={s} placeholder={`Input ${s}`} icon={<Search size={s === 'xs' ? 14 : 16} />} />
                <Button size={s === 'xs' ? 'xs' : s === 'sm' ? 'sm' : s === 'md' ? 'md' : s === 'lg' ? 'lg' : 'xl'}>Go</Button>
              </div>
            ))}
            <div className="flex flex-col gap-3 pt-2">
              <Checkbox label="Unchecked" /><Checkbox label="Checked" defaultChecked /><Checkbox label="Indeterminate" indeterminate /><Checkbox label="Disabled" disabled />
              <div className="flex items-center gap-3"><button role="switch" aria-checked="false" aria-label="Off" className="nubi-switch"><span className="nubi-switch-thumb" /></button><button role="switch" aria-checked="true" aria-label="On" className="nubi-switch"><span className="nubi-switch-thumb" /></button></div>
            </div>
            <div className="nubi-input-icon-wrap"><input className="nubi-input" type="password" placeholder="legacy wrapper: right icon only, no left indent" /><button className="nubi-input-icon-right" aria-label="Show"><Eye size={16} /></button></div>
          </div>
        </div>
      </Section>

      <Section id="06" title="Tabs, segmented control">
        <Row label="Underline tabs">
          <div className="w-full"><Tabs value={tab} onValueChange={setTab}><TabsList><TabsTrigger value="all" badge={11}>All dashboards</TabsTrigger><TabsTrigger value="mine" badge={4}>Mine</TabsTrigger><TabsTrigger value="shared">Shared</TabsTrigger><TabsTrigger value="x" disabled>Archived</TabsTrigger></TabsList><TabsContent value="all"><p className="py-3 text-small text-muted">Panel content.</p></TabsContent></Tabs></div>
        </Row>
        <Row label="Chip tabs"><Tabs defaultValue="a" variant="pills"><TabsList><TabsTrigger value="a" badge={3}>All</TabsTrigger><TabsTrigger value="b">Active</TabsTrigger><TabsTrigger value="c">Paused</TabsTrigger></TabsList></Tabs></Row>
        <Row label="Segmented (sm / md / lg)">
          <SegmentedControl size="sm" value={seg2} onChange={setSeg2} options={[{ value: 'day', label: 'Day' }, { value: 'week', label: 'Week' }, { value: 'month', label: 'Month' }]} />
          <SegmentedControl value={seg} onChange={setSeg} options={[{ value: 'grid', label: <LayoutGrid size={14} />, ariaLabel: 'Grid' }, { value: 'list', label: <List size={14} />, ariaLabel: 'List' }]} />
          <SegmentedControl size="lg" value={seg2} onChange={setSeg2} options={[{ value: 'day', label: 'Day' }, { value: 'week', label: 'Week' }, { value: 'month', label: 'Month' }]} />
        </Row>
      </Section>

      <Section id="07" title="Table">
        <Table
          selectable selected={sel} onSelectedChange={setSel} caption="Dashboards"
          sortKey={sortKey} sortDir={sortDir} onSort={(k, d) => { setSortKey(k); setSortDir(d) }}
          columns={[
            { key: 'name', label: 'Name', sortable: true },
            { key: 'region', label: 'Region' },
            { key: 'orders', label: 'Orders', numeric: true, sortable: true, render: (v) => v.toLocaleString('en-US') },
            { key: 'amount', label: 'Revenue', numeric: true, sortable: true, render: (v) => fmt(v) },
            { key: 'status', label: 'Status', render: (v) => <Tag dot variant={v === 'Live' ? 'success' : v === 'Error' ? 'danger' : 'neutral'}>{v}</Tag> },
          ]}
          rows={rows}
        />
        <div className="grid md:grid-cols-2 gap-6 mt-6">
          <div><div className="t-label mb-2">compact 28px</div><Table density="compact" columns={[{ key: 'name', label: 'Name' }, { key: 'orders', label: 'Orders', numeric: true }]} rows={ROWS} /></div>
          <div><div className="t-label mb-2">loading</div><Table loading columns={[{ key: 'name', label: 'Name' }, { key: 'orders', label: 'Orders', numeric: true }]} rows={[]} /></div>
        </div>
        <div className="mt-6" style={{ maxWidth: 360 }}><div className="t-label mb-2">sticky first column in 360px</div><Table stickyFirstColumn columns={[{ key: 'name', label: 'Name' }, { key: 'region', label: 'Region' }, { key: 'orders', label: 'Orders', numeric: true }, { key: 'amount', label: 'Revenue', numeric: true, render: (v) => fmt(v) }]} rows={ROWS} /></div>
      </Section>

      <Section id="08" title="Card, Skeleton, Spinner">
        <div className="grid md:grid-cols-3 gap-4">
          <Card><CardTitle>Plain card</CardTitle><CardDescription>No shadow at rest.</CardDescription></Card>
          <Card raised><CardTitle>Raised card</CardTitle><CardDescription>2px ink top rule.</CardDescription></Card>
          <Card interactive onClick={() => {}}><CardTitle>Interactive card</CardTitle><CardDescription>Border turns ink on hover.</CardDescription></Card>
        </div>
        <Card className="mt-4" noPadding><CardHeader><CardTitle>Header + footer</CardTitle></CardHeader><CardBody>Body content.</CardBody><CardFooter><Button variant="secondary" size="sm">Cancel</Button><Button size="sm">Save</Button></CardFooter></Card>
        <div className="grid md:grid-cols-3 gap-6 mt-6">
          <div><div className="t-label mb-2">Skeleton.Kpi + Text</div><Skeleton.Kpi /><div className="mt-4"><Skeleton.Text lines={3} /></div></div>
          <div className="md:col-span-2"><div className="t-label mb-2">Skeleton.Chart</div><Skeleton.Chart height={180} /></div>
          <div className="md:col-span-3"><div className="t-label mb-2">Skeleton.Rows / .Table</div><div className="grid md:grid-cols-2 gap-6"><Skeleton.Rows rows={3} /><Skeleton.Table rows={3} cols={4} /></div></div>
        </div>
        <Row label="Spinner xs, sm, md, lg, xl, accent">
          {(['xs', 'sm', 'md', 'lg', 'xl'] as const).map((s) => <Spinner key={s} size={s} />)}<Spinner accent size="lg" />
        </Row>
      </Section>

      <Section id="09" title="Empty states">
        <div className="grid md:grid-cols-2 gap-4">
          <EmptyState bordered title="No dashboards yet" description="A dashboard is a set of charts that share filters. Start from a saved query." icon={<Inbox size={20} />} action={<Button><Plus size={16} /> Create dashboard</Button>} secondaryAction={<Button variant="ghost">Read the guide</Button>} />
          <EmptyState bordered kind="none-match" title="No dashboards match “q3”" description="Try a shorter search, or clear the filter." icon={<Search size={20} />} action={<Button variant="secondary">Clear filter</Button>} />
          <EmptyState bordered kind="gated" title="Flows are on the Pro plan" description="Schedule SQL and Python runs with retries and alerts." icon={<Lock size={20} />} action={<Button>View plans</Button>} />
          <EmptyState bordered kind="error" title="This list couldn’t load" description="The API returned an error. Nothing was changed." requestId="req_8f31c2a9" action={<Button>Retry</Button>} secondaryAction={<Button variant="ghost">Open in editor</Button>} />
        </div>
        <div className="mt-4 border border-border"><EmptyState compact title="No rows" description="The query returned nothing." /></div>
      </Section>

      <Section id="10" title="Overlays: Modal, Tooltip, Toast, dropdown">
        <Row>
          <Button onClick={() => setOpen(true)}>Open modal</Button>
          <Tooltip content="Search" kbd={['⌘', 'K']}><Button variant="secondary" size="icon" aria-label="Search"><Search size={16} /></Button></Tooltip>
          <Tooltip content="Settings" side="bottom"><Button id="tip-trigger" variant="secondary" size="icon" aria-label="Settings"><Settings size={16} /></Button></Tooltip>
          <Button variant="secondary" onClick={() => toast.success('Dashboard saved', { duration: 0 })}>Success toast</Button>
          <Button variant="secondary" onClick={() => toast.error('The query timed out after 30s', { title: 'Run failed', duration: 0, action: { label: 'Retry', onClick: () => {} } })}>Error toast</Button>
          <Button variant="secondary" onClick={() => toast.warning('You are at 92% of your plan', { duration: 0 })}>Warning</Button>
        </Row>
        <div className="relative h-44 mt-2"><div className="nubi-dropdown" style={{ left: 0, top: 0 }}><button className="nubi-dropdown-item">Rename</button><button className="nubi-dropdown-item">Duplicate</button><div className="nubi-dropdown-divider" /><button className="nubi-dropdown-item danger">Delete</button></div>
          <div className="nubi-notice nubi-notice-warning" style={{ position: 'absolute', left: 220, top: 0, right: 0 }}>Warning banner: the one place a tinted wash is allowed.</div>
          <div className="nubi-notice nubi-notice-error" style={{ position: 'absolute', left: 220, top: 56, right: 0 }}>Error banner with a 2px left rule.</div>
        </div>
      </Section>

      <Section id="11" title="Logo, mark, loader">
        <Row label="Mark at 16, 20, 24, 32, 48, 96">
          {[16, 20, 24, 32, 48, 96].map((s) => <Logo key={s} size={s} variant="mark" />)}
        </Row>
        <Row label="Lockup at 18, 22, 30, 40">{[18, 22, 30, 40].map((s) => <Logo key={s} size={s} />)}</Row>
        <Row label="Wordmark"><Logo size={30} variant="wordmark" /></Row>
        <Row label="On accent / ink tiles">
          <span className="inline-flex p-4 bg-accent text-primary-fg"><Logo size={32} /></span>
          <span className="inline-flex p-4 bg-ink text-ink-fg"><Logo size={32} /></span>
          <span className="inline-flex p-4 bg-ink" style={{ color: '#fff' }} dangerouslySetInnerHTML={{ __html: NUBI_MARK_SVG.replace('class="nubi-logo-svg"', 'width="40"') }} />
        </Row>
        <Row label="Static files (public/brand)">
          <img src="/brand/nubi-mark.svg" width={48} alt="" /><img src="/brand/nubi-lockup.svg" height={32} style={{ height: 32 }} alt="" /><img src="/brand/nubi-wordmark.svg" style={{ height: 28 }} alt="" /><img src="/brand/nubi-mark-duo.svg" width={64} alt="" />
          <span className="p-3 bg-[#0b0c0e] inline-flex gap-4 items-center"><img src="/brand/nubi-mark-inverse.svg" width={32} alt="" /><img src="/brand/nubi-lockup-inverse.svg" style={{ height: 28 }} alt="" /><img src="/brand/nubi-mark-duo-inverse.svg" width={48} alt="" /></span>
        </Row>
        <Row label="Favicon set"><img src="/favicon.svg" width={16} alt="" /><img src="/favicon.svg" width={32} alt="" /><img src="/favicon.ico" width={32} alt="" /><img src="/apple-touch-icon.png" width={90} alt="" /><img src="/icon-192.png" width={96} alt="" /><img src="/icon-maskable-512.png" width={96} alt="" /></Row>
        <Row label="BrandLoader sm / md / lg + label"><BrandLoader size="sm" /><BrandLoader /><BrandLoader size="lg" /><BrandLoader size={96} label="Loading your workspace…" /></Row>
      </Section>

      <Modal open={open} onClose={() => setOpen(false)} title="Delete dashboard" description="This removes the dashboard and its saved versions.">
        <Modal.Body>
          <Field label="Type the name to confirm"><Input placeholder="Revenue by region" /></Field>
        </Modal.Body>
        <Modal.Footer><Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button variant="danger" onClick={() => setOpen(false)}>Delete dashboard</Button></Modal.Footer>
      </Modal>
      <Toaster />
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<StrictMode><Gallery /></StrictMode>)
