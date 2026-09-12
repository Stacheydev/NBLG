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
}

/** The columns the dashboard actually reads - no `select('*')`. */
export const LEAD_COLUMNS =
  'domain,business_name,instagram_url,website_url,created_at'
