/**
 * Docs registry — loads all markdown sources from docs/*.md via Vite
 * import.meta.glob and builds a sectioned navigation.
 *
 * Three top-level SECTIONS (rendered in order):
 *   1. "Using Nubi"          — how to use the product / the UI / every feature.
 *                              Applies to both self-host and Nubi Cloud.
 *   2. "Nubi Cloud"          — the thin managed layer: billing differs here.
 *   3. "Open-source project" — self-host, architecture, internals, building on Nubi.
 *
 * Each section contains one or more collapsible GROUPS. All doc content lives
 * under /docs/ in the repo root; slugs are derived from filenames; the home doc
 * uses the slug "home".
 */

// ── Load markdown files eagerly (one glob; assignment is by slug below) ───────
const mdFiles = import.meta.glob(
  [
    '/docs/index.md',
    // Using Nubi
    '/docs/quickstart.md',
    '/docs/getting-started.md',
    '/docs/ui-tour.md',
    '/docs/connectors.md',
    '/docs/queries-and-params.md',
    '/docs/pre-aggregations.md',
    '/docs/dashboards.md',
    '/docs/exports-and-jobs.md',
    '/docs/flows.md',
    '/docs/ai-and-mcp.md',
    '/docs/embedding.md',
    '/docs/organization-settings.md',
    '/docs/notifications-and-integrations.md',
    '/docs/how-to.md',
    '/docs/api-reference.md',
    '/docs/api-auth.md',
    '/docs/api-resources.md',
    '/docs/api-analytics.md',
    '/docs/api-ai.md',
    '/docs/api-flows.md',
    '/docs/api-billing.md',
    // Nubi Cloud
    '/docs/cloud.md',
    '/docs/billing-and-usage.md',
    // Open-source project
    '/docs/self-host.md',
    '/docs/open-core.md',
    '/docs/architecture-open-core.md',
    '/docs/developer-guide.md',
    '/docs/connector-security.md',
    '/docs/kernel-security.md',
    '/docs/cache-key-spec.md',
    '/docs/conformance.md',
    '/docs/secrets.md',
    '/docs/sdk-and-cli.md',
    '/docs/files-as-code.md',
    '/docs/git-sync.md',
    '/docs/bridges.md',
    '/docs/development.md',
    '/docs/docs-and-screenshots.md',
    // Feature docs (shipped + linked from README/index, previously unreachable in-app)
    '/docs/metrics-reference.md',
    '/docs/semantic-and-data-apps.md',
    '/docs/transformation.md',
    '/docs/materialization.md',
    '/docs/data-health.md',
    '/docs/governance.md',
    '/docs/mcp.md',
    '/docs/embed-api.md',
    '/docs/compute-kernel-attribution-runner.md',
    '/docs/notebooks.md',
    '/docs/billing-model.md',
    '/docs/architecture-and-economics.md',
    '/docs/compliance.md',
    '/docs/observability.md',
  ],
  { query: '?raw', import: 'default', eager: true }
)

