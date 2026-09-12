import { useCallback, useEffect, useRef, useState } from 'react'
import { supabase } from '../lib/supabase'

// The workflow takes minutes, far longer than any serverless function may run,
// so the browser polls a cheap status endpoint rather than holding a request
// open. Measured runs complete in 2-7 minutes.
const POLL_INTERVAL_MS = 5_000
const MAX_WAIT_MS = 15 * 60 * 1_000

export type NoticeTone = 'success' | 'neutral' | 'error'

export interface GenerationNoticeData {
  tone: NoticeTone
  text: string
}

interface UseGenerationResult {
  generating: boolean
  notice: GenerationNoticeData | null
  dismissNotice: () => void
  generate: () => void
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Total leads, unfiltered by the current search.
 *
 * head + count asks Postgres for the count without transferring any rows, and
 * ignoring the search box is deliberate: the before/after difference must
 * describe the database, not the user's current filter.
 */
async function countLeads(): Promise<number | null> {
  const { count, error } = await supabase
    .from('leads')
    .select('domain', { count: 'exact', head: true })

  if (error) {
    console.error('Lead count failed:', error.message)
    return null
  }
  return count ?? 0
}

/** The signed-in user's bearer token for the server endpoints. */
async function authHeaders(): Promise<Record<string, string> | null> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  return token ? { authorization: `Bearer ${token}` } : null
}

function pluralLeads(count: number): string {
  return `${count} new lead${count === 1 ? '' : 's'} added`
}

/**
 * Runs the generation round trip: count, trigger, poll, count, refresh.
 *
 * `onLeadsChanged` is called once a run has finished so the visible list
 * refetches. It is held in a ref so `generate` stays referentially stable.
 */
export function useGeneration(onLeadsChanged: () => void): UseGenerationResult {
  const [generating, setGenerating] = useState(false)
  const [notice, setNotice] = useState<GenerationNoticeData | null>(null)

  // Guards a second click that lands before React has re-rendered the button
  // as disabled. State alone is not enough for that race.
  const busyRef = useRef(false)
  const unmountedRef = useRef(false)
  const onLeadsChangedRef = useRef(onLeadsChanged)

  useEffect(() => {
    onLeadsChangedRef.current = onLeadsChanged
  }, [onLeadsChanged])

  useEffect(() => {
    return () => {
      unmountedRef.current = true
    }
  }, [])

  const dismissNotice = useCallback(() => setNotice(null), [])

  const generate = useCallback(() => {
    if (busyRef.current) return
    busyRef.current = true
    setGenerating(true)
    setNotice(null)

    const settle = (tone: NoticeTone, text: string) => {
      busyRef.current = false
      if (unmountedRef.current) return
      setGenerating(false)
      setNotice({ tone, text })
    }

    void (async () => {
      try {
        const headers = await authHeaders()
        if (!headers) {
          settle('error', 'Your session has expired. Please sign in again.')
          return
        }

        const before = await countLeads()

        const started = await fetch('/api/generate', { method: 'POST', headers })
        if (started.status === 401) {
          settle('error', 'Your session has expired. Please sign in again.')
          return
        }
        if (!started.ok) {
          console.error('Trigger failed with status', started.status)
          settle('error', 'Generation failed. Please try again.')
          return
        }

        const trigger = (await started.json()) as { after?: number }
        const after = typeof trigger.after === 'number' ? trigger.after : 0

        if (!unmountedRef.current) {
          setNotice({
            tone: 'neutral',
            text: 'Generating new leads. This usually takes a few minutes…',
          })
        }

        // --- poll until the run resolves, or we give up waiting ---
        let state = 'queued'
        const deadline = Date.now() + MAX_WAIT_MS

        while (Date.now() < deadline) {
          await sleep(POLL_INTERVAL_MS)
          if (unmountedRef.current) {
            busyRef.current = false
            return
          }

          const probe = await fetch(`/api/generate-status?after=${after}`, {
            headers,
          })

          if (probe.status === 401) {
            settle('error', 'Your session has expired. Please sign in again.')
            return
          }
          // A transient blip must not abandon a run that is still going; the
          // next tick tries again.
          if (!probe.ok) continue

          const body = (await probe.json()) as { state?: string }
          state = body.state ?? 'queued'

          if (state === 'completed_success' || state === 'completed_failure') {
            break
          }
        }

        // --- report, and always refresh so partial results are visible ---
        const total = await countLeads()
        onLeadsChangedRef.current()

        const added = before !== null && total !== null ? total - before : null

        if (state === 'completed_success') {
          if (added === null) settle('neutral', 'Generation finished.')
          else if (added > 0) settle('success', pluralLeads(added))
          else settle('neutral', 'No new leads found')
          return
        }

        if (state === 'completed_failure') {
          // The workflow syncs whatever the generator managed to save before
          // failing, so leads can exist even on a red run. Saying "failed"
          // while new rows appeared would be misleading.
          if (added !== null && added > 0) {
            settle('success', `${pluralLeads(added)}, but the run ended early`)
          } else {
            settle('error', 'Generation failed. Please try again.')
          }
          return
        }

        settle(
          'neutral',
          'Still generating. New leads will appear here once the run finishes.',
        )
      } catch (cause) {
        console.error('Generation failed:', cause)
        settle('error', 'Generation failed. Please try again.')
      }
    })()
  }, [])

  return { generating, notice, dismissNotice, generate }
}
