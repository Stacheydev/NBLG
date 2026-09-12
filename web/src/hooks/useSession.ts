import { useEffect, useState } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase } from '../lib/supabase'

interface UseSessionResult {
  session: Session | null
  /** True until the stored session has been restored (or ruled out). */
  loading: boolean
}

/**
 * The current Supabase session, restored on mount and kept in sync.
 *
 * getSession() reads the persisted session so a reload does not bounce the
 * user back to the login screen; onAuthStateChange keeps it current for
 * sign-in, sign-out and silent token refreshes.
 */
export function useSession(): UseSessionResult {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true

    void supabase.auth.getSession().then(({ data, error }) => {
      if (!active) return
      if (error) {
        // A corrupt or expired stored session is not fatal: fall through to
        // the login screen rather than rendering a broken dashboard.
        console.error('Could not restore session:', error.message)
      }
      setSession(data.session)
      setLoading(false)
    })

    const { data: listener } = supabase.auth.onAuthStateChange(
      (_event, nextSession) => {
        setSession(nextSession)
        setLoading(false)
      },
    )

    return () => {
      active = false
      listener.subscription.unsubscribe()
    }
  }, [])

  return { session, loading }
}
