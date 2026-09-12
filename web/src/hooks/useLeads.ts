import { useCallback, useEffect, useRef, useState } from 'react'
import { supabase } from '../lib/supabase'
import { LEAD_COLUMNS, type Lead } from '../types'

export const PAGE_SIZE = 50

type FetchMode = 'replace' | 'append' | 'refresh'

interface UseLeadsResult {
  leads: Lead[]
  /** Total matching rows server-side, or null before the first response. */
  total: number | null
  loading: boolean
  loadingMore: boolean
  refreshing: boolean
  error: string | null
  hasMore: boolean
  loadMore: () => void
  refresh: () => void
}

/**
 * Escape a search term for a PostgREST filter value.
 *
 * Inside an `or=(...)` group, a bare `,` starts a new condition and `)` closes
 * the group, so an unescaped term could break the filter. Wrapping the value
 * in double quotes fixes that; within quotes only `\` and `"` need escaping.
 */
function quoteFilterValue(term: string): string {
  return term.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

/**
 * Server-side paged, searchable, newest-first lead list.
 *
 * Querying is always server-side: the browser never downloads the table to
 * filter it locally.
 */
export function useLeads(search: string): UseLeadsResult {
  const [leads, setLeads] = useState<Lead[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Debounced typing means several requests can be in flight. Only the newest
  // one may write to state, otherwise a slow earlier response can overwrite a
  // newer result set.
  const latestRequest = useRef(0)

  const fetchPage = useCallback(
    async (offset: number, limit: number, mode: FetchMode) => {
      const requestId = ++latestRequest.current

      if (mode === 'append') setLoadingMore(true)
      else if (mode === 'refresh') setRefreshing(true)
      else setLoading(true)
      setError(null)

      let query = supabase
        .from('leads')
        .select(LEAD_COLUMNS, { count: 'exact' })
        // created_at is unique in practice but nothing guarantees it. A second
        // key makes the sort a total order, so .range() pages cannot duplicate
        // or skip a row when two leads share a timestamp.
        .order('created_at', { ascending: false })
        .order('domain', { ascending: true })
        .range(offset, offset + limit - 1)

      const term = search.trim()
      if (term) {
        const value = quoteFilterValue(term)
        query = query.or(
          `business_name.ilike."%${value}%",domain.ilike."%${value}%"`,
        )
      }

      const { data, error: queryError, count } = await query.returns<Lead[]>()

      if (requestId !== latestRequest.current) return // superseded

      if (queryError) {
        setError(queryError.message)
      } else {
        const rows = data ?? []
        setTotal(count ?? rows.length)
        setLeads((previous) =>
          mode === 'append' ? [...previous, ...rows] : rows,
        )
      }

      setLoading(false)
      setLoadingMore(false)
      setRefreshing(false)
    },
    [search],
  )

  // Runs on mount and whenever the debounced search term changes, which also
  // resets pagination back to the first page.
  useEffect(() => {
    void fetchPage(0, PAGE_SIZE, 'replace')
  }, [fetchPage])

  const loadMore = useCallback(() => {
    void fetchPage(leads.length, PAGE_SIZE, 'append')
  }, [fetchPage, leads.length])

  // Refetch from the top, keeping however many rows are already on screen so
  // a refresh does not collapse the list back to one page.
  const refresh = useCallback(() => {
    void fetchPage(0, Math.max(leads.length, PAGE_SIZE), 'refresh')
  }, [fetchPage, leads.length])

  return {
    leads,
    total,
    loading,
    loadingMore,
    refreshing,
    error,
    hasMore: total !== null && leads.length < total,
    loadMore,
    refresh,
  }
}
