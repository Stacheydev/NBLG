const panel =
  'rounded-lg border border-slate-200 bg-white p-10 text-center dark:border-slate-800 dark:bg-slate-900'

/** Full-page placeholder while the stored session is being restored. */
export function BootSplash() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-slate-50 dark:bg-slate-950">
      <p className="text-sm text-slate-400 dark:text-slate-500">Loading…</p>
    </div>
  )
}

/** Row-shaped skeleton, so the first paint matches the real layout. */
export function LoadingSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading leads"
      className="overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900"
    >
      <div className="divide-y divide-slate-100 dark:divide-slate-800">
        {Array.from({ length: 8 }, (_, index) => (
          <div key={index} className="flex items-center gap-4 px-4 py-3.5">
            <div className="min-w-0 flex-1 space-y-2">
              <div className="h-3.5 w-2/5 animate-pulse rounded bg-slate-200 dark:bg-slate-800" />
              <div className="h-2.5 w-1/4 animate-pulse rounded bg-slate-100 dark:bg-slate-800/60" />
            </div>
            <div className="hidden h-3 w-24 animate-pulse rounded bg-slate-100 md:block dark:bg-slate-800/60" />
            <div className="hidden h-3 w-28 animate-pulse rounded bg-slate-100 md:block dark:bg-slate-800/60" />
            <div className="h-3 w-16 animate-pulse rounded bg-slate-100 dark:bg-slate-800/60" />
          </div>
        ))}
      </div>
    </div>
  )
}

interface ErrorStateProps {
  message: string
  onRetry: () => void
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div role="alert" className={panel}>
      <h2 className="text-sm font-medium text-slate-900 dark:text-slate-100">
        Could not load leads
      </h2>
      <p className="mx-auto mt-1.5 max-w-md text-sm text-slate-500 dark:text-slate-400">
        {message}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-5 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-slate-900"
      >
        Try again
      </button>
    </div>
  )
}

/** Shown when a query failed but rows from an earlier fetch are still listed. */
export function InlineErrorBanner({ message, onRetry }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
    >
      <span>{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 font-medium underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
      >
        Retry
      </button>
    </div>
  )
}

/** The table is genuinely empty - no leads have been generated yet. */
export function EmptyState() {
  return (
    <div className={panel}>
      <h2 className="text-sm font-medium text-slate-900 dark:text-slate-100">
        No leads yet
      </h2>
      <p className="mx-auto mt-1.5 max-w-sm text-sm text-slate-500 dark:text-slate-400">
        Leads appear here after a generation run completes.
      </p>
    </div>
  )
}

interface NoResultsStateProps {
  query: string
  onClear: () => void
}

/** Distinct from EmptyState on purpose: "nothing here" and "nothing matched"
 *  are different problems and conflating them makes the page feel broken. */
export function NoResultsState({ query, onClear }: NoResultsStateProps) {
  return (
    <div className={panel}>
      <h2 className="text-sm font-medium text-slate-900 dark:text-slate-100">
        No matches
      </h2>
      <p className="mx-auto mt-1.5 max-w-sm text-sm text-slate-500 dark:text-slate-400">
        Nothing matched “{query}”. Try a shorter term, or part of a domain.
      </p>
      <button
        type="button"
        onClick={onClear}
        className="mt-5 rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
      >
        Clear search
      </button>
    </div>
  )
}
