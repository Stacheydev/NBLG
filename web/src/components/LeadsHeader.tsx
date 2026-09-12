import { supabase } from '../lib/supabase'

interface LeadsHeaderProps {
  total: number | null
  refreshing: boolean
  onRefresh: () => void
}

export default function LeadsHeader({
  total,
  refreshing,
  onRefresh,
}: LeadsHeaderProps) {
  return (
    // h-14 is fixed because the table header sticks directly beneath it.
    <header className="sticky top-0 z-20 h-14 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
      <div className="mx-auto flex h-full max-w-6xl items-center gap-3 px-4 sm:px-6">
        <h1 className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-900 sm:text-sm dark:text-slate-100">
          Northbound{' '}
          <span className="font-normal text-slate-400 dark:text-slate-500">
            Leads
          </span>
        </h1>

        {total !== null && (
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium tabular-nums text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
            {total.toLocaleString()}
          </span>
        )}

        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="rounded-md px-2.5 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          <button
            type="button"
            onClick={() => void supabase.auth.signOut()}
            className="rounded-md px-2.5 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  )
}
