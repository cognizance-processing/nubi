/**
 * CalcShell — terminal-framed wrapper for the live pricing calculators, shared
 * by the landing page and /pricing so the calculators look identical
 * everywhere: dark chrome strip (traffic dots + `calc/NN · slug`) with a
 * "live estimate" badge, over a light card body supplied by the caller.
 */
export default function CalcShell({ index, slug, children }) {
  return (
    <div className="rounded-2xl sm:rounded-3xl border border-border bg-surface shadow-[0_30px_70px_-32px_rgba(27,35,99,0.45)] overflow-hidden">
      {/* always-dark terminal strip */}
      <div className="flex items-center justify-between gap-3 px-4 sm:px-7 py-2.5 bg-[#0d1430] border-b border-black/40">
        <span className="flex items-center gap-3 min-w-0">
          <span className="flex gap-1.5 shrink-0" aria-hidden="true">
            <span className="w-2.5 h-2.5 rounded-full bg-[#f4726f]/80" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#f5bd4f]/80" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#61c554]/80" />
          </span>
          <span className="font-mono text-[11px] text-slate-300 truncate">
            calc/{index} · {slug}
          </span>
        </span>
        <span className="hidden sm:inline font-mono text-[9.5px] text-teal-300/90 border border-teal-400/25 bg-teal-400/[0.08] rounded px-1.5 py-0.5 whitespace-nowrap">
          live estimate
        </span>
      </div>
      {children}
    </div>
  )
}
