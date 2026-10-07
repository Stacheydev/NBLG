/**
 * Copy rules, grounding, and model-output validation.
 *
 * No network and no API key: every function under test is pure. The bias
 * these tests encode is deliberate - a lost good analysis costs one click,
 * a hallucinated observation sent to a real prospect costs credibility.
 */
import { describe, expect, it } from 'vitest'

import type { Evidence, PageEvidence } from '../api/_evidence.js'
import {
  BANNED_PHRASES,
  WORDS_HARD_MAX,
  WORDS_HARD_MIN,
  buildMessages,
  checkGrounding,
  checkMessage,
  contentTokens,
  NOT_FOUND,
  hasEnoughEvidence,
  resolveRef,
  validateAnalysis,
  wordCount,
} from '../api/_prompt.js'

// ============================================================
// FIXTURES
// ============================================================

function page(overrides: Partial<PageEvidence> = {}): PageEvidence {
  return {
    url: 'https://qatfa.com/',
    kind: 'homepage',
    title: 'Qatfa — Handmade Lebanese Ceramics',
    headings: ['New arrivals', 'Shop all'],
    nav_labels: ['Shop', 'Collections', 'About', 'Contact'],
    collections: ['Mugs', 'Bowls', 'Vases'],
    products: [
      { name: 'Cedar Mug', price: 'USD 18' },
      { name: 'Olive Bowl', price: null },
      { name: 'Beirut Vase', price: null },
    ],
    ctas: ['Add to cart', 'Shop now'],
    price_detection: 'present',
    search_detection: 'present',
    size_guide_detection: 'unknown',
    product_card_detection: 'present',
    ...overrides,
  }
}

function evidence(overrides: Partial<Evidence> = {}): Evidence {
  return { domain: 'qatfa.com', pages: [page()], pages_fetched: 1, ...overrides }
}

/** A model response that should pass every check. */
function goodResponse(messageText?: string) {
  return {
    status: 'success',
    website_opportunity: {
      category: 'price visibility',
      // problem_type and shopper_impact are required: an opportunity has to
      // name WHICH KIND of deficiency it is, so a working feature cannot be
      // returned as a finding.
      problem_type: 'hidden',
      observation:
        'Most product cards on the homepage show no price until you open the product.',
      shopper_impact: 'Shoppers cannot compare pieces without clicking each one.',
      evidence: 'Olive Bowl and Beirut Vase have no price on the card',
      evidence_refs: ['pages[0].products[1].name', 'pages[0].products[2].name'],
      confidence: 0.8,
    },
    outreach_angle: 'price visibility on product cards',
    message:
      messageText ??
      'Hey! I came across Qatfa and noticed most of the product cards on your '
      + 'homepage do not show a price until you click through. It stood out '
      + 'because shoppers browsing ceramics usually compare before clicking. '
      + 'I am Hadi from North Bound. Would you be open to me showing you what '
      + 'I mean?',
  }
}

/** Build a message of exactly `n` words, ending in a question. */
function messageOf(n: number): string {
  const words = ['Hey', 'I', 'saw', 'Qatfa', 'and', 'noticed', 'the', 'prices',
    'are', 'hidden', 'on', 'cards', 'which', 'seemed', 'worth', 'a', 'mention',
    'since', 'shoppers', 'compare', 'first']
  const out: string[] = []
  while (out.length < n - 1) out.push(words[out.length % words.length]!)
  out.push('interested?')
  return out.join(' ')
}

// ============================================================
// WORD COUNT
// ============================================================

