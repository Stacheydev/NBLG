/**
 * An opportunity must be a PROBLEM.
 *
 * Regression suite for a real failure. For Orient 499 the analyzer produced:
 *
 *   "...the product cards on the homepage already show the price for each
 *    dress, like the Yasmine Crepe Silk Tareq Embroidered Dress at
 *    $2,145.00. It's great for shoppers to see cost upfront. I'm Hadi from
 *    North Bound - does that help your customers?"
 *
 * That message was well-grounded, inside the word limits, and useless: it
 * named no problem, complimented the prospect, and asked a question whose
 * answer is obvious. A separate live run leaked our storage format into the
 * copy ('each product entry has "price": null').
 */
import { describe, expect, it } from 'vitest'

import type { Evidence, PageEvidence } from '../api/_evidence.js'
import {
  checkIsProblem,
  checkMessage,
  checkNoInternalIdentifiers,
  validateAnalysis,
} from '../api/_prompt.js'

// ============================================================
// FIXTURES
// ============================================================

function page(overrides: Partial<PageEvidence> = {}): PageEvidence {
  return {
    url: 'https://orient499.com/',
    kind: 'homepage',
    title: 'Orient 499 - Lebanese Craft',
    headings: ['New arrivals'],
    nav_labels: ['Dresses', 'Kaftans', 'About'],
    collections: ['Dresses', 'Kaftans'],
    products: [
      { name: 'Yasmine Crepe Silk Tareq Embroidered Dress', price: '$2,145.00' },
      { name: 'Linen Kaftan', price: '$480.00' },
    ],
    ctas: ['Add to cart'],
    price_detection: 'present',
    search_detection: 'present',
    size_guide_detection: 'present',
    product_card_detection: 'present',
    ...overrides,
  }
}

/** Prices ARE visible - a price finding here must be refused. */
const PRICED: Evidence = {
  domain: 'orient499.com', pages: [page()], pages_fetched: 1,
}

/** The same shop with no prices on the cards - a finding is permissible. */
const UNPRICED: Evidence = {
  domain: 'orient499.com',
  pages: [page({
    products: [
      { name: 'Yasmine Crepe Silk Tareq Embroidered Dress', price: null },
      { name: 'Linen Kaftan', price: null },
    ],
    price_detection: 'absent',
  })],
  pages_fetched: 1,
}

const GOOD_MESSAGE =
  'Hey, I was looking at Orient 499 and noticed the pieces on the homepage '
  + 'do not show a price until you open each one. Shoppers comparing dresses '
  + 'often give up before that. I am Hadi from North Bound - was that '
  + 'deliberate?'

interface Opportunity {
  category: string
  problem_type: string
  observation: string
  shopper_impact: string
  evidence: string
  evidence_refs: string[]
  confidence: number
}

const VALID_OPPORTUNITY: Opportunity = {
  category: 'price visibility',
  problem_type: 'hidden',
  observation: 'The product cards do not show a price until you open the product.',
  shopper_impact: 'Shoppers cannot compare pieces without clicking each one.',
  evidence:
    'Yasmine Crepe Silk Tareq Embroidered Dress shows no price on the card',
  evidence_refs: ['pages[0].products[0].price', 'pages[0].price_detection'],
  confidence: 0.8,
}

function response(
  opportunity: Partial<Opportunity> = {},
  message: string = GOOD_MESSAGE,
) {
  return {
    status: 'success',
    website_opportunity: { ...VALID_OPPORTUNITY, ...opportunity },
    outreach_angle: 'price visibility',
    message,
  }
}

// ============================================================
// 1. VISIBLE PRICES MUST NOT PRODUCE A PRICE OPPORTUNITY
// ============================================================

describe('a working feature is never an opportunity', () => {
  it('rejects the exact Orient 499 failure', () => {
    const result = validateAnalysis(response(
      {
        problem_type: 'missing',
        observation:
          'The product cards on the homepage already show the price for each '
          + 'dress, like the Yasmine Crepe Silk Tareq Embroidered Dress at '
          + '$2,145.00.',
        shopper_impact: 'Shoppers see cost upfront.',
        evidence: 'Yasmine Crepe Silk Tareq Embroidered Dress at $2,145.00',
        evidence_refs: ['pages[0].products[0].price'],
      },
      'Hey, I was browsing Orient 499 and noticed that the product cards on '
      + 'the homepage already show the price for each dress, like the Yasmine '
      + 'Crepe Silk Tareq Embroidered Dress at $2,145.00. It is great for '
      + 'shoppers to see cost upfront. I am Hadi from North Bound - does that '
      + 'help your customers?',
    ), PRICED)

    expect(result.status).toBe('no_strong_opportunity')
    expect(result.message).toBeNull()
    expect(result.rejected_reason).toMatch(/exists and works|compliment/)
  })

  it('rejects "products show prices" however it is phrased', () => {
    for (const observation of [
      'Every product card displays its price clearly.',
      'The homepage shows the price for each dress.',
      'Prices are visible on all product cards.',
      'The store already displays prices upfront.',
    ]) {
      expect(checkIsProblem(observation).ok, `allowed: "${observation}"`)
        .toBe(false)
    }
  })

  it('rejects praise for search, size guides and navigation', () => {
    for (const observation of [
      'The site has a search box in the header.',
      'There is a size guide on the product pages.',
      'The navigation is clean and well organised.',
      'The layout is polished and professional.',
    ]) {
      expect(checkIsProblem(observation).ok, observation).toBe(false)
    }
  })
})

