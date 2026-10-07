import { useState } from 'react'
import { supabase } from '../lib/supabase'
import type { Lead } from '../types'

interface LeadAnalysisProps {
  lead: Lead
}

interface Opportunity {
  category: string | null
  observation: string | null
  evidence: string | null
  confidence: number | null
}

interface AnalysisResponse {
  status: 'success' | 'no_strong_opportunity' | 'insufficient_evidence' | 'failed'
  opportunity: Opportunity | null
  outreach_angle: string | null
  message: string | null
  pages_fetched?: number
  error?: string
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

  async function copyMessage() {
    if (!result?.message) return
    try {
      await navigator.clipboard.writeText(result.message)
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
                  onClick={copyMessage}
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

      {/* No defensible observation. Deliberately no message. */}
      {(result?.status === 'no_strong_opportunity'
        || result?.status === 'insufficient_evidence') && (
        <p className="mt-2.5 text-sm text-slate-500 dark:text-slate-400">
          No strong outreach opportunity found.
        </p>
      )}

      {result?.status === 'failed' && (
        <p className="mt-2.5 text-sm text-amber-700 dark:text-amber-500">
          {result.error ?? 'Could not analyze this lead right now.'}
        </p>
      )}
    </div>
  )
}
