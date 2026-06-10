/**
 * GitFilesPanel — read-only synced-repo file browser.
 *
 * BUILD_PLAN.md W1-D: display the flat file list returned by
 * listGitFiles(projectId, ref) as a folder tree in the left pane; clicking a
 * file loads its content (getGitFileContent) into the right pane with a
 * syntax hint by extension.
 *
 * Props:
 *   projectId  {string}           required — the project whose repo to browse.
 *   defaultRef {string|undefined} optional starting branch/sha.
 *
 * Degrades gracefully:
 *   - null result from listGitFiles → "No synced repo" empty state.
 *   - loading skeleton while fetching the file list or a file's content.
 *   - per-file error banner when getGitFileContent throws.
 *
 * Do NOT mount this component directly — Wave 2 wires it into GitSyncPanel /
 * AppShell.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  FileCode,
  FileText,
  Folder,
  FolderOpen,
  GitBranch,
  Loader2,
  RefreshCw,
  AlertTriangle,
  GitCommitHorizontal,
} from 'lucide-react'
import { listGitFiles, getGitFileContent } from '../../lib/gitenv.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return a stable language / syntax hint for a file by extension. */
function langHint(path) {
  const ext = path.split('.').pop().toLowerCase()
  switch (ext) {
    case 'sql':   return 'sql'
    case 'py':    return 'python'
    case 'md':    return 'markdown'
    case 'json':  return 'json'
    case 'toml':  return 'toml'
    case 'yaml':
    case 'yml':   return 'yaml'
    default:      return 'text'
  }
}

/** Icon for a file based on its language hint. */
function FileIcon({ path, className }) {
  const lang = langHint(path)
  if (lang === 'markdown') return <FileText size={13} className={className} />
  return <FileCode size={13} className={className} />
}

/**
 * Build a nested tree structure from a flat list of repo-relative paths.
 *
 * Returns { dirs: Map<string, Node>, files: string[] } where each Node is
 * recursively the same shape. The top-level well-known groups (queries/,
 * dashboards/, flows/) come first; everything else is sorted alphabetically.
 */
function buildTree(paths) {
  const root = { dirs: new Map(), files: [] }

  for (const p of paths) {
    const parts = p.split('/')
    let node = root
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i]
      if (!node.dirs.has(seg)) node.dirs.set(seg, { dirs: new Map(), files: [] })
      node = node.dirs.get(seg)
    }
    node.files.push(p)
  }

  return root
}

/** Sort directory segments: well-known prefixes first, rest alphabetically. */
const PRIORITY_DIRS = ['queries', 'dashboards', 'flows']

function sortedDirEntries(dirs) {
  return [...dirs.entries()].sort(([a], [b]) => {
    const ai = PRIORITY_DIRS.indexOf(a)
    const bi = PRIORITY_DIRS.indexOf(b)
    if (ai !== -1 && bi !== -1) return ai - bi
    if (ai !== -1) return -1
    if (bi !== -1) return 1
    return a.localeCompare(b)
  })
}

// ---------------------------------------------------------------------------
// TreeNode — renders one folder with its children
// ---------------------------------------------------------------------------

/**
 * @param {object}   props
 * @param {string}   props.name        folder segment name
 * @param {object}   props.node        { dirs, files }
 * @param {string}   props.prefix      full path prefix up to (not including) name
 * @param {string|null} props.selected currently selected file path
 * @param {function} props.onSelect    (path: string) => void
 * @param {number}   props.depth
 */
