import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import mermaid from 'mermaid'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { useEffect, useId, useMemo, useState } from 'react'
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
const SCREENSHOT_SRC = /^\/docs\/screenshots\/([\w][\w.-]*)\.webp$/

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
        <a href={`#${id}`} className="group no-underline">
          {children}
          <span className="ml-2 opacity-0 group-hover:opacity-40 text-brand-teal font-normal transition-opacity">
            #
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
        <a href={`#${id}`} className="group no-underline">
          {children}
          <span className="ml-1.5 opacity-0 group-hover:opacity-40 text-brand-teal font-normal transition-opacity">
            #
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
        <div className="my-5 rounded-xl overflow-hidden border border-border shadow-lg">
          <SyntaxHighlighter
            style={oneDark}
            language={lang}
            PreTag="div"
            className="!rounded-none !m-0 text-sm"
            showLineNumbers={lang !== 'bash' && lang !== 'sh' && lang !== 'text'}
            {...props}
          >
            {raw}
          </SyntaxHighlighter>
        </div>
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
    return <thead className="bg-surface-2">{children}</thead>
  },
  tbody({ children }) {
    return <tbody className="divide-y divide-border bg-surface">{children}</tbody>
  },
  tr({ children }) {
    return <tr className="hover:bg-surface-2 transition-colors">{children}</tr>
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
  const rehypePlugins = allowRawHtml ? [rehypeRaw, [rehypeSanitize, docsHtmlSchema]] : []
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