// ── Section / group layout (order matters) ────────────────────────────────────
// Each group lists display `items`. An item is either a bare slug string, or a
// `{ slug, children: [...slugs] }` object for a parent page with one level of
// nested sub-pages (rendered indented under the parent in the sidebar). The
// first group ('Home') has no section header.
const LAYOUT = [
  { section: null,                  group: 'Home',              items: ['home'] },

  { section: 'Using Nubi',          group: 'Get started',       items: ['quickstart', 'getting-started', 'ui-tour'] },
  { section: 'Using Nubi',          group: 'Work with data',    items: [
    'connectors',
    { slug: 'queries-and-params', children: ['pre-aggregations', 'metrics-reference'] },
    'dashboards', 'data-health', 'governance', 'exports-and-jobs',
  ] },
  { section: 'Using Nubi',          group: 'Automate & build',  items: [
    { slug: 'flows', children: ['notebooks'] },
    'transformation', 'materialization', 'semantic-and-data-apps',
    { slug: 'ai-and-mcp', children: ['mcp'] },
    { slug: 'embedding', children: ['embed-api'] },
  ] },
  { section: 'Using Nubi',          group: 'Your account',      items: ['organization-settings', 'notifications-and-integrations'] },
  { section: 'Using Nubi',          group: 'Reference',         items: [
    'how-to',
    { slug: 'api-reference', children: ['api-auth', 'api-resources', 'api-analytics', 'api-ai', 'api-flows', 'api-billing'] },
  ] },

  { section: 'Nubi Cloud',          group: 'Cloud & billing',   items: ['cloud', 'billing-and-usage', 'billing-model'] },

  { section: 'Open-source project', group: 'Self-host',         items: ['self-host', 'open-core', 'architecture-open-core'] },
  { section: 'Open-source project', group: 'Security & internals', items: ['architecture-and-economics', 'compliance', 'connector-security', 'kernel-security', 'cache-key-spec', 'conformance', 'secrets', 'observability'] },
  { section: 'Open-source project', group: 'Build on Nubi',     items: ['developer-guide', 'sdk-and-cli', 'files-as-code', 'git-sync', 'bridges', 'compute-kernel-attribution-runner'] },
  { section: 'Open-source project', group: 'Contributing',      items: ['development', 'docs-and-screenshots'] },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Extract the first H1 heading from markdown content */
function extractTitle(content, slug) {
  const h1Match = content.match(/^#\s+(.+)$/m)
  if (h1Match) return h1Match[1].trim()
  return slug.charAt(0).toUpperCase() + slug.slice(1).replace(/-/g, ' ')
}

/** /docs/foo-bar.md → "foo-bar"; /docs/index.md → "home" */
function pathToSlug(filePath) {
  const filename = filePath.split('/').pop().replace(/\.md$/, '')
  return filename === 'index' ? 'home' : filename.toLowerCase()
}

// Build a slug → { content, path } map.
const bySlug = {}
for (const [path, content] of Object.entries(mdFiles)) {
  bySlug[pathToSlug(path)] = { content, path }
}

// ── Assemble docs + groups from the layout ────────────────────────────────────
// DOCS is the flattened list in display order (parent immediately followed by
// its children) — used for search, prev/next, and slug resolution. DOC_GROUPS
// carries the nested tree (each parent doc may have a `children` array) for the
// sidebar renderer.
const DOCS = []
const seen = new Set()

/** Build a single doc object (and record it in the flat DOCS list). */
function buildDoc(slug, group, section) {
  const entry = bySlug[slug]
  if (!entry || seen.has(slug)) return null
  seen.add(slug)
  const title = slug === 'home' ? 'Nubi Docs' : extractTitle(entry.content, slug)
  const doc = { slug, title, group, section, content: entry.content, path: entry.path }
  DOCS.push(doc)
  return doc
}

export const DOC_GROUPS = LAYOUT.map(({ section, group, items }) => {
  const docs = []
  for (const item of items) {
    const slug = typeof item === 'string' ? item : item.slug
    const childSlugs = typeof item === 'string' ? [] : (item.children ?? [])
    const doc = buildDoc(slug, group, section)
    if (!doc) continue
    // Children are appended to DOCS right after their parent (display order) and
    // attached to the parent for nested sidebar rendering.
    const children = []
    for (const childSlug of childSlugs) {
      const child = buildDoc(childSlug, group, section)
      if (child) {
        child.parentSlug = slug
        children.push(child)
      }
    }
    if (children.length) doc.children = children
    docs.push(doc)
  }
  return { name: group, section, docs }
}).filter(g => g.docs.length > 0)

// Ordered list of the three section names (for rendering section headers).
export const DOC_SECTIONS = DOC_GROUPS
  .map(g => g.section)
  .filter((s, i, arr) => s && arr.indexOf(s) === i)

export function getDocs() {
  return DOCS
}

export function getDoc(slug) {
  return DOCS.find(d => d.slug === slug) ?? null
}

export const FIRST_DOC = DOCS[0] ?? null