function TreeNode({ name, node, prefix, selected, onSelect, depth }) {
  const fullPath = prefix ? `${prefix}/${name}` : name
  const [open, setOpen] = useState(depth < 2) // auto-open top two levels

  const hasChildren = node.dirs.size > 0 || node.files.length > 0
  const indent = depth * 12

  return (
    <div>
      {/* Folder row */}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(v => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1 rounded-lg text-sm text-fg hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        style={{ paddingLeft: `${8 + indent}px` }}
      >
        <span className="shrink-0 text-muted">
          {open
            ? <FolderOpen size={14} />
            : <Folder size={14} />}
        </span>
        <span className="truncate font-medium text-xs">{name}</span>
        <span className="ml-auto shrink-0 text-muted">
          {hasChildren
            ? (open ? <ChevronDown size={11} /> : <ChevronRight size={11} />)
            : null}
        </span>
      </button>

      {/* Children */}
      {open && (
        <div>
          {sortedDirEntries(node.dirs).map(([childName, childNode]) => (
            <TreeNode
              key={childName}
              name={childName}
              node={childNode}
              prefix={fullPath}
              selected={selected}
              onSelect={onSelect}
              depth={depth + 1}
            />
          ))}
          {node.files.map(filePath => (
            <FileRow
              key={filePath}
              path={filePath}
              selected={selected === filePath}
              onSelect={onSelect}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// FileRow — leaf node
// ---------------------------------------------------------------------------

function FileRow({ path, selected, onSelect, depth }) {
  const name = path.split('/').pop()
  const indent = depth * 12

  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={() => onSelect(path)}
      className={[
        'flex w-full items-center gap-1.5 px-2 py-1 rounded-lg text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
        selected
          ? 'bg-primary/10 text-primary font-medium'
          : 'text-fg hover:bg-surface-2',
      ].join(' ')}
      style={{ paddingLeft: `${8 + indent}px` }}
    >
      <FileIcon path={path} className="shrink-0 text-muted" />
      <span className="truncate">{name}</span>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Content pane
// ---------------------------------------------------------------------------

function ContentPane({ file, loading, error }) {
  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 h-full py-16 text-sm text-muted">
        <Loader2 size={16} className="animate-spin" />
        Loading…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-start gap-2 m-4 px-3 py-2.5 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-xs text-red-700 dark:text-red-300">
        <AlertTriangle size={14} className="shrink-0 mt-0.5" />
        <span>{error}</span>
      </div>
    )
  }

  if (!file) {
    return (
      <div className="flex flex-col items-center justify-center h-full py-16 gap-2 text-sm text-muted select-none">
        <FileCode size={28} className="opacity-30" />
        <span>Select a file to view its content.</span>
      </div>
    )
  }

  const lang = langHint(file.path)
  const fileName = file.path.split('/').pop()

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* File header */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border bg-surface shrink-0">
        <FileIcon path={file.path} className="text-muted" />
        <span className="text-xs font-mono font-medium text-fg truncate" title={file.path}>
          {file.path}
        </span>
        <span className="ml-auto shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-2 border border-border text-[10px] font-mono text-muted">
          {lang}
        </span>
      </div>

      {/* Code */}
      <div className="flex-1 min-h-0 overflow-auto">
        <pre
          aria-label={`Content of ${fileName}`}
          className="p-4 text-xs font-mono leading-relaxed text-fg whitespace-pre min-w-0"
        >
          <code>{file.content}</code>
        </pre>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// GitFilesPanel — main export
// ---------------------------------------------------------------------------

/**
 * @param {{ projectId: string, defaultRef?: string }} props
 */
export default function GitFilesPanel({ projectId, defaultRef = '' }) {
  const [ref, setRef] = useState(defaultRef)
  const [draftRef, setDraftRef] = useState(defaultRef)

  // File list state
  const [listLoading, setListLoading] = useState(false)
  const [fileList, setFileList] = useState(null) // null = not loaded; { ref, files }
  const [listError, setListError] = useState(null)
  const noRepo = fileList === null && !listLoading && !listError

  // Selected file + content state
  const [selectedPath, setSelectedPath] = useState(null)
  const [contentLoading, setContentLoading] = useState(false)
  const [fileContent, setFileContent] = useState(null) // { path, ref, content }
  const [contentError, setContentError] = useState(null)

  // Track the last fetch so stale responses are dropped
  const fetchSeq = useRef(0)

  // Load the file list
  const loadFiles = useCallback(async (refToLoad) => {
    if (!projectId) return
    setListLoading(true)
    setListError(null)
    setFileList(null)
    setSelectedPath(null)
    setFileContent(null)
    setContentError(null)

    const result = await listGitFiles(projectId, refToLoad || undefined)
    setListLoading(false)
    if (result === null) {
      // graceful null → no repo
      setListError(null)
      setFileList(null)
    } else if (!result || !Array.isArray(result.files)) {
      setListError('Unexpected response from server.')
    } else {
      setFileList(result)
    }
  }, [projectId])

  // Initial load
  useEffect(() => {
    loadFiles(ref)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Load a file's content when selectedPath changes
  useEffect(() => {
    if (!selectedPath || !projectId) return

    const seq = ++fetchSeq.current
    setContentLoading(true)
    setContentError(null)
    setFileContent(null)

    getGitFileContent(projectId, selectedPath, ref || undefined)
      .then(data => {
        if (fetchSeq.current !== seq) return // stale
        setFileContent(data)
      })
      .catch(err => {
        if (fetchSeq.current !== seq) return
        setContentError(err?.message || 'Failed to load file content.')
      })
      .finally(() => {
        if (fetchSeq.current === seq) setContentLoading(false)
      })
  }, [selectedPath, projectId, ref])

  function handleRefSubmit(e) {
    e?.preventDefault?.()
    const next = draftRef.trim()
    setRef(next)
    loadFiles(next)
  }

  const tree = useMemo(
    () => (fileList ? buildTree(fileList.files) : null),
    [fileList],
  )

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex flex-col h-full min-h-0 bg-surface rounded-2xl border border-border overflow-hidden">

      {/* ---- Panel header ---- */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border shrink-0">
        <GitCommitHorizontal size={16} className="text-muted shrink-0" />
        <span className="text-sm font-semibold text-fg">Synced files</span>

        {/* Ref input */}
        <form onSubmit={handleRefSubmit} className="ml-auto flex items-center gap-1.5">
          <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-bg border border-border text-xs text-muted">
            <GitBranch size={12} className="shrink-0" />
            <input
              type="text"
              value={draftRef}
              onChange={e => setDraftRef(e.target.value)}
              onBlur={handleRefSubmit}
              placeholder="main"
              aria-label="Ref or branch"
              className="w-24 bg-transparent text-fg placeholder:text-muted focus:outline-none"
            />
          </div>
          <button
            type="button"
            onClick={() => loadFiles(ref)}
            disabled={listLoading}
            aria-label="Refresh file list"
            title="Refresh file list"
            className="p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <RefreshCw size={13} className={listLoading ? 'animate-spin' : ''} />
          </button>
        </form>
      </div>

      {/* ---- Body: left tree + right content ---- */}
      <div className="flex flex-1 min-h-0 divide-x divide-border overflow-hidden">

        {/* LEFT — file tree */}
        <div className="w-56 shrink-0 overflow-y-auto py-2 px-1 space-y-0.5">
          {listLoading && (
            <div className="flex items-center gap-2 px-3 py-4 text-xs text-muted">
              <Loader2 size={13} className="animate-spin" />
              Loading files…
            </div>
          )}

          {listError && (
            <div className="flex items-start gap-1.5 px-3 py-2 text-xs text-red-500">
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
              {listError}
            </div>
          )}

          {!listLoading && !listError && fileList === null && (
            /* No synced repo */
            <div className="flex flex-col items-center gap-2 px-3 py-6 text-center">
              <GitBranch size={22} className="text-muted/40" />
              <p className="text-xs text-muted leading-snug">
                No synced repo.<br />
                Connect a remote in project settings.
              </p>
            </div>
          )}

          {!listLoading && tree && fileList.files.length === 0 && (
            <p className="px-3 py-4 text-xs text-muted">No files on this ref.</p>
          )}

          {!listLoading && tree && (
            <>
              {sortedDirEntries(tree.dirs).map(([name, node]) => (
                <TreeNode
                  key={name}
                  name={name}
                  node={node}
                  prefix=""
                  selected={selectedPath}
                  onSelect={setSelectedPath}
                  depth={0}
                />
              ))}
              {tree.files.map(filePath => (
                <FileRow
                  key={filePath}
                  path={filePath}
                  selected={selectedPath === filePath}
                  onSelect={setSelectedPath}
                  depth={0}
                />
              ))}
            </>
          )}
        </div>

        {/* RIGHT — content pane */}
        <div className="flex-1 min-w-0 overflow-hidden flex flex-col">
          <ContentPane
            file={fileContent}
            loading={contentLoading}
            error={contentError}
          />
        </div>
      </div>
    </div>
  )
}