describe('message word count', () => {
  it('counts words, not characters', () => {
    expect(wordCount('one two three')).toBe(3)
    expect(wordCount('  padded   spacing  ')).toBe(2)
    expect(wordCount('')).toBe(0)
  })

  it('rejects 24 words', () => {
    const check = checkMessage(messageOf(24))
    expect(check.ok).toBe(false)
    expect(check.reason).toContain('24 words')
  })

  it('accepts 25 words - the hard minimum', () => {
    expect(checkMessage(messageOf(25)).ok).toBe(true)
  })

  it('accepts 30 words without demanding padding to 40', () => {
    // The target range starts at 40, but a concise 30-word message is
    // explicitly preferable to a padded 60-word one.
    expect(checkMessage(messageOf(30)).ok).toBe(true)
  })

  it('accepts 80 words - the hard maximum', () => {
    expect(checkMessage(messageOf(80)).ok).toBe(true)
  })

  it('rejects 81 words', () => {
    const check = checkMessage(messageOf(81))
    expect(check.ok).toBe(false)
    expect(check.reason).toContain('81 words')
  })

  it('uses the documented limits', () => {
    expect(WORDS_HARD_MIN).toBe(25)
    expect(WORDS_HARD_MAX).toBe(80)
  })
})

// ============================================================
// BANNED PHRASES
// ============================================================

describe('banned phrases', () => {
  it('rejects every phrase on the list', () => {
    for (const phrase of BANNED_PHRASES) {
      const message = `Hey! I came across Qatfa and noticed something about the '
        + 'product cards. ${phrase}. Would you be open to a look? Thanks for '
        + 'reading this message today my friend.`
      const check = checkMessage(message)
      expect(check.ok, `"${phrase}" was allowed through`).toBe(false)
      expect(check.reason).toContain(phrase)
    }
  })

  it('rejects an offer of a free audit', () => {
    expect(checkMessage(
      'Hey! I noticed the product cards on Qatfa hide prices until you click. '
      + 'I would be happy to run a free audit of the storefront for you and '
      + 'send it over. Would that be useful to you at all?',
    ).ok).toBe(false)
  })

  it('rejects a redesign pitch', () => {
    expect(checkMessage(
      'Hey! I noticed the product cards on Qatfa hide prices until you click. '
      + 'I think a redesign of those pages would really help the store a lot. '
      + 'Would you be open to hearing more about it?',
    ).ok).toBe(false)
  })

  it('rejects asking for a call', () => {
    expect(checkMessage(
      'Hey! I noticed the product cards on Qatfa hide prices until you click. '
      + 'Could we book a call this week to talk it through properly and see '
      + 'what might work? Let me know what suits you.',
    ).ok).toBe(false)
  })

  it('rejects generic conversion claims', () => {
    expect(checkMessage(
      'Hey! I came across Qatfa and noticed the hidden prices on product '
      + 'cards. Fixing it would boost your sales considerably over time I '
      + 'think. Would you be open to me showing you what I mean?',
    ).ok).toBe(false)
  })

  it('is case-insensitive', () => {
    expect(checkMessage(
      'Hey! I came across Qatfa and noticed the hidden prices on your cards. '
      + 'Happy to do a FREE AUDIT of the whole storefront for you this week. '
      + 'Would that be interesting to you at all?',
    ).ok).toBe(false)
  })

  it('accepts a clean message', () => {
    expect(checkMessage(goodResponse().message).ok).toBe(true)
  })
})

describe('reply mechanics', () => {
  it('requires a question, because the whole point is a reply', () => {
    const check = checkMessage(messageOf(40).replace('interested?', 'interested.'))
    expect(check.ok).toBe(false)
    expect(check.reason).toContain('no question')
  })
})

// ============================================================
// EVIDENCE REFERENCE RESOLUTION
// ============================================================

