/** One row of `public.leads`, exactly as the sync script writes it. */
export interface Lead {
  /** Primary key. The generator's stable identifier for a business. */
  domain: string
  business_name: string
  /** NULL whenever the generator found no handle it trusted. */
  instagram_url: string | null
  website_url: string
  /** ISO-8601 UTC. The generator's real discovery time, not the sync time. */
  created_at: string

  /**
   * Contact priority - a HEURISTIC ordering of already-qualified leads,
   * 1-10. NOT a reply probability and not a chance of response: the project
   * has no labelled outreach outcomes to calibrate one. See scoring.py.
   *
   * NULL for every lead generated before this feature existed. Those are
   * deliberately not backfilled, so the UI shows them as "not scored".
   */
  contact_priority_score: number | null
  contact_priority: 'high' | 'medium' | 'low' | null
  /** Per-component breakdown, e.g. "reach=4.5(ig,wa,phone) relevance=3(...)". */
  contact_priority_reasons: string | null
}

/** The columns the dashboard actually reads - no `select('*')`. */
export const LEAD_COLUMNS =
  'domain,business_name,instagram_url,website_url,created_at,' +
  'contact_priority_score,contact_priority,contact_priority_reasons'

/** One row of `public.lead_analyses`. Written only by `POST /api/analyze`. */
export interface LeadAnalysis {
  domain: string
  status: 'success' | 'no_strong_opportunity' | 'insufficient_evidence' | 'failed'
  /** All of these are NULL unless `status` is `success`. */
  opportunity_category: string | null
  opportunity_observation: string | null
  opportunity_evidence: string | null
  opportunity_confidence: number | null
  outreach_angle: string | null
  /** The EVIDENCE-BACKED message. Non-null only when status is `success`. */
  message: string | null

  /**
   * The generic opener, used when no verified opportunity was found.
   *
   * A separate column from `message` on purpose: anything in `message` has
   * passed opportunity validation and anything here explicitly has not, so
   * the two can never be confused by a reader - including after a reload.
   */
  fallback_message: string | null
  fallback_category: string | null
  /** Always false when a fallback exists. Never true. */
  fallback_verified: boolean | null

  model: string | null
  pages_fetched: number | null
  analyzed_at: string
}

export const LEAD_ANALYSIS_COLUMNS =
  'domain,status,opportunity_category,opportunity_observation,' +
  'opportunity_evidence,opportunity_confidence,outreach_angle,message,' +
  'fallback_message,fallback_category,fallback_verified,' +
  'model,pages_fetched,analyzed_at'
