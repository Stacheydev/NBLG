import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { LEAD_ANALYSIS_COLUMNS, type Lead, type LeadAnalysis as StoredAnalysis } from '../types'
import { type AnalysisResponse, storedToResponse } from '../lib/analysis'

interface LeadAnalysisProps {
  lead: Lead
}

const label = 'text-[11px] font-semibold uppercase tracking-wider ' +
  'text-slate-400 dark:text-slate-500'

/**
 * The Analyze Lead panel.
 *
 * Human-triggered: one click, one analysis. Nothing analyses automatically,
 * which is what keeps the free provider tier workable.
 *
 * When no defensible observation exists the panel says so and shows NO
 * message. That is a correct outcome, not an error - a fabricated opener
 * sent to a real prospect is far more costly than a blank panel.
 */
export default function LeadAnalysis({ lead }: LeadAnalysisProps) {
  const [result, setResult] = useState<AnalysisResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [copied, setCopied] = useState(false)

  /**
   * Load any previously saved analysis, so a result survives a reload.
   *
   * Read straight through the existing Supabase client, under the same RLS
   * the lead list uses - no new endpoint, and no service-role key. A
   * `failed` row is ignored: it records that an attempt did not work, which
   * is not something to re-display as a result.
   */
  useEffect(() => {
    let cancelled = false

    void (async () => {
      const { data, error } = await supabase
        .from('lead_analyses')
        .select(LEAD_ANALYSIS_COLUMNS)
        .eq('domain', lead.domain)
        .maybeSingle<StoredAnalysis>()

      if (cancelled || error || !data || data.status === 'failed') return
      setResult(storedToResponse(data))
    })()

    return () => { cancelled = true }
  }, [lead.domain])

  async function analyze() {
    setRunning(true)
    setCopied(false)
    try {
      const { data } = await supabase.auth.getSession()
      const token = data.session?.access_token
      if (!token) {
        setResult({
          status: 'failed', opportunity: null, outreach_angle: null,
          message: null, error: 'Your session expired. Sign in again.',
        })
        return
      }

      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: {
          authorization: `Bearer ${token}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify({ domain: lead.domain }),
      })

      const body = (await response.json().catch(() => null)) as
        | AnalysisResponse
        | { error?: string }
        | null

      if (!response.ok) {
        setResult({
          status: 'failed', opportunity: null, outreach_angle: null,
          message: null,
          error: (body && 'error' in body && body.error)
            || 'Could not analyze this lead right now.',
        })
        return
      }
      setResult(body as AnalysisResponse)
    } catch {
      setResult({
        status: 'failed', opportunity: null, outreach_angle: null,
        message: null, error: 'Could not reach the analyzer.',
      })
    } finally {
      setRunning(false)
    }
  }

  async function copyText(text: string | null | undefined) {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2_000)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="mt-3 border-t border-slate-100 pt-3 dark:border-slate-800">
      <button
        type="button"
        onClick={analyze}
        disabled={running}
        className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium
          text-white transition hover:bg-slate-700 disabled:opacity-50
          dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
      >
        {running ? 'Analyzing…' : result ? 'Re-analyze' : 'Analyze lead'}
      </button>

      {result?.status === 'success' && result.opportunity && (
        <dl className="mt-3 space-y-2.5 text-sm">
          <div>
            <dt className={label}>
              Opportunity
              {result.opportunity.category ? ` · ${result.opportunity.category}` : ''}
              {typeof result.opportunity.confidence === 'number'
                ? ` · ${Math.round(result.opportunity.confidence * 100)}% confident`
                : ''}
            </dt>
            <dd className="mt-0.5 text-slate-800 dark:text-slate-200">
              {result.opportunity.observation}
            </dd>
          </div>

          <div>
            <dt className={label}>Evidence</dt>
            <dd className="mt-0.5 text-slate-600 dark:text-slate-400">
              {result.opportunity.evidence}
            </dd>
          </div>

          {result.outreach_angle && (
            <div>
              <dt className={label}>Outreach angle</dt>
              <dd className="mt-0.5 text-slate-600 dark:text-slate-400">
                {result.outreach_angle}
              </dd>
            </div>
          )}

          {result.message && (
            <div>
              <dt className={label}>Message</dt>
              <dd className="mt-1">
                <p className="whitespace-pre-wrap rounded-md bg-slate-50 p-2.5
                  text-slate-800 dark:bg-slate-800/60 dark:text-slate-200">
                  {result.message}
                </p>
                <button
                  type="button"
                  onClick={() => copyText(result.message)}
                  className="mt-1.5 rounded-md border border-slate-300 px-2.5
                    py-1 text-xs font-medium text-slate-700 transition
                    hover:bg-slate-50 dark:border-slate-600
                    dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  {copied ? 'Copied' : 'Copy message'}
                </button>
              </dd>
            </div>
          )}
        </dl>
      )}

      {/* No defensible observation.
          The generic opener is shown, but it must be impossible to mistake
          for an evidence-backed finding: its own amber-bordered block, its
          own heading, and the word "unverified" above the text. */}
      {(result?.status === 'no_strong_opportunity'
        || result?.status === 'insufficient_evidence') && (
        <div className="mt-2.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No strong outreach opportunity found.
          </p>

          {result.fallback_message && (
            <div className="mt-2 rounded-md border border-amber-400/70 bg-amber-50/70
              p-2.5 dark:border-amber-500/40 dark:bg-amber-500/10">
              <p className="text-[11px] font-semibold uppercase tracking-wider
                text-amber-800 dark:text-amber-400">
                ⚠ Generic fallback — unverified
              </p>
              <p className="mt-1 text-xs text-amber-900/80 dark:text-amber-300/80">
                The website analysis did not find a strong, evidence-backed
                issue. This opener makes no claim about this store — it only
                starts a conversation.
              </p>
              <p className="mt-2 whitespace-pre-wrap text-sm text-slate-800
                dark:text-slate-200">
                {result.fallback_message}
              </p>
              <button
                type="button"
                onClick={() => copyText(result.fallback_message)}
                className="mt-1.5 rounded-md border border-amber-500/60 px-2.5
                  py-1 text-xs font-medium text-amber-900 transition
                  hover:bg-amber-100/60 dark:border-amber-500/40
                  dark:text-amber-300 dark:hover:bg-amber-500/10"
              >
                {copied ? 'Copied' : 'Copy generic message'}
              </button>
            </div>
          )}
        </div>
      )}

      {result?.status === 'failed' && (
        <p className="mt-2.5 text-sm text-amber-700 dark:text-amber-500">
          {result.error ?? 'Could not analyze this lead right now.'}
        </p>
      )}
    </div>
  )
}
