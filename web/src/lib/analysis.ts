// The .js extension is required, not optional: the tests under web/tests
// import this module, and they are typechecked under nodenext, which does
// not guess extensions. Vite maps it back to types.ts for the browser build.
import type { LeadAnalysis as StoredAnalysis } from '../types.js'

/**
 * The shape `POST /api/analyze` returns, and the shape the UI renders.
 *
 * A stored row is mapped into this same shape on load, so a reloaded
 * analysis goes through exactly the same render branches as a freshly
 * generated one. That is what stops a persisted fallback from being drawn
 * as anything other than an unverified fallback.
 */
export interface Opportunity {
  category: string | null
  observation: string | null
  evidence: string | null
  confidence: number | null
}

export interface AnalysisResponse {
  status: 'success' | 'no_strong_opportunity' | 'insufficient_evidence' | 'failed'
  opportunity: Opportunity | null
  outreach_angle: string | null
  /** Evidence-backed. Only ever present when status is success. */
  message: string | null
  /** A generic opener. NOT a verified finding. */
  fallback_message?: string | null
  fallback_category?: string | null
  fallback_verified?: boolean
  pages_fetched?: number
  error?: string
}

/**
 * A stored `lead_analyses` row as an AnalysisResponse.
 *
 * Two normalisations, both deliberate:
 *
 *   - `opportunity` is built only for `success`. A row that was downgraded
 *     cannot present leftover opportunity columns as a finding.
 *   - `fallback_verified` is forced to false. A fallback is unverified by
 *     definition, so no stored value could make it otherwise, and reading
 *     the column optimistically is the one mistake that would matter.
 */
export function storedToResponse(row: StoredAnalysis): AnalysisResponse {
  return {
    status: row.status,
    opportunity:
      row.status === 'success'
        ? {
            category: row.opportunity_category,
            observation: row.opportunity_observation,
            evidence: row.opportunity_evidence,
            confidence: row.opportunity_confidence,
          }
        : null,
    outreach_angle: row.outreach_angle,
    // A verified message is only ever honoured on a success row.
    message: row.status === 'success' ? row.message : null,
    fallback_message: row.status === 'success' ? null : row.fallback_message,
    fallback_category: row.status === 'success' ? null : row.fallback_category,
    fallback_verified: false,
    pages_fetched: row.pages_fetched ?? undefined,
  }
}