describe('resolveRef', () => {
  const sample = evidence()

  it('resolves a nested array path', () => {
    expect(resolveRef(sample, 'pages[0].products[1].name')).toBe('Olive Bowl')
  })

  it('resolves a whole array field', () => {
    expect(resolveRef(sample, 'pages[0].nav_labels')).toEqual(
      ['Shop', 'Collections', 'About', 'Contact'],
    )
  })

  it('resolves a detection field, including unknown', () => {
    // The booleans became tri-state. 'unknown' is a real resolved VALUE -
    // distinguishable from NOT_FOUND - and checkGrounding then refuses to
    // build a finding on it.
    expect(resolveRef(sample, 'pages[0].size_guide_detection')).toBe('unknown')
    expect(resolveRef(sample, 'pages[0].price_detection')).toBe('present')
    expect(resolveRef(sample, 'pages[0].size_guide_detection'))
      .not.toBe(NOT_FOUND)
  })

  it('treats a bare path as page 0', () => {
    expect(resolveRef(sample, 'products[0].name')).toBe('Cedar Mug')
  })

  it('rejects an out-of-range index', () => {
    expect(resolveRef(sample, 'pages[0].products[99].name')).toBe(NOT_FOUND)
    expect(resolveRef(sample, 'pages[7].title')).toBe(NOT_FOUND)
  })

  it('rejects an unknown field', () => {
    expect(resolveRef(sample, 'pages[0].checkout_steps')).toBe(NOT_FOUND)
    expect(resolveRef(sample, 'pages[0].products[0].discount')).toBe(NOT_FOUND)
  })

  it('rejects an empty or malformed ref', () => {
    expect(resolveRef(sample, '')).toBe(NOT_FOUND)
    expect(resolveRef(sample, '   ')).toBe(NOT_FOUND)
  })

  it('resolves a null value, because absence IS the evidence', () => {
    // Regression from the first live run: the model cited
    // products[0].price to support "the cards show no prices", and the
    // resolver rejected it for resolving to null - throwing away a correct,
    // well-grounded finding. A path that EXISTS resolves, whatever it holds.
    expect(resolveRef(sample, 'pages[0].products[1].price')).toBeNull()
    expect(resolveRef(sample, 'pages[0].products[1].price')).not.toBe(NOT_FOUND)
  })

  it('resolves an empty array and an explicit null field', () => {
    const thin = evidence({ pages: [page({ collections: [], title: null })] })
    expect(resolveRef(thin, 'pages[0].collections')).toEqual([])
    expect(resolveRef(thin, 'pages[0].title')).toBeNull()
    expect(resolveRef(thin, 'pages[0].collections')).not.toBe(NOT_FOUND)
  })

  it('distinguishes a missing path from a present null one', () => {
    expect(resolveRef(sample, 'pages[0].products[1].price')).not.toBe(NOT_FOUND)
    expect(resolveRef(sample, 'pages[0].products[1].nonexistent')).toBe(NOT_FOUND)
  })

  it('does not reach prototype properties', () => {
    expect(resolveRef(sample, 'pages[0].constructor')).toBe(NOT_FOUND)
    expect(resolveRef(sample, 'pages.__proto__')).toBe(NOT_FOUND)
  })
})

// ============================================================
// GROUNDING
// ============================================================

