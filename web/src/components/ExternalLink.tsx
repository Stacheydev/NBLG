import type { ReactNode } from 'react'

interface ExternalLinkProps {
  href: string
  title?: string
  children: ReactNode
}

/**
 * Every outbound lead link goes through here.
 *
 * These URLs are scraped from third-party sites, so `rel="noopener
 * noreferrer"` is mandatory: noopener stops the opened page reaching back
 * through window.opener, noreferrer withholds this dashboard's URL. Keeping it
 * in one component means no call site can forget it.
 */
export default function ExternalLink({
  href,
  title,
  children,
}: ExternalLinkProps) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={title ?? href}
      className="inline-flex max-w-full items-center gap-1 rounded text-blue-600 underline decoration-blue-600/25 underline-offset-2 transition-colors hover:text-blue-700 hover:decoration-blue-600/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400 dark:decoration-blue-400/25 dark:hover:text-blue-300"
    >
      <span className="truncate">{children}</span>
      <svg
        aria-hidden="true"
        viewBox="0 0 12 12"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="size-2.5 shrink-0 opacity-60"
      >
        <path d="M4.5 2.5h5v5" />
        <path d="M9.5 2.5 3 9" />
      </svg>
    </a>
  )
}
