import { useState } from 'react'
import { useSession } from './hooks/useSession'
import { useDebouncedValue } from './hooks/useDebouncedValue'
import { useLeads } from './hooks/useLeads'
import LoginScreen from './components/LoginScreen'
import LeadsHeader from './components/LeadsHeader'
import SearchInput from './components/SearchInput'
import LeadsTable from './components/LeadsTable'
import LeadCard from './components/LeadCard'
import LoadMoreButton from './components/LoadMoreButton'
import {
  BootSplash,
  EmptyState,
  ErrorState,
  InlineErrorBanner,
  LoadingSkeleton,
  NoResultsState,
} from './components/States'

export default function App() {
  const { session, loading } = useSession()

  if (loading) return <BootSplash />
  if (!session) return <LoginScreen />
  return <LeadsDashboard />
}

function LeadsDashboard() {
  const [searchInput, setSearchInput] = useState('')
  const search = useDebouncedValue(searchInput, 250)

  const {
    leads,
    total,
    loading,
    loadingMore,
    refreshing,
    error,
    hasMore,
    loadMore,
    refresh,
  } = useLeads(search)

  const isSearching = search.trim().length > 0
  const hasRows = leads.length > 0

  return (
    <div className="min-h-dvh bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <LeadsHeader total={total} refreshing={refreshing} onRefresh={refresh} />

      <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
        <div className="mb-5 max-w-md">
          <SearchInput value={searchInput} onChange={setSearchInput} />
        </div>

        {/* An error with rows already on screen is a banner, not a takeover -
            losing the list the user was reading would be worse. */}
        {error && hasRows && (
          <InlineErrorBanner message={error} onRetry={refresh} />
        )}

        {error && !hasRows ? (
          <ErrorState message={error} onRetry={refresh} />
        ) : loading && !hasRows ? (
          <LoadingSkeleton />
        ) : !hasRows ? (
          isSearching ? (
            <NoResultsState query={search} onClear={() => setSearchInput('')} />
          ) : (
            <EmptyState />
          )
        ) : (
          <>
            <LeadsTable leads={leads} />

            <ul className="space-y-3 md:hidden">
              {leads.map((lead) => (
                <LeadCard key={lead.domain} lead={lead} />
              ))}
            </ul>

            {hasMore && total !== null ? (
              <LoadMoreButton
                loading={loadingMore}
                loaded={leads.length}
                total={total}
                onClick={loadMore}
              />
            ) : (
              <p className="py-6 text-center text-xs tabular-nums text-slate-400 dark:text-slate-500">
                {isSearching
                  ? `${leads.length.toLocaleString()} matching ${leads.length === 1 ? 'lead' : 'leads'}`
                  : `All ${leads.length.toLocaleString()} leads shown`}
              </p>
            )}
          </>
        )}
      </main>
    </div>
  )
}
