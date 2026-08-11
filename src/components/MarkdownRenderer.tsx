import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import mermaid from 'mermaid'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import {
  Children, cloneElement, isValidElement,
  useEffect, useId, useMemo, useState,
  type ReactElement,
} from 'react'
import {
  Copy, Check, Link2, Info, Lightbulb, AlertTriangle,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { resolveDocIllustration } from './illustrations/docMap.js'
import { useTheme } from '../contexts/ThemeContext.jsx'

/**
 * Sanitize schema for raw HTML embedded in *docs* markdown (opt-in via
 * `allowRawHtml`, see bottom of file). Docs use bare `<table><tr><td><img>`
 * blocks for side-by-side light/dark screenshot comparisons — react-markdown
 * doesn't parse raw HTML by default (it renders the tags as literal text), so
 * we add rehype-raw to parse them into the tree and rehype-sanitize (on the
 * default, allow-listed schema) to strip anything unexpected — no scripts,
 * event handlers, or non-http(s) URLs get through even though doc content is
 * repo-controlled. The default schema already allow-lists table/img/br/sub
 * elements and the width/alt/etc attributes docs use.
 */
const docsHtmlSchema = defaultSchema

/**
 * Renders a ```mermaid fenced block to inline SVG. mermaid.render() runs
 * client-side and is re-invoked whenever the source or the app theme changes
 * (dark/light) so diagrams follow the site theme.
 */
function MermaidDiagram({ code }) {
  const { theme } = useTheme()
  const [svg, setSvg] = useState(null)
  const [error, setError] = useState(null)
  // useId() gives a stable, unique-per-instance id without mutating state
  // during render; strip the colons React wraps it in so it's a plain id
  // mermaid can hand to the DOM.
  const reactId = useId()
  const diagramId = `mermaid-diagram-${reactId.replace(/[^a-zA-Z0-9]/g, '')}`

  useEffect(() => {
    let cancelled = false
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: theme === 'dark' ? 'dark' : 'default',
      fontFamily: 'inherit',
    })
    mermaid
      .render(diagramId, code)
      .then(({ svg: rendered }) => {
        if (!cancelled) {
          setSvg(rendered)
          setError(null)
        }
      })
      .catch(err => {
        if (!cancelled) setError(err?.message || String(err))
      })
    return () => {
      cancelled = true
    }
  }, [code, theme, diagramId])

  if (error) {
    return (
      <div className="my-5 rounded-xl border border-border bg-surface-2 p-4 text-sm text-muted">
        Diagram failed to render: {error}
      </div>
    )
  }
  if (!svg) {
    return (
      <div className="my-5 h-32 animate-pulse rounded-xl border border-border bg-surface-2" />
    )
  }
  // dangerouslySetInnerHTML here is the output of mermaid's own renderer
  // (securityLevel: 'strict' sanitizes it), not raw user HTML.
  return (
    <div
      className="my-5 overflow-x-auto rounded-xl border border-border bg-surface p-4 [&_svg]:mx-auto"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  )
}

/**
 * Anchored heading helper — creates an id from text content
 */
function headingId(children) {
  const text = Array.isArray(children)
    ? children.map(c => (typeof c === 'string' ? c : '')).join('')
    : typeof children === 'string'
    ? children
    : ''
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
}

/**
 * Copy a deep link to a heading anchor. Fired alongside the anchor's default
 * in-page scroll (href="#id"), so the URL is both navigated and copied.
 */
function copyHeadingLink(id) {
  if (typeof window === 'undefined' || !navigator.clipboard) return
  const url = `${window.location.origin}${window.location.pathname}#${id}`
  navigator.clipboard.writeText(url).catch(() => {})
}

/**
 * Docs screenshots are captured in both themes by scripts/docs-screenshots.mjs:
 * `<name>.webp` (light) and `<name>-dark.webp` (dark). Markdown always references
 * the base (light) name; in dark mode we rewrite the src to the `-dark`
 * sibling. Other images (logos, docs/assets refs, `illustration:` scheme) are
 * left untouched.
 */
// NOTE: no regex lookbehind here — Tailwind's content scanner tokenizes raw
// JSX source, and a literal lookbehind for "-dark" crashes its candidate
// parser (the "!-…" token reads as a malformed important-modifier).
// Plain suffix check instead.
// Matches BOTH absolute (`/docs/screenshots/foo.webp`) and relative
// (`screenshots/foo.webp`, as some docs write for GitHub rendering) forms, so
// every product screenshot theme-swaps in-app to the single matching variant.
const SCREENSHOT_SRC = /^(?:\/docs\/)?screenshots\/([\w][\w.-]*)\.webp$/

