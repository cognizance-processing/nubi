import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Link } from 'react-router-dom'
import { resolveDocIllustration } from './illustrations/docMap.js'

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

  // ── Images — `illustration:Name` renders a brand SVG; else a plain image ──
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
    return (
      <img src={src} alt={alt || ''} loading="lazy"
        className="my-6 rounded-xl border border-border max-w-full h-auto" />
    )
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
}

export default function MarkdownRenderer({ content }) {
  return (
    <article className="max-w-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </article>
  )
}
