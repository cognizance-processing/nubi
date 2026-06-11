/**
 * CodeTile.jsx — editor-style syntax-highlighted code tiles for the landing
 * Features section. They share the `Illustration` contract used by DiffRow
 * (a component accepting `className`), so they drop in wherever an SVG
 * illustration would — for dev-centric features a real snippet is more
 * convincing than abstract art.
 *
 * Highlighting is a tiny dependency-free tokenizer (comments / strings /
 * keywords / decorators / functions / numbers) — enough to read well without
 * pulling in a full highlighter.
 */

const KEYWORDS = {
  python: ['from', 'import', 'def', 'return', 'class', 'with', 'as', 'for', 'in', 'if', 'else', 'await', 'async', 'yield', 'lambda', 'None', 'True', 'False'],
  js: ['import', 'from', 'const', 'let', 'var', 'function', 'return', 'await', 'async', 'export', 'default', 'new', 'if', 'else'],
}

// Ordered token matchers. First match at the current scan position wins.
function matchers(lang) {
  if (lang === 'shell') {
    return [
      ['comment', /^#.*/],
      ['string', /^("[^"]*"|'[^']*')/],
      ['fn', /^\bnubi\b/],              // the CLI binary reads as the "function"
      ['decorator', /^--?[\w-]+/],      // flags: --env, -d, etc.
      ['space', /^\s+/],
      ['word', /^[\w./@:=]+/],
      ['other', /^[^\s]/],
    ]
  }
  if (lang === 'html') {
    return [
      ['comment', /^<!--.*?-->/],
      ['string', /^("[^"]*"|'[^']*')/],
      ['tag', /^<\/?[\w-]+/],          // <nubi-dashboard  or  </nubi-dashboard
      ['tagpunct', /^\/?>/],            // >  or  />
      ['attr', /^[\w-]+(?==)/],         // attribute name immediately before "="
      ['space', /^\s+/],
      ['other', /^[^<>"'\s=]+/],        // text / unquoted attr values
      ['other', /^[=]/],
    ]
  }
  const comment = lang === 'python' ? /^#.*/ : /^\/\/.*/
  return [
    ['comment', comment],
    ['string', /^("[^"]*"|'[^']*'|`[^`]*`)/],
    ['decorator', /^@[\w.]+/],
    ['number', /^\b\d+(\.\d+)?\b/],
    // a name immediately followed by "(" is a function call
    ['fn', /^[A-Za-z_]\w*(?=\s*\()/],
    ['word', /^[A-Za-z_]\w*/],
    ['space', /^\s+/],
    ['other', /^[^\sA-Za-z_@#"'`0-9]+/],
  ]
}

const TOKEN_CLASS = {
  comment: 'text-slate-400/70 italic',
  string: 'text-amber-300',
  decorator: 'text-violet-300',
  number: 'text-violet-300',
  fn: 'text-sky-300',
  keyword: 'text-teal-300',
  word: 'text-slate-100',
  space: '',
  other: 'text-slate-400',
  // HTML
  tag: 'text-sky-300',
  attr: 'text-violet-300',
  tagpunct: 'text-slate-500',
}

function tokenizeLine(line, lang) {
  const kw = new Set(KEYWORDS[lang] ?? [])
  const ms = matchers(lang)
  const out = []
  let rest = line
  let guard = 0
  while (rest.length && guard++ < 500) {
    let matched = false
    for (const [type, re] of ms) {
      const m = re.exec(rest)
      if (m && m[0].length) {
        let t = type
        if (type === 'word' && kw.has(m[0])) t = 'keyword'
        out.push({ t, text: m[0] })
        rest = rest.slice(m[0].length)
        matched = true
        break
      }
    }
    if (!matched) { out.push({ t: 'other', text: rest[0] }); rest = rest.slice(1) }
  }
  return out
}

/**
 * @param {{ filename: string, lang: 'python'|'js', code: string, className?: string }} props
 */
export function CodeTile({ filename, lang, code, className = '' }) {
  const lines = code.replace(/\n$/, '').split('\n')
  return (
    <div className={`w-full max-w-[480px] ${className}`}>
      <div className="rounded-xl overflow-hidden border border-white/10 bg-[#0c1322] shadow-xl shadow-black/20 ring-1 ring-black/5">
        {/* Title bar */}
        <div className="flex items-center gap-2 px-3.5 py-2.5 bg-white/[0.03] border-b border-white/10">
          <span className="flex gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-red-400/80" />
            <span className="w-2.5 h-2.5 rounded-full bg-amber-400/80" />
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-400/80" />
          </span>
          <span className="ml-1 text-[11px] font-mono text-slate-400">{filename}</span>
        </div>
        {/* Code */}
        <pre className="overflow-x-auto px-4 py-3.5 text-[12.5px] leading-[1.65] font-mono">
          <code className="grid">
            {lines.map((line, i) => (
              <span key={i} className="grid grid-cols-[1.6rem_1fr] gap-3">
                <span className="text-right text-slate-600 select-none tabular-nums">{i + 1}</span>
                <span className="whitespace-pre">
                  {line === '' ? ' ' : tokenizeLine(line, lang).map((tok, j) => (
                    <span key={j} className={TOKEN_CLASS[tok.t]}>{tok.text}</span>
                  ))}
                </span>
              </span>
            ))}
          </code>
        </pre>
      </div>
    </div>
  )
}

// ── Feature-specific snippets (drop-in Illustration replacements) ───────────

const CONNECTOR_SDK = `from nubi import connector, Query

@connector("postgres")        # register a datastore type
def orders(q: Query):
    return q.sql("""
      select date, region,
             sum(amount) as revenue
      from orders group by 1, 2
    """)`

export function ConnectorSdkCode({ className }) {
  return <CodeTile filename="connectors/orders.py" lang="python" code={CONNECTOR_SDK} className={className} />
}

const FLOW_CODE = `from nubi.flows import flow, task

@flow(schedule="0 6 * * *")     # daily 06:00
def daily_rollup():
    raw   = task.query("orders")
    clean = task.python(dedupe, needs=[raw])
    task.materialize(clean,
                     table="orders_daily")`

export function FlowCode({ className }) {
  return <CodeTile filename="flows/daily_rollup.py" lang="python" code={FLOW_CODE} className={className} />
}

const EMBED_AUTH = `import { sign } from "@nubi/embed"

// row-level security rides in the token
const token = sign({
  dashboard: "revenue",
  claims: { org_id: user.orgId,
            role: "viewer" },
})
// <iframe src={` + '`' + `.../e/\${token}` + '`' + `} />`

export function EmbedAuthCode({ className }) {
  return <CodeTile filename="embed.ts" lang="js" code={EMBED_AUTH} className={className} />
}

const LLM_DASHBOARD = `<!-- authored by an agent via author_dashboard -->
<nubi-dashboard title="Revenue">
  <nubi-query id="rev" sql="
    select month, sum(amount) as revenue
    from orders group by 1" />

  <nubi-kpi query="rev" value="revenue" />
  <nubi-chart type="area" query="rev"
              x="month" y="revenue" />
</nubi-dashboard>`

export function LlmDashboardCode({ className }) {
  return <CodeTile filename="dashboards/revenue.html" lang="html" code={LLM_DASHBOARD} className={className} />
}

const FILES_AS_CODE_CLI = `nubi login            # auth, token in ~/.nubi
nubi pull             # dashboards, queries,
                      # flows + connectors → files

# edit anything as files, commit to git
git add . && git commit -m "tweak rollup"

nubi push             # non-secret manifests → cloud
nubi deploy --env prod  # CI: secrets + manifests`

export function FilesAsCodeCli({ className }) {
  return <CodeTile filename="~/projects/analytics" lang="shell" code={FILES_AS_CODE_CLI} className={className} />
}