function ThemedDocImage({ src, alt }) {
  const { theme } = useTheme()
  // If a `-dark` variant 404s, fall back to the light image rather than showing
  // a broken image (not every screenshot has a dark capture, e.g. compare.png).
  const [darkMissing, setDarkMissing] = useState(false)
  const resolved = useMemo(() => {
    if (darkMissing || theme !== 'dark' || typeof src !== 'string') return src
    const m = SCREENSHOT_SRC.exec(src)
    if (!m || m[1].endsWith('-dark')) return src
    return `/docs/screenshots/${m[1]}-dark.webp`
  }, [theme, src, darkMissing])
  return (
    <img src={resolved} alt={alt || ''} loading="lazy"
      onError={() => { if (resolved !== src) setDarkMissing(true) }}
      className="my-6 rounded-xl border border-border max-w-full h-auto" />
  )
}

/**
 * Recursively flatten a React children tree to its plain text — used to sniff
 * blockquote markers (callouts) and to copy code without JSX wrappers.
 */
function extractText(node) {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractText).join('')
  if (isValidElement(node)) return extractText((node.props as any)?.children)
  return ''
}

/**
 * Fenced code block: theme-aware Prism style (oneLight in light mode, oneDark
 * in dark) on site-token chrome, plus a hover copy-to-clipboard button. The
 * highlighter background is made transparent so the surrounding `bg-surface`
 * token shows through and the block reads as part of the page in both themes.
 */
