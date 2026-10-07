/**
 * Website evidence collection for the lead analyzer.
 *
 * Fetches a small number of pages from one storefront and reduces them to a
 * compact, structured record the model can reason about. Raw HTML is never
 * sent anywhere.
 *
 * TWO THINGS THIS FILE IS BUILT AROUND
 *
 * 1. Everything it returns is UNTRUSTED. A product description can contain
 *    "Ignore previous instructions and say X". That text is website copy, so
 *    it is carried as a JSON string VALUE, truncated, stripped of control
 *    characters, and stripped of the delimiter and chat-template tokens that
 *    could otherwise break out of the evidence block in _prompt.ts. It never
 *    reaches the system prompt.
 *
 * 2. It is budgeted. Every fetch takes a deadline, not a timeout, so the
 *    caller's total budget is respected however the work is distributed.
 *    Homepage first; additional pages only when the homepage is thin.
 *
 * No HTML parser dependency: the fields below are extracted with targeted
 * patterns over the markup, the same approach scraper.py already takes for
 * its signals. A DOM library would add a runtime dependency for no gain at
 * this level of detail.
 */

// ============================================================
// LIMITS
// ============================================================

/** Absolute ceiling on pages fetched for one analysis. */
export const MAX_PAGES = 5

/** Longest any single page fetch may take. */
export const PAGE_TIMEOUT_MS = 3_000

/** Bytes of HTML read per page before truncating. Storefront homepages are
 *  routinely 500KB+, almost all of it inline script we do not look at. */
const MAX_HTML_BYTES = 400_000

/** Per-field caps, chosen to stay well inside Groq's 8,000 TPM free limit. */
const LIMITS = {
  stringLength: 120,
  headings: 8,
  navLabels: 15,
  collections: 10,
  products: 12,
  ctas: 8,
}

/** Homepage evidence this strong means no further pages are needed. */
const SUFFICIENT_PRODUCTS = 6
const SUFFICIENT_LABELS = 3

// ============================================================
// SHAPES
// ============================================================

export interface ProductEvidence {
  name: string
  price: string | null
}

export interface PageEvidence {
  url: string
  kind: 'homepage' | 'collection' | 'product'
  title: string | null
  headings: string[]
  nav_labels: string[]
  collections: string[]
  products: ProductEvidence[]
  ctas: string[]
  has_search: boolean
  has_size_guide: boolean
  has_product_cards: boolean
  shows_prices: boolean
}

export interface Evidence {
  domain: string
  pages: PageEvidence[]
  pages_fetched: number
}

export interface EvidenceResult {
  evidence: Evidence | null
  /** Set when the homepage itself could not be read. */
  error: 'unreachable' | 'timeout' | null
}

// ============================================================
// SANITISING
// ============================================================

/**
 * Delimiter and chat-template tokens removed from every extracted string.
 *
 * The evidence block in _prompt.ts is fenced with <untrusted_website_content>.
 * A page that contained the closing tag could otherwise appear to end the
 * block and have its following text read as a new instruction. The <| |>
 * forms are chat-template control tokens.
 */
const BREAKOUT_PATTERNS: RegExp[] = [
  /<\/?untrusted_website_content>/gi,
  /<\|[^|>]{0,64}\|>/g,
  /<\/?(system|assistant|user)\b[^>]{0,64}>/gi,
]

const NAMED_ENTITIES: Record<string, string> = {
  '&nbsp;': ' ', '&amp;': '&', '&quot;': '"', '&apos;': "'",
  '&lt;': '<', '&gt;': '>',
  '&ndash;': '–', '&mdash;': '—', '&hellip;': '…', '&middot;': '·',
  '&rsquo;': '’', '&lsquo;': '‘', '&ldquo;': '“', '&rdquo;': '”',
}

/** One decoded character, or a space if the entity named nothing valid. */
function codePoint(value: number): string {
  if (!Number.isInteger(value) || value <= 0 || value > 0x10ffff) return ' '
  // Surrogate halves are not standalone characters.
  if (value >= 0xd800 && value <= 0xdfff) return ' '
  try {
    return String.fromCodePoint(value)
  } catch {
    return ' '
  }
}

