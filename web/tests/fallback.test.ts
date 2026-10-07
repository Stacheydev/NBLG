/**
 * The generic fallback: a message for every lead, without inventing a fault.
 *
 * Two modes that must never blur into each other.
 *
 *   VERIFIED  reliable evidence -> genuine problem -> validated opportunity
 *             -> `message`, which has passed every opportunity check.
 *
 *   FALLBACK  no reliable problem -> status no_strong_opportunity ->
 *             `fallback_message`, a conversation opener that claims nothing
 *             about the site, in its own field and explicitly unverified.
 *
 * The fallback exists so we can contact a lead WITHOUT the analyzer being
 * pressured into fabricating a criticism. A fallback carrying an invented
 * observation would defeat its own purpose, so it is held to every rule a
 * verified message is, plus one: it may assert nothing specific.
 */
import { describe, expect, it } from 'vitest'

import type { Evidence } from '../api/_evidence.js'
import {
  FALLBACK_CATEGORY,
  checkFallbackMessage,
  validateAnalysis,
} from '../api/_prompt.js'

// ============================================================
// FIXTURES
// ============================================================

const EVIDENCE: Evidence = {
  domain: 'guavaonlineshop.com',
  pages: [{
    url: 'https://guavaonlineshop.com/', kind: 'homepage',
    title: 'Guava Online Shop Lebanon',
    headings: ['New arrivals'],
    nav_labels: ['Dresses', 'Tops'],
    collections: ['Dresses', 'Tops'],
    products: [{ name: 'Cold-Shoulder Ribbed Top', price: '$18.00' }],
    ctas: ['Add to cart'],
    price_detection: 'present',
    search_detection: 'present',
    size_guide_detection: 'unknown',
    product_card_detection: 'present',
  }],
  pages_fetched: 1,
}

const GOOD_FALLBACK =
  'Hey Guava Lebanon, I was browsing your site and was curious how you are '
  + 'thinking about the overall shopping experience, from finding a product '
  + 'through to deciding whether to buy. I am Hadi from North Bound. Is that '
  + 'something you have been looking at?'

function declined(fallback: string | null = GOOD_FALLBACK) {
  return {
    status: 'no_strong_opportunity',
    website_opportunity: null,
    outreach_angle: null,
    message: null,
    fallback_message: fallback,
  }
}

// ============================================================
// THE FALLBACK IS RETURNED, AND MARKED UNVERIFIED
// ============================================================

describe('fallback mode', () => {
  it('returns a fallback when no strong opportunity is found', () => {
    const result = validateAnalysis(declined(), EVIDENCE)
    expect(result.status).toBe('no_strong_opportunity')
    expect(result.fallback_message).toBe(GOOD_FALLBACK)
  })

  it('marks the fallback explicitly unverified', () => {
    const result = validateAnalysis(declined(), EVIDENCE)
    expect(result.fallback_verified).toBe(false)
    expect(result.fallback_category).toBe(FALLBACK_CATEGORY)
    expect(FALLBACK_CATEGORY).toBe('generic_shopping_experience')
  })

  it('keeps the fallback OUT of the verified message field', () => {
    // The field a verified finding uses must stay null, so nothing
    // downstream can mistake an opener for an evidence-backed message.
    const result = validateAnalysis(declined(), EVIDENCE)
    expect(result.message).toBeNull()
    expect(result.opportunity_observation).toBeNull()
    expect(result.opportunity_evidence).toBeNull()
    expect(result.evidence_refs).toBeNull()
  })

  it('attaches a fallback to a downgraded opportunity too', () => {
    // A finding rejected for citing an unknown field still leaves Hadi
    // something to send.
    const result = validateAnalysis({
      status: 'success',
      website_opportunity: {
        category: 'price visibility', problem_type: 'missing',
        observation: 'The cards do not show a price.',
        shopper_impact: 'Shoppers cannot compare.',
        evidence: 'no price on the cards',
        evidence_refs: ['pages[0].size_guide_detection'],
        confidence: 0.9,
      },
      outreach_angle: 'price visibility',
      message:
        'Hey Guava Lebanon, I noticed the cards do not show a price until '
        + 'you open each one. Shoppers comparing tops often give up before '
        + 'that. I am Hadi from North Bound - was that deliberate?',
      fallback_message: GOOD_FALLBACK,
    }, EVIDENCE)

    expect(result.status).not.toBe('success')
    expect(result.message).toBeNull()
    expect(result.fallback_message).toBe(GOOD_FALLBACK)
    expect(result.fallback_verified).toBe(false)
  })

  it('a verified success carries no fallback', () => {
    const result = validateAnalysis({
      status: 'success',
      website_opportunity: {
        category: 'price visibility', problem_type: 'absent' in {} ? 'x' : 'missing',
        observation: 'None of the product cards show a price.',
        shopper_impact: 'Shoppers cannot compare without clicking.',
        evidence: 'the Cold-Shoulder Ribbed Top card shows no price',
        evidence_refs: ['pages[0].products[0].name'],
        confidence: 0.8,
      },
      outreach_angle: 'price visibility',
      message:
        'Hey Guava Lebanon, I noticed the tops on your homepage do not show '
        + 'a price until you open each one. Shoppers comparing pieces often '
        + 'give up before that. I am Hadi from North Bound - was that '
        + 'deliberate?',
      fallback_message: GOOD_FALLBACK,
    }, EVIDENCE)

    expect(result.status).toBe('success')
    expect(result.message).toBeTruthy()
    expect(result.fallback_message).toBeNull()
    expect(result.fallback_category).toBeNull()
    expect(result.fallback_verified).toBe(false)
  })
})

