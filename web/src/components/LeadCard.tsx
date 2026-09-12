import type { Lead } from '../types'
import { exactTime, hostname, instagramHandle, relativeTime } from '../lib/format'
import ExternalLink from './ExternalLink'

interface LeadCardProps {
  lead: Lead
}

/** Mobile view. A stacked card instead of squeezing the table onto a phone. */
export default function LeadCard({ lead }: LeadCardProps) {
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate font-medium text-slate-900 dark:text-slate-100">
            {lead.business_name}
          </h2>
          <p className="truncate font-mono text-xs text-slate-500 dark:text-slate-400">
            {lead.domain}
          </p>
        </div>
        <span
          className="shrink-0 whitespace-nowrap text-xs text-slate-400 dark:text-slate-500"
          title={exactTime(lead.created_at)}
        >
          {relativeTime(lead.created_at)}
        </span>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm">
        <ExternalLink href={lead.website_url}>
          {hostname(lead.website_url)}
        </ExternalLink>

        {lead.instagram_url ? (
          <ExternalLink href={lead.instagram_url}>
            {instagramHandle(lead.instagram_url)}
          </ExternalLink>
        ) : (
          <span className="text-xs text-slate-400 dark:text-slate-500">
            No Instagram
          </span>
        )}
      </div>
    </li>
  )
}
