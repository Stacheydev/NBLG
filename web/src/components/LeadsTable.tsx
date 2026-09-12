import type { Lead } from '../types'
import { exactTime, hostname, instagramHandle, relativeTime } from '../lib/format'
import ExternalLink from './ExternalLink'

interface LeadsTableProps {
  leads: Lead[]
}

// Sticky lives on the CELLS, not the <tr>: with border-collapse a sticky
// row's own background and border are not reliably painted, so the rows
// underneath show through it. Each cell carries its own opaque background.
//
// The bottom rule is an inset shadow rather than border-b for the same
// reason - a collapsed border belongs to the table grid, not the cell, so it
// scrolls away from a sticky cell instead of travelling with it.
//
// top-14 matches the page header's fixed h-14, so the two stack flush.
const headerCell =
  'sticky top-14 z-10 bg-slate-50 px-4 py-2.5 text-left text-[11px] ' +
  'font-semibold uppercase tracking-wider text-slate-500 ' +
  'shadow-[inset_0_-1px_0_var(--color-slate-200)] ' +
  'dark:bg-slate-800 dark:text-slate-400 ' +
  'dark:shadow-[inset_0_-1px_0_var(--color-slate-700)]'

/** Desktop view. Hidden below `md`, where LeadCard takes over. */
export default function LeadsTable({ leads }: LeadsTableProps) {
  return (
    // overflow-hidden is deliberately absent: it makes this div a scroll
    // container, which is what `position: sticky` then resolves against
    // instead of the viewport. Corner clipping is replaced by rounding the
    // outer header cells and the final row's outer cells.
    <div className="hidden rounded-lg border border-slate-200 bg-white md:block dark:border-slate-800 dark:bg-slate-900">
      <table className="w-full table-fixed border-collapse text-sm [&>tbody>tr:last-child>td:first-child]:rounded-bl-lg [&>tbody>tr:last-child>td:last-child]:rounded-br-lg">
        <colgroup>
          <col className="w-[38%]" />
          <col className="w-[22%]" />
          <col className="w-[25%]" />
          <col className="w-[15%]" />
        </colgroup>

        <thead>
          <tr>
            {/* Outer cells round with the wrapper, which can no longer clip. */}
            <th scope="col" className={`${headerCell} rounded-tl-lg`}>
              Business
            </th>
            <th scope="col" className={headerCell}>
              Instagram
            </th>
            <th scope="col" className={headerCell}>
              Website
            </th>
            <th scope="col" className={`${headerCell} rounded-tr-lg`}>
              Added
            </th>
          </tr>
        </thead>

        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {leads.map((lead) => (
            <tr
              key={lead.domain}
              className="transition-colors hover:bg-slate-50/80 dark:hover:bg-slate-800/40"
            >
              <td className="px-4 py-3">
                <div className="truncate font-medium text-slate-900 dark:text-slate-100">
                  {lead.business_name}
                </div>
                <div className="truncate font-mono text-xs text-slate-500 dark:text-slate-400">
                  {lead.domain}
                </div>
              </td>

              <td className="px-4 py-3">
                {lead.instagram_url ? (
                  <ExternalLink href={lead.instagram_url}>
                    {instagramHandle(lead.instagram_url)}
                  </ExternalLink>
                ) : (
                  <span
                    className="text-slate-300 dark:text-slate-600"
                    title="No Instagram account was found for this business"
                  >
                    —
                  </span>
                )}
              </td>

              <td className="px-4 py-3">
                <ExternalLink href={lead.website_url}>
                  {hostname(lead.website_url)}
                </ExternalLink>
              </td>

              <td className="px-4 py-3">
                <span
                  className="text-slate-500 dark:text-slate-400"
                  title={exactTime(lead.created_at)}
                >
                  {relativeTime(lead.created_at)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
