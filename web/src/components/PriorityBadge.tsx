import type { Lead } from '../types'

interface PriorityBadgeProps {
  lead: Lead
}

const BAND_STYLE: Record<string, string> = {
  high: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20 ' +
    'dark:bg-emerald-500/10 dark:text-emerald-400 dark:ring-emerald-400/20',
  medium: 'bg-amber-50 text-amber-700 ring-amber-600/20 ' +
    'dark:bg-amber-500/10 dark:text-amber-400 dark:ring-amber-400/20',
  low: 'bg-slate-100 text-slate-600 ring-slate-500/20 ' +
    'dark:bg-slate-700/40 dark:text-slate-400 dark:ring-slate-400/20',
}

/**
 * Contact priority, 1-10 plus a band.
 *
 * Shared by the table and the card so the wording lives in one place. The
 * label says "heuristic" on purpose: this orders already-qualified leads by
 * observable signals, and is NOT a reply probability. Leads generated before
 * the feature existed are NULL and render as "Not scored" rather than 0.
 */
export default function PriorityBadge({ lead }: PriorityBadgeProps) {
  const { contact_priority_score: score, contact_priority: band } = lead

  if (score === null || band === null) {
    return (
      <span className="text-xs text-slate-400 dark:text-slate-500">
        Not scored
      </span>
    )
  }

  return (
    <span
      className="inline-flex items-baseline gap-1.5"
      // The component breakdown, so a score is always auditable against the
      // signals that produced it.
      title={lead.contact_priority_reasons ?? undefined}
    >
      <span className="font-medium tabular-nums text-slate-900 dark:text-slate-100">
        {score}
        <span className="text-slate-400 dark:text-slate-500">/10</span>
      </span>
      <span
        className={
          'rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ' +
          'tracking-wide ring-1 ring-inset ' +
          (BAND_STYLE[band] ?? BAND_STYLE.low)
        }
      >
        {band}
      </span>
    </span>
  )
}
