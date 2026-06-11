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
    // Nubi Cloud
    '/docs/cloud.md',
    '/docs/billing-and-usage.md',
    // Open-source project
    '/docs/self-host.md',
    '/docs/open-core.md',
    '/docs/architecture-open-core.md',
    '/docs/connector-security.md',
    '/docs/kernel-security.md',
    '/docs/cache-key-spec.md',
    '/docs/conformance.md',
    '/docs/secrets.md',
    '/docs/sdk-and-cli.md',
    '/docs/files-as-code.md',
    '/docs/git-sync.md',
    '/docs/bridges.md',
    '/docs/lakehouse.md',
    '/docs/development.md',
    '/docs/docs-and-screenshots.md',
  ],
  { query: '?raw', import: 'default', eager: true }
)

// ── Section / group layout (order matters) ────────────────────────────────────
// Each group lists doc slugs in display order. The first group ('Home') has no
// section header.
const LAYOUT = [
  { section: null,                  group: 'Home',              slugs: ['home'] },

  { section: 'Using Nubi',          group: 'Get started',       slugs: ['getting-started', 'ui-tour'] },
  { section: 'Using Nubi',          group: 'Work with data',    slugs: ['connectors', 'queries-and-params', 'pre-aggregations', 'dashboards', 'exports-and-jobs'] },
  { section: 'Using Nubi',          group: 'Automate & build',  slugs: ['flows', 'ai-and-mcp', 'embedding'] },
  { section: 'Using Nubi',          group: 'Your account',      slugs: ['organization-settings', 'notifications-and-integrations'] },

  { section: 'Nubi Cloud',          group: 'Cloud & billing',   slugs: ['cloud', 'billing-and-usage'] },

  { section: 'Open-source project', group: 'Self-host',         slugs: ['self-host', 'open-core', 'architecture-open-core'] },
  { section: 'Open-source project', group: 'Security & internals', slugs: ['connector-security', 'kernel-security', 'cache-key-spec', 'conformance', 'secrets'] },
  { section: 'Open-source project', group: 'Build on Nubi',     slugs: ['sdk-and-cli', 'files-as-code', 'git-sync', 'bridges', 'lakehouse'] },
  { section: 'Open-source project', group: 'Contributing',      slugs: ['development', 'docs-and-screenshots'] },
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
const DOCS = []
const seen = new Set()

export const DOC_GROUPS = LAYOUT.map(({ section, group, slugs }) => {
  const docs = []
  for (const slug of slugs) {
    const entry = bySlug[slug]
    if (!entry || seen.has(slug)) continue
    seen.add(slug)
    const title = slug === 'home' ? 'Nubi Docs' : extractTitle(entry.content, slug)
    const doc = { slug, title, group, section, content: entry.content, path: entry.path }
    docs.push(doc)
    DOCS.push(doc)
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