describe('checkGrounding', () => {
  const sample = evidence()

  it('accepts a claim backed by resolvable refs', () => {
    expect(checkGrounding(
      sample,
      ['pages[0].products[1].name', 'pages[0].products[2].name'],
      'Olive Bowl and Beirut Vase have no price on the card',
    ).grounded).toBe(true)
  })

  it('accepts reasonable paraphrasing rather than demanding a substring', () => {
    // Different wording and order, same referenced content.
    expect(checkGrounding(
      sample,
      ['pages[0].nav_labels'],
      'the navigation lists Collections, About and Contact',
    ).grounded).toBe(true)
  })

  it('rejects empty refs', () => {
    const result = checkGrounding(sample, [], 'prices are missing')
    expect(result.grounded).toBe(false)
    expect(result.reason).toContain('no evidence_refs')
  })

  it('rejects a missing refs field', () => {
    expect(checkGrounding(sample, undefined, 'prices missing').grounded).toBe(false)
    expect(checkGrounding(sample, 'not-an-array', 'prices missing').grounded).toBe(false)
  })

  it('rejects fabricated refs', () => {
    const result = checkGrounding(
      sample,
      ['pages[0].checkout.steps'],
      'the checkout has four steps',
    )
    expect(result.grounded).toBe(false)
    expect(result.reason).toContain('did not resolve')
  })

  it('rejects an invented observation that cites real refs', () => {
    // The refs resolve, but the claim is about something else entirely -
    // this is the hallucination the token check exists to catch.
    const result = checkGrounding(
      sample,
      ['pages[0].products[0].name'],
      'the checkout asks for a phone number before showing shipping costs',
    )
    expect(result.grounded).toBe(false)
    expect(result.reason).toContain('overlaps')
  })

  it('rejects a claim with no content words', () => {
    expect(checkGrounding(sample, ['pages[0].nav_labels'], 'the and of').grounded)
      .toBe(false)
  })

  it('REFUSES the live Guava citation, because a null price is not an absence', () => {
    // This assertion is deliberately the inverse of what it used to be.
    //
    // It previously required that a null price field could ground a "no
    // prices" claim. That is precisely what let the analyzer tell Guava
    // Lebanon their cards showed no prices when all 75 of them did: our
    // parser had dropped the prices, and null meant "not extracted", not
    // "not shown". On this fixture price_detection is 'present', so the
    // claim must now be refused.
    const result = checkGrounding(
      sample,
      ['pages[0].products[1].price', 'pages[0].price_detection'],
      'All product entries have no price, so prices are not shown on cards.',
    )
    expect(result.grounded).toBe(false)
    expect(result.reason).toBeTruthy()
  })

  it('refuses a claim resting on an unknown detection', () => {
    // Also an inversion. size_guide_detection is 'unknown' because a
    // substring miss cannot establish that a size guide is missing - the
    // markup may simply be shaped in a way we do not recognise. So "there
    // is no size guide" is no longer groundable, by design.
    const result = checkGrounding(
      sample,
      ['pages[0].size_guide_detection'],
      'there is no size guide',
    )
    expect(result.grounded).toBe(false)
    expect(result.reason).toContain('unknown')
  })

  it('supports a claim about a feature positively established as absent', () => {
    // The capability is preserved where absence is real: a page with
    // product cards and no price markup anywhere yields 'absent', and a
    // claim citing it grounds.
    const absent: Evidence = {
      ...sample,
      pages: [{ ...sample.pages[0]!, price_detection: 'absent',
        products: [{ name: 'Cedar Mug', price: null }] }],
    }
    expect(checkGrounding(
      absent,
      ['pages[0].price_detection'],
      'none of the product cards show a price',
    ).grounded).toBe(true)
  })
})

describe('contentTokens', () => {
  it('drops stopwords, punctuation and single characters', () => {
    expect(contentTokens('The prices are not on the card!')).toEqual(
      ['prices', 'card'],
    )
  })
})

// ============================================================
// FULL VALIDATION
// ============================================================