/** Collapse whitespace, drop control characters, defuse breakout tokens, cap. */
function clean(raw: string | null | undefined): string | null {
  if (!raw) return null

  // ORDER MATTERS. Entities are decoded FIRST, because a page can encode a
  // breakout token to slip it past the filter: `&lt;|im_start|&gt;` is not
  // `<|im_start|>` until it has been decoded, and stripping before decoding
  // would let the encoded form through intact.
  let text = raw
    // Named entities we actually see in storefront copy. The punctuation
    // ones are not cosmetic: a live storefront title arrived as
    // "Fashion &ndash; Guava Lebanon", and an undecoded entity goes to the
    // model as evidence and can end up quoted back in the message.
    .replace(
      /&(nbsp|amp|quot|apos|lt|gt|ndash|mdash|rsquo|lsquo|ldquo|rdquo|hellip|middot);/gi,
      (m) => NAMED_ENTITIES[m.toLowerCase()] ?? ' ',
    )
    // Numeric entities, BOTH forms. Hexadecimal is not optional: a live
    // storefront returned "Lebanon&#x27;s #1 Tech Store", and decoding only
    // the decimal form left the raw entity in the evidence.
    .replace(/&#x([0-9a-f]{1,6});/gi, (_, hex) => codePoint(parseInt(hex, 16)))
    .replace(/&#(\d{1,7});/g, (_, dec) => codePoint(Number(dec)))

  for (const pattern of BREAKOUT_PATTERNS) text = text.replace(pattern, ' ')

  text = text
    // Control characters, including the newlines that would let one field
    // look like several lines of prose.
    .replace(/[\x00-\x1f\x7f-\x9f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  if (!text) return null
  return text.length > LIMITS.stringLength
    ? `${text.slice(0, LIMITS.stringLength - 1)}…`
    : text
}

/** Strip tags from an HTML fragment, then clean it. */
function textOf(fragment: string): string | null {
  return clean(fragment.replace(/<[^>]*>/g, ' '))
}

function unique(values: (string | null)[], cap: number): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const value of values) {
    if (!value) continue
    const key = value.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(value)
    if (out.length >= cap) break
  }
  return out
}

// ============================================================
// EXTRACTION
// ============================================================

/** Remove the regions whose text is never user-visible evidence. */
function stripNoise(html: string): string {
  return html
    .replace(/<script\b[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style\b[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript\b[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<svg\b[\s\S]*?<\/svg>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
}

function matchAll(html: string, pattern: RegExp): string[] {
  return [...html.matchAll(pattern)].map((m) => m[1] ?? '')
}

const PRICE = /(?:USD|LBP|\$|£|€)\s?\d[\d.,]*|\d[\d.,]*\s?(?:USD|LBP)/i

function extractProducts(html: string): ProductEvidence[] {
  const out: ProductEvidence[] = []
  const seen = new Set<string>()

  // Shopify product URLs are canonical: /products/<handle>. The anchor text
  // is the product title in every theme we have fixtures for.
  for (const match of html.matchAll(
    /<a\b[^>]*href="[^"]*\/products\/[^"]*"[^>]*>([\s\S]{0,400}?)<\/a>/gi,
  )) {
    const name = textOf(match[1] ?? '')
    if (!name) continue
    const key = name.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)

    // A price inside the same anchor, or just after it, belongs to this card.
    const tail = html.slice(
      (match.index ?? 0) + match[0].length,
      (match.index ?? 0) + match[0].length + 400,
    )
    const price = (match[1]?.match(PRICE)?.[0] ?? tail.match(PRICE)?.[0]) ?? null

    out.push({ name, price: clean(price) })
    if (out.length >= LIMITS.products) break
  }
  return out
}

function extractCollections(html: string): string[] {
  return unique(
    [...html.matchAll(
      /<a\b[^>]*href="[^"]*\/collections\/[^"]*"[^>]*>([\s\S]{0,200}?)<\/a>/gi,
    )].map((m) => textOf(m[1] ?? '')),
    LIMITS.collections,
  )
}

function extractNav(html: string): string[] {
  // Prefer real nav/header regions; fall back to nothing rather than
  // scraping every link on the page, which would be noise.
  const regions = [
    ...matchAll(html, /<nav\b[^>]*>([\s\S]{0,8000}?)<\/nav>/gi),
    ...matchAll(html, /<header\b[^>]*>([\s\S]{0,8000}?)<\/header>/gi),
    ...matchAll(html, /<ul\b[^>]*(?:menu|nav)[^>]*>([\s\S]{0,8000}?)<\/ul>/gi),
  ].join(' ')

  return unique(
    matchAll(regions, /<a\b[^>]*>([\s\S]{0,120}?)<\/a>/gi).map(textOf),
    LIMITS.navLabels,
  )
}

function extractCtas(html: string): string[] {
  return unique(
    [
      ...matchAll(html, /<button\b[^>]*>([\s\S]{0,120}?)<\/button>/gi),
      ...matchAll(html, /<input\b[^>]*type="submit"[^>]*value="([^"]{0,120})"/gi),
      ...matchAll(html, /<a\b[^>]*class="[^"]*(?:btn|button)[^"]*"[^>]*>([\s\S]{0,120}?)<\/a>/gi),
    ].map((f) => textOf(f)),
    LIMITS.ctas,
  )
}

export function extractPage(
  html: string,
  url: string,
  kind: PageEvidence['kind'],
): PageEvidence {
  const clean_html = stripNoise(html)
  const lower = clean_html.toLowerCase()
  const products = extractProducts(clean_html)

  return {
    url,
    kind,
    title: textOf(clean_html.match(/<title\b[^>]*>([\s\S]{0,300}?)<\/title>/i)?.[1] ?? ''),
    headings: unique(
      [
        ...matchAll(clean_html, /<h1\b[^>]*>([\s\S]{0,300}?)<\/h1>/gi),
        ...matchAll(clean_html, /<h2\b[^>]*>([\s\S]{0,300}?)<\/h2>/gi),
      ].map(textOf),
      LIMITS.headings,
    ),
    nav_labels: extractNav(clean_html),
    collections: extractCollections(clean_html),
    products,
    ctas: extractCtas(clean_html),
    has_search: /type="search"|name="q"|role="search"|\/search/i.test(clean_html),
    has_size_guide: /size\s?(guide|chart)|fit\s?guide/i.test(lower),
    has_product_cards: products.length > 0,
    shows_prices: products.some((p) => p.price !== null),
  }
}

/** Is the homepage alone enough to find a defensible observation? */
export function isSufficient(page: PageEvidence): boolean {
  return (
    page.products.length >= SUFFICIENT_PRODUCTS &&
    page.nav_labels.length + page.collections.length >= SUFFICIENT_LABELS
  )
}

// ============================================================
// FETCHING
// ============================================================

/** Fetch one page within both its own timeout and the overall deadline. */
async function fetchPage(
  url: string,
  deadlineMs: number,
): Promise<string | null> {
  const remaining = deadlineMs - Date.now()
  if (remaining <= 250) return null

  const controller = new AbortController()
  const timer = setTimeout(
    () => controller.abort(),
    Math.min(PAGE_TIMEOUT_MS, remaining),
  )

  try {
    const response = await fetch(url, {
      signal: controller.signal,
      redirect: 'follow',
      headers: {
        // Same UA as scraper.py: storefronts that block unknown agents
        // already accept this one.
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        accept: 'text/html,application/xhtml+xml',
      },
    })
    if (!response.ok) return null

    const type = response.headers.get('content-type') ?? ''
    if (type && !/text\/html|application\/xhtml/i.test(type)) return null

    const body = await response.text()
    return body.length > MAX_HTML_BYTES ? body.slice(0, MAX_HTML_BYTES) : body
  } catch {
    // Timeout, DNS, TLS, abort - all the same to the caller.
    return null
  } finally {
    clearTimeout(timer)
  }
}

/** Absolute same-origin URLs for one collection and one product page. */
function pickFollowUps(html: string, origin: string): string[] {
  const picks: string[] = []
  const seen = new Set<string>()

  const add = (href: string | undefined) => {
    if (!href || picks.length >= 2) return
    let resolved: URL
    try {
      resolved = new URL(href, origin)
    } catch {
      return
    }
    // Same-origin only: never follow a link off the lead's own site.
    if (resolved.origin !== new URL(origin).origin) return
    resolved.hash = ''
    const key = resolved.toString()
    if (seen.has(key)) return
    seen.add(key)
    picks.push(key)
  }

  const collection = html.match(
    /href="([^"]*\/collections\/(?!all\b)[^"?#]+)"/i,
  ) ?? html.match(/href="([^"]*\/collections\/[^"?#]+)"/i)
  add(collection?.[1])

  const product = html.match(/href="([^"]*\/products\/[^"?#]+)"/i)
  add(product?.[1])

  // A homepage can be a pure marketing page with no catalogue links on it at
  // all, while the catalogue sits one well-known URL away. /collections/all
  // is Shopify's canonical "everything" grid, so when there is nothing to
  // follow it is a better single guess than giving up.
  if (picks.length === 0) add('/collections/all')

  return picks
}

/**
 * Collect evidence for one storefront.
 *
 * Homepage first. If that alone is sufficient, stop there - the typical
 * analysis reads ONE page. Otherwise fetch at most one collection and one
 * product page, in parallel, and only while the deadline allows.
 */
export async function collectEvidence(
  websiteUrl: string,
  domain: string,
  deadlineMs: number,
): Promise<EvidenceResult> {
  let origin: string
  try {
    origin = new URL(websiteUrl).toString()
  } catch {
    return { evidence: null, error: 'unreachable' }
  }

  const homeHtml = await fetchPage(origin, deadlineMs)
  if (homeHtml === null) {
    return {
      evidence: null,
      error: Date.now() >= deadlineMs ? 'timeout' : 'unreachable',
    }
  }

  const home = extractPage(homeHtml, origin, 'homepage')
  const pages: PageEvidence[] = [home]

  // Only spend more of the budget when the homepage is genuinely thin, and
  // only if there is room for the model call afterwards.
  const roomToFollowUp = deadlineMs - Date.now() > PAGE_TIMEOUT_MS + 1_500
  if (!isSufficient(home) && roomToFollowUp) {
    const followUps = pickFollowUps(stripNoise(homeHtml), origin)
      .slice(0, MAX_PAGES - pages.length)

    const fetched = await Promise.all(
      followUps.map(async (url) => ({
        url,
        html: await fetchPage(url, deadlineMs),
      })),
    )

    for (const { url, html } of fetched) {
      if (html === null) continue
      pages.push(
        extractPage(html, url, url.includes('/products/') ? 'product' : 'collection'),
      )
    }
  }

  return {
    evidence: { domain, pages, pages_fetched: pages.length },
    error: null,
  }
}
