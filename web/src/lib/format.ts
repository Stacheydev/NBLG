const relativeFormatter = new Intl.RelativeTimeFormat(undefined, {
  numeric: 'auto',
})

const MINUTE = 60
const HOUR = 3600
const DAY = 86_400
const MONTH = 2_592_000 // 30 days
const YEAR = 31_536_000 // 365 days

/** "3 days ago" / "yesterday". Falls back to an em dash on a bad value. */
export function relativeTime(iso: string): string {
  const timestamp = new Date(iso).getTime()
  if (Number.isNaN(timestamp)) return '—'

  const seconds = Math.round((timestamp - Date.now()) / 1000)
  const magnitude = Math.abs(seconds)

  if (magnitude < MINUTE) return relativeFormatter.format(seconds, 'second')
  if (magnitude < HOUR) {
    return relativeFormatter.format(Math.round(seconds / MINUTE), 'minute')
  }
  if (magnitude < DAY) {
    return relativeFormatter.format(Math.round(seconds / HOUR), 'hour')
  }
  if (magnitude < MONTH) {
    return relativeFormatter.format(Math.round(seconds / DAY), 'day')
  }
  if (magnitude < YEAR) {
    return relativeFormatter.format(Math.round(seconds / MONTH), 'month')
  }
  return relativeFormatter.format(Math.round(seconds / YEAR), 'year')
}

/** The full timestamp, for a `title` tooltip beside the relative one. */
export function exactTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

/**
 * '@handle' from a canonical Instagram profile URL.
 *
 * The sync only ever stores 'https://www.instagram.com/<handle>/', but this
 * degrades to a generic label rather than throwing if that ever changes.
 */
export function instagramHandle(url: string): string {
  const match = url.match(/instagram\.com\/([^/?#]+)/i)
  return match ? `@${match[1]}` : 'Instagram'
}

/** Display host for a website link, e.g. 'www.example.com'. */
export function hostname(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}