describe('validateAnalysis', () => {
  const sample = evidence()

  it('accepts a well-formed grounded success', () => {
    const result = validateAnalysis(goodResponse(), sample)
    expect(result.status).toBe('success')
    expect(result.message).toBeTruthy()
    expect(result.opportunity_category).toBe('price visibility')
    expect(result.evidence_refs).toHaveLength(2)
    expect(result.rejected_reason).toBeNull()
  })

  it('passes through no_strong_opportunity with no message', () => {
    const result = validateAnalysis(
      { status: 'no_strong_opportunity', website_opportunity: null,
        outreach_angle: null, message: null },
      sample,
    )
    expect(result.status).toBe('no_strong_opportunity')
    expect(result.message).toBeNull()
  })

  it('never lets a message survive a non-success status', () => {
    const result = validateAnalysis(
      { status: 'no_strong_opportunity', website_opportunity: null,
        outreach_angle: 'angle', message: 'A message that should be dropped.' },
      sample,
    )
    expect(result.status).toBe('no_strong_opportunity')
    expect(result.message).toBeNull()
  })

  it.each([
    ['not an object', 'a string'],
    ['null', null],
    ['an array', []],
    ['an unknown status', { status: 'great_news' }],
    ['success with no opportunity', { status: 'success', website_opportunity: null }],
  ])('fails on %s', (_label, raw) => {
    expect(validateAnalysis(raw, sample).status).toBe('failed')
  })

  it('fails when a success carries no message', () => {
    const body = { ...goodResponse(), message: null }
    expect(validateAnalysis(body, sample).status).toBe('failed')
  })

  it('fails when the opportunity is incomplete', () => {
    const body = goodResponse()
    const incomplete = {
      ...body,
      website_opportunity: { ...body.website_opportunity, observation: '' },
    }
    expect(validateAnalysis(incomplete, sample).status).toBe('failed')
  })

  it('downgrades an ungrounded success to insufficient_evidence', () => {
    const body = goodResponse()
    const ungrounded = {
      ...body,
      website_opportunity: {
        ...body.website_opportunity,
        evidence_refs: ['pages[0].invented_field'],
      },
    }
    const result = validateAnalysis(ungrounded, sample)
    expect(result.status).toBe('insufficient_evidence')
    expect(result.message).toBeNull()
    expect(result.rejected_reason).toContain('did not resolve')
  })

  it('downgrades a success whose message breaks the copy rules', () => {
    const result = validateAnalysis(
      goodResponse(
        'Hey! I noticed the product cards on Qatfa hide prices. Happy to send '
        + 'over a free audit of the store whenever suits you best this week. '
        + 'Would that be helpful at all?',
      ),
      sample,
    )
    expect(result.status).toBe('insufficient_evidence')
    expect(result.message).toBeNull()
    expect(result.rejected_reason).toContain('free audit')
  })

  it('clamps confidence into 0..1', () => {
    const body = goodResponse()
    for (const [given, expected] of [[1.7, 1], [-2, 0], [0.42, 0.42]] as const) {
      const result = validateAnalysis(
        { ...body, website_opportunity: { ...body.website_opportunity, confidence: given } },
        sample,
      )
      expect(result.opportunity_confidence).toBe(expected)
    }
  })

  it('nulls a non-numeric confidence rather than failing', () => {
    const body = goodResponse()
    const result = validateAnalysis(
      { ...body, website_opportunity: { ...body.website_opportunity, confidence: 'high' } },
      sample,
    )
    expect(result.status).toBe('success')
    expect(result.opportunity_confidence).toBeNull()
  })
})

// ============================================================
// PROMPT CONSTRUCTION
// ============================================================

describe('buildMessages', () => {
  it('puts evidence in a user message, never the system prompt', () => {
    const messages = buildMessages(evidence(), 'Qatfa')
    expect(messages).toHaveLength(2)
    expect(messages[0]!.role).toBe('system')
    expect(messages[1]!.role).toBe('user')
    expect(messages[0]!.content).not.toContain('Cedar Mug')
    expect(messages[1]!.content).toContain('Cedar Mug')
  })

  it('fences the evidence and labels it as data', () => {
    const user = buildMessages(evidence(), 'Qatfa')[1]!.content
    expect(user).toContain('<untrusted_website_content>')
    expect(user).toContain('</untrusted_website_content>')
    expect(user).toMatch(/DATA .*not instructions/i)
  })

  it('tells the model that embedded instructions are content', () => {
    const system = buildMessages(evidence(), 'Qatfa')[0]!.content
    expect(system).toMatch(/ignore previous instructions/i)
    expect(system).toMatch(/never as a directive/i)
  })

  it('states the copy rules the validators enforce', () => {
    const system = buildMessages(evidence(), 'Qatfa')[0]!.content
    expect(system).toContain('25')
    expect(system).toContain('80')
    expect(system).toMatch(/free audit/i)
    expect(system).toMatch(/no_strong_opportunity/)
  })
})

// ============================================================
// EVIDENCE SUFFICIENCY GATE
// ============================================================

describe('hasEnoughEvidence', () => {
  it('accepts a page with products', () => {
    expect(hasEnoughEvidence(evidence())).toBe(true)
  })

  it('accepts a page with only navigation', () => {
    expect(hasEnoughEvidence(evidence({
      pages: [page({ products: [], collections: [], nav_labels: ['Shop'] })],
    }))).toBe(true)
  })

  it('rejects a page with nothing to observe, so no model call is spent', () => {
    expect(hasEnoughEvidence(evidence({
      pages: [page({ products: [], collections: [], nav_labels: [] })],
    }))).toBe(false)
  })
})
