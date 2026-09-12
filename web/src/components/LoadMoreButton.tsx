interface LoadMoreButtonProps {
  loading: boolean
  loaded: number
  total: number
  onClick: () => void
}

export default function LoadMoreButton({
  loading,
  loaded,
  total,
  onClick,
}: LoadMoreButtonProps) {
  return (
    <div className="flex flex-col items-center gap-2 py-6">
      <button
        type="button"
        onClick={onClick}
        disabled={loading}
        className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
      >
        {loading ? 'Loading…' : 'Load more'}
      </button>
      <p className="text-xs tabular-nums text-slate-400 dark:text-slate-500">
        Showing {loaded.toLocaleString()} of {total.toLocaleString()}
      </p>
    </div>
  )
}