// ============================================================
// A FALLBACK MAY NOT FABRICATE A CRITICISM
// ============================================================

describe('a fallback claims nothing specific', () => {
  it('accepts a broad, honest opener', () => {
    expect(checkFallbackMessage(GOOD_FALLBACK).ok).toBe(true)
  })

  it.each([
    'I noticed your product discovery is difficult to navigate for shoppers.',
    'I saw that your store does not show prices anywhere on the homepage.',
    'I noticed there is no way to filter products on your collection pages.',
    'I spotted that your size guide is missing from the product pages.',
  ])('rejects a fabricated observation: %s', (claim) => {
    const check = checkFallbackMessage(
      `Hey Guava Lebanon, ${claim} I am Hadi from North Bound and wanted to `
      + 'ask about the shop experience today. Is that something you have '
      + 'been looking at recently?',
    )
    expect(check.ok).toBe(false)
  })

  it.each([
    'your checkout is confusing',
    'the navigation is unclear',
    'your product pages are hard',
  ])('rejects an unverified weakness: %s', (weakness) => {
    const check = checkFallbackMessage(
      `Hey Guava Lebanon, I was browsing and felt ${weakness} for a first `
      + 'time visitor arriving from social. I am Hadi from North Bound and '
      + 'work on this. Is that something you have been looking at?',
    )
    expect(check.ok).toBe(false)
  })

  it('rejects a fallback that pays a compliment', () => {
    expect(checkFallbackMessage(
      'Hey Guava Lebanon, your store looks great and the photography is '
      + 'beautiful throughout the whole site. I am Hadi from North Bound. Is '
      + 'the shopping experience something you have been looking at?',
    ).ok).toBe(false)
  })

  it('rejects a fallback leaking internal evidence identifiers', () => {
    expect(checkFallbackMessage(
      'Hey Guava Lebanon, I was browsing and the price_detection on your '
      + 'homepage came back unclear to me today. I am Hadi from North Bound. '
      + 'Is the shopping experience something you have looked at?',
    ).ok).toBe(false)
  })

  it('still enforces the ordinary copy rules', () => {
    for (const bad of [
      // asks for a call
      'Hey Guava Lebanon, I was browsing your site and wondered about the '
      + 'shopping experience overall. I am Hadi from North Bound - could we '
      + 'book a call this week to talk it through properly?',
      // offers an audit
      'Hey Guava Lebanon, I was browsing your site and wondered about the '
      + 'overall shopping experience there. I am Hadi from North Bound and '
      + 'would happily run a free audit for you - interested?',
      // too short
      'Hey Guava, thoughts on your shopping experience?',
      // no question
      'Hey Guava Lebanon, I was browsing your site and was curious how you '
      + 'are thinking about the overall shopping experience from finding a '
      + 'product through to deciding whether to buy it today.',
    ]) {
      expect(checkFallbackMessage(bad).ok, bad.slice(0, 50)).toBe(false)
    }
  })

  it('drops a fallback that fails its own validation rather than sending it', () => {
    const result = validateAnalysis(
      declined('I noticed your navigation is broken and confusing for users '
        + 'arriving on mobile devices from Instagram. I am Hadi from North '
        + 'Bound - is that something you have looked at?'),
      EVIDENCE,
    )
    expect(result.status).toBe('no_strong_opportunity')
    expect(result.fallback_message).toBeNull()
    expect(result.fallback_verified).toBe(false)
  })

  it('handles a missing fallback without failing the analysis', () => {
    const result = validateAnalysis(declined(null), EVIDENCE)
    expect(result.status).toBe('no_strong_opportunity')
    expect(result.fallback_message).toBeNull()
  })
})