// ============================================================
// 2. MISSING PRICES MAY PRODUCE ONE
// ============================================================

describe('a genuine deficiency may be an opportunity', () => {
  it('accepts missing prices on an unpriced page', () => {
    const result = validateAnalysis(response(), UNPRICED)
    expect(result.status).toBe('success')
    expect(result.message).toBeTruthy()
    expect(result.rejected_reason).toBeNull()
  })

  it('recognises each deficiency phrasing as a problem', () => {
    for (const observation of [
      'The product cards do not show a price until you open the product.',
      'There is no way to filter dresses by size.',
      'Sizing information is missing from the clothing pages.',
      'The search returns an empty page for common terms.',
      'Prices are hidden behind a click.',
      'Shoppers have to open each product to see the cost.',
      'The collection names are inconsistent with the menu labels.',
      'The size guide link is broken.',
    ]) {
      expect(checkIsProblem(observation).ok, `rejected: "${observation}"`)
        .toBe(true)
    }
  })
})

// ============================================================
// 3. A POSITIVE FEATURE ALONE -> no_strong_opportunity
// ============================================================

describe('positive findings degrade to no_strong_opportunity', () => {
  it('downgrades rather than failing, and emits no message', () => {
    const result = validateAnalysis(response({
      category: 'search',
      problem_type: 'friction',
      observation: 'The storefront has a working search box in the header.',
      shopper_impact: 'Shoppers can find things.',
      evidence: 'a search field is present',
      evidence_refs: ['pages[0].search_detection'],
    }), PRICED)

    expect(result.status).toBe('no_strong_opportunity')
    expect(result.message).toBeNull()
    expect(result.opportunity_observation).toBeNull()
  })

  it('rejects a problem_type outside the deficiency enum', () => {
    for (const problem_type of ['strength', 'positive', 'good', '', 'none']) {
      const result = validateAnalysis(
        response({ problem_type }), UNPRICED,
      )
      expect(result.status, `allowed problem_type="${problem_type}"`)
        .toBe('no_strong_opportunity')
    }
  })

  it('accepts every deficiency problem_type', () => {
    for (const problem_type of [
      'missing', 'hidden', 'unclear', 'inconsistent', 'broken', 'friction',
    ]) {
      const result = validateAnalysis(response({ problem_type }), UNPRICED)
      expect(result.status, `rejected problem_type="${problem_type}"`)
        .toBe('success')
    }
  })
})

// ============================================================
// 4. NO INTERNAL EVIDENCE REPRESENTATION IN COPY
// ============================================================

describe('internal evidence representation never reaches a human', () => {
  it.each([
    ['shows_prices', 'The shows_prices flag is false on the homepage.'],
    ['price=null', 'Each product has price=null on the card.'],
    ['json null', 'Each product entry has "price": null in the homepage data.'],
    ['array index', 'The item at products[0] has no price.'],
    ['pages index', 'On pages[0] the cards show nothing.'],
    ['has_search', 'The has_search field is false.'],
    ['nav_labels', 'The nav_labels array is missing entries.'],
    ['evidence_refs', 'See evidence_refs for details.'],
  ])('rejects %s', (_label, text) => {
    expect(checkNoInternalIdentifiers(text, 'message').ok).toBe(false)
  })

  it('rejects a MESSAGE that leaks the storage format', () => {
    // Taken from a real live run.
    const check = checkMessage(
      'Hey, I was looking at Orient 499 and noticed each product entry has '
      + '"price": null so no cost is shown on the cards. Shoppers cannot '
      + 'compare without clicking. I am Hadi from North Bound - was that '
      + 'deliberate?',
    )
    expect(check.ok).toBe(false)
    expect(check.reason).toContain('internal evidence representation')
  })

  it('downgrades the whole analysis when the evidence field leaks', () => {
    const result = validateAnalysis(response({
      evidence: '"shows_prices": false and each product has "price": null',
    }), UNPRICED)

    expect(result.status).toBe('insufficient_evidence')
    expect(result.message).toBeNull()
    expect(result.rejected_reason).toContain('internal evidence representation')
  })

  it('allows plain-English evidence naming real visible things', () => {
    expect(checkNoInternalIdentifiers(
      'The Linen Kaftan and Yasmine dress both show no price on the card',
      'evidence',
    ).ok).toBe(true)
  })
})

// ============================================================
// 5. THE MESSAGE MUST RAISE A PROBLEM, NOT COMPLIMENT
// ============================================================

describe('the message raises a problem', () => {
  it.each([
    'It is great for shoppers to see cost upfront.',
    'Your navigation is really clean and well organised.',
    'I love your product photography.',
    'The cards already show prices, which is excellent.',
  ])('rejects a complimentary message: %s', (praise) => {
    const check = checkMessage(
      `Hey, I was looking at Orient 499 and noticed something. ${praise} `
      + 'I am Hadi from North Bound and wanted to reach you about the shop '
      + 'today - would you be open to hearing more about this idea?',
    )
    expect(check.ok).toBe(false)
  })

  it('accepts a problem-first message', () => {
    expect(checkMessage(GOOD_MESSAGE).ok).toBe(true)
  })

  it('still enforces the existing copy rules alongside the new ones', () => {
    // A problem-first message that asks for a call is still refused.
    expect(checkMessage(
      'Hey, I noticed the pieces on Orient 499 do not show a price until you '
      + 'open each one. Shoppers comparing dresses often give up. I am Hadi '
      + 'from North Bound - could we book a call this week to discuss it?',
    ).ok).toBe(false)
  })
})