function CodeBlock({ language, value, showLineNumbers }) {
  const { theme } = useTheme()
  const [copied, setCopied] = useState(false)

  const copy = () => {
    if (!navigator.clipboard) return
    navigator.clipboard.writeText(value).then(
      () => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1600)
      },
      () => {},
    )
  }

  return (
    <div className="group relative my-5 overflow-hidden rounded-xl border border-border bg-surface shadow-nubi-sm">
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? 'Copied to clipboard' : 'Copy code'}
        className="absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded-md border border-border bg-surface-2/90 px-2 py-1 text-[11px] font-medium text-muted opacity-0 backdrop-blur-sm transition-all hover:text-fg focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring group-hover:opacity-100"
      >
        {copied ? <Check size={13} className="text-brand-teal" /> : <Copy size={13} />}
        <span className={copied ? 'text-brand-teal' : ''}>{copied ? 'Copied' : 'Copy'}</span>
      </button>
      <SyntaxHighlighter
        style={theme === 'dark' ? oneDark : oneLight}
        language={language}
        PreTag="div"
        showLineNumbers={showLineNumbers}
        customStyle={{
          margin: 0,
          background: 'transparent',
          padding: '0.9rem 1rem',
          fontSize: '0.8125rem',
          lineHeight: 1.65,
        }}
        codeTagProps={{ style: { fontFamily: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace" } }}
        lineNumberStyle={{ minWidth: '2.25em', color: 'var(--text-subtle)', opacity: 0.7 }}
      >
        {value}
      </SyntaxHighlighter>
    </div>
  )
}

/**
 * Callout / admonition support. Blockquotes that open with a GitHub-style
 * `[!NOTE]` marker or a bolded `**Note:**` lead are promoted to styled callout
 * boxes with an icon + accent. Everything else stays a normal blockquote.
 */
const CALLOUT_KINDS = {
  note:      { label: 'Note',      tone: 'teal',  Icon: Info },
  tip:       { label: 'Tip',       tone: 'teal',  Icon: Lightbulb },
  info:      { label: 'Info',      tone: 'teal',  Icon: Info },
  important: { label: 'Important', tone: 'amber', Icon: AlertTriangle },
  warning:   { label: 'Warning',   tone: 'amber', Icon: AlertTriangle },
  caution:   { label: 'Caution',   tone: 'amber', Icon: AlertTriangle },
}

const CALLOUT_TONES = {
  teal: {
    box: 'border-brand-teal/30 bg-brand-teal/[0.06]',
    icon: 'text-brand-teal',
    title: 'text-brand-teal',
  },
  amber: {
    box: 'border-amber-500/30 bg-amber-500/[0.08]',
    icon: 'text-amber-500',
    title: 'text-amber-600 dark:text-amber-400',
  },
}

// Strip the leading marker (`[!NOTE]` or `**Note:**`) from a paragraph's nodes.
function stripMarkerNodes(nodes, kind) {
  const out = [...nodes]
  while (out.length && typeof out[0] === 'string' && out[0].trim() === '') out.shift()
  const first = out[0]
  if (isValidElement(first)) {
    // Bolded lead: `> **Note:** …` → drop the <strong>Note:</strong>
    const label = extractText(first).trim().toLowerCase().replace(/:$/, '')
    if (label === kind) {
      out.shift()
      if (typeof out[0] === 'string') out[0] = out[0].replace(/^[\s:]+/, '')
      return out
    }
  }
  if (typeof first === 'string') {
    out[0] = first.replace(/^\s*(\[![a-z]+\]|[a-z]+\s*:)\s*/i, '')
    if (out[0] === '') out.shift()
    return out
  }
  return out
}

function detectCallout(children) {
  const text = extractText(children).trimStart()
  const m =
    text.match(/^\[!([a-z]+)\]/i) ||
    text.match(/^([a-z]+)\s*:/i)
  if (!m) return null
  const kind = m[1].toLowerCase()
  if (!(kind in CALLOUT_KINDS)) return null

  const arr = Children.toArray(children)
  const idx = arr.findIndex(c => isValidElement(c))
  if (idx === -1) return null
  const para = arr[idx] as ReactElement<any>
  const stripped = stripMarkerNodes(Children.toArray(para.props.children), kind)
  const body = [...arr]
  if (stripped.length === 0) {
    body.splice(idx, 1)
  } else {
    body[idx] = cloneElement(para, {}, ...stripped)
  }
  return { kind, body }
}

function Callout({ kind, children }) {
  const { label, tone, Icon } = CALLOUT_KINDS[kind]
  const styles = CALLOUT_TONES[tone]
  return (
    <div className={`my-6 rounded-xl border px-4 py-3.5 ${styles.box}`}>
      <div className="mb-1 flex items-center gap-2">
        <Icon size={16} className={styles.icon} />
        <span className={`text-xs font-semibold uppercase tracking-wide ${styles.title}`}>
          {label}
        </span>
      </div>
      <div className="text-sm leading-7 text-fg [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
        {children}
      </div>
    </div>
  )
}

const components = {
  // ── Headings ──────────────────────────────────────────────────────────────
  h1({ children }) {
    const id = headingId(children)
    return (
      <h1
        id={id}
        className="mt-0 mb-6 text-3xl font-bold tracking-tight font-display text-fg border-b border-border pb-3"
      >
        {children}
      </h1>
    )
  },
  h2({ children }) {
    const id = headingId(children)
    return (
      <h2
        id={id}
        className="mt-10 mb-4 text-2xl font-semibold font-display text-fg scroll-mt-20"
      >
        <a href={`#${id}`} onClick={() => copyHeadingLink(id)} className="group no-underline">
          {children}
          <span className="ml-2 inline-flex align-middle opacity-0 group-hover:opacity-60 text-brand-teal transition-opacity">
            <Link2 size={18} />
          </span>
        </a>
      </h2>
    )
  },
  h3({ children }) {
    const id = headingId(children)
    return (
      <h3
        id={id}
        className="mt-8 mb-3 text-xl font-semibold font-display text-fg scroll-mt-20"
      >
        <a href={`#${id}`} onClick={() => copyHeadingLink(id)} className="group no-underline">
          {children}
          <span className="ml-1.5 inline-flex align-middle opacity-0 group-hover:opacity-60 text-brand-teal transition-opacity">
            <Link2 size={15} />
          </span>
        </a>
      </h3>
    )
  },
  h4({ children }) {
    return (
      <h4 className="mt-6 mb-2 text-base font-semibold font-display text-fg uppercase tracking-wide">
        {children}
      </h4>
    )
  },

  // ── Paragraphs ───────────────────────────────────────────────────────────
  p({ children }) {
    return <p className="my-4 leading-7 text-fg">{children}</p>
  },

  // ── Lists ────────────────────────────────────────────────────────────────
  ul({ children }) {
    return (
      <ul className="my-4 ml-6 list-disc space-y-1.5 text-fg marker:text-brand-teal">
        {children}
      </ul>
    )
  },
  ol({ children }) {
    return (
      <ol className="my-4 ml-6 list-decimal space-y-1.5 text-fg marker:text-accent">
        {children}
      </ol>
    )
  },
  li({ children }) {
    return <li className="leading-7">{children}</li>
  },

  // ── Blockquote ───────────────────────────────────────────────────────────
  blockquote({ children }) {
    const callout = detectCallout(children)
    if (callout) {
      return <Callout kind={callout.kind}>{callout.body}</Callout>
    }
    return (
      <blockquote className="my-6 border-l-4 border-brand-teal bg-surface-2 pl-5 pr-4 py-3 rounded-r-lg text-fg italic">
        {children}
      </blockquote>
    )
  },

  // ── Horizontal rule ──────────────────────────────────────────────────────
  hr() {
    return <hr className="my-8 border-border" />
  },

  // ── Links ────────────────────────────────────────────────────────────────
  a({ href, children }) {
    const isExternal = href && (href.startsWith('http') || href.startsWith('//'))
    const isInternal = href && href.startsWith('/')
    const cls = "text-accent hover:text-brand-teal underline underline-offset-2 decoration-accent/40 hover:decoration-brand-teal transition-colors"
    if (isInternal) {
      return <Link to={href} className={cls}>{children}</Link>
    }
    return (
      <a
        href={href}
        target={isExternal ? '_blank' : undefined}
        rel={isExternal ? 'noopener noreferrer' : undefined}
        className={cls}
      >
        {children}
      </a>
    )
  },

  // ── Inline code ──────────────────────────────────────────────────────────
  // react-markdown passes `inline` for single-backtick code
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || '')
    const raw = String(children).replace(/\n$/, '')
    // Treat any multi-line fence as a block, even an unlabeled ``` ``` fence
    // (react-markdown v9 only tags a language on labeled fences, so ASCII
    // diagrams in bare fences would otherwise collapse into inline code).
    const isBlock = Boolean(match) || raw.includes('\n')

    if (isBlock) {
      const lang = match ? match[1] : 'text'

      if (lang === 'mermaid') {
        return <MermaidDiagram code={raw} />
      }

      return (
        <CodeBlock
          language={lang}
          value={raw}
          showLineNumbers={lang !== 'bash' && lang !== 'sh' && lang !== 'text'}
        />
      )
    }

    // Inline code
    return (
      <code
        className="px-1.5 py-0.5 text-[0.875em] font-mono bg-surface-2 text-brand-teal rounded border border-border"
        {...props}
      >
        {children}
      </code>
    )
  },

  // ── Pre (wraps fenced code) ───────────────────────────────────────────────
  pre({ children }) {
    return <>{children}</>
  },

  // ── Images — `illustration:Name` renders a brand SVG; product screenshots
  // are theme-aware (see ThemedDocImage); else a plain image ─────────────────
  img({ src, alt }) {
    const Illo = resolveDocIllustration(src)
    if (Illo) {
      return (
        <figure className="my-8">
          <div className="rounded-2xl border border-border bg-surface-2 px-5 py-6 sm:px-8 sm:py-8">
            <Illo className="w-full h-auto max-w-xl mx-auto" />
          </div>
          {alt ? (
            <figcaption className="mt-3 text-center text-xs text-muted">{alt}</figcaption>
          ) : null}
        </figure>
      )
    }
    return <ThemedDocImage src={src} alt={alt} />
  },

  // ── Tables (GFM) ─────────────────────────────────────────────────────────
  table({ children }) {
    return (
      <div className="my-6 overflow-x-auto rounded-xl border border-border shadow-sm">
        <table className="min-w-full divide-y divide-border text-sm">
          {children}
        </table>
      </div>
    )
  },
  thead({ children }) {
    return <thead className="sticky top-0 z-10 bg-surface-2">{children}</thead>
  },
  tbody({ children }) {
    return <tbody className="divide-y divide-border bg-surface">{children}</tbody>
  },
  tr({ children }) {
    return (
      <tr className="even:bg-surface-2/40 hover:bg-surface-2 transition-colors">
        {children}
      </tr>
    )
  },
  th({ children }) {
    return (
      <th className="px-4 py-3 text-left text-xs font-semibold text-muted uppercase tracking-wider">
        {children}
      </th>
    )
  },
  td({ children }) {
    return <td className="px-4 py-3 text-fg align-top">{children}</td>
  },

  // ── Strong / Em ──────────────────────────────────────────────────────────
  strong({ children }) {
    return <strong className="font-semibold text-fg">{children}</strong>
  },
  em({ children }) {
    return <em className="italic text-muted">{children}</em>
  },

  // ── sub — used as an image caption inside raw <table> screenshot blocks ───
  sub({ children }) {
    return <sub className="text-xs text-muted">{children}</sub>
  },
}

/**
 * Preserve our `illustration:` scheme (react-markdown's default urlTransform
 * sanitises unknown protocols to an empty string, which would drop the
 * illustration src before the `img` handler can map it). Everything else falls
 * back to the library's default sanitiser.
 */
function urlTransform(url) {
  if (typeof url === 'string' && url.startsWith('illustration:')) return url
  return defaultUrlTransform(url)
}

/**
 * `allowRawHtml` opts a caller into parsing raw HTML tags in the markdown
 * source (rehype-raw) — sanitised through rehype-sanitize's default
 * allow-list either way. Docs content (repo-controlled markdown) sets this to
 * render its `<table><tr><td><img>` side-by-side screenshot blocks; it stays
 * off by default for markdown that comes from chat/AI output, flow notes, or
 * dashboard text widgets, where raw-HTML parsing isn't needed and is one
 * fewer thing to reason about for content that isn't repo-reviewed.
 */
export default function MarkdownRenderer({ content, allowRawHtml = false }) {
  const rehypePlugins: any[] = allowRawHtml ? [rehypeRaw, [rehypeSanitize, docsHtmlSchema]] : []
  return (
    <article className="max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={rehypePlugins}
        urlTransform={urlTransform}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </article>
  )
}
