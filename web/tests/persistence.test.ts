/**
 * Durability of an analysis, in either mode.
 *
 * A fallback used to live only in the API response, so it vanished on
 * reload. Now all three fallback columns are written on every save and read
 * back on mount. These tests defend the two invariants that make the stored
 * row trustworthy:
 *
 *   a verified row never carries a fallback
 *   a fallback row never carries a verified message
 *
 * and that a round trip through the database changes neither.
 */
import { describe, expect, it } from 'vitest'

import { storedToResponse } from '../src/lib/analysis.js'
import type { LeadAnalysis as StoredAnalysis } from '../src/types.js'

// ============================================================
// FIXTURES - rows exactly as saveAnalysis() writes them
// ============================================================

const VERIFIED_ROW: StoredAnalysis = {
  domain: 'guavaonlineshop.com',
  status: 'success',
  opportunity_category: 'price visibility',
  opportunity_observation: 'None of the product cards show a price.',
  opportunity_evidence: 'the Cold-Shoulder Ribbed Top card shows no price',
  opportunity_confidence: 0.8,
  outreach_angle: 'price visibility',
  message:
    'Hey Guava Lebanon, I noticed the tops on your homepage do not show a '
    + 'price until you open each one. Shoppers comparing pieces often give '
    + 'up before that. I am Hadi from North Bound - was that deliberate?',
  fallback_message: null,
  fallback_category: null,
  fallback_verified: false,
  model: 'openai/gpt-oss-120b',
  pages_fetched: 1,
  analyzed_at: '2026-10-07T12:00:00+00:00',
}

const FALLBACK_ROW: StoredAnalysis = {
  domain: 'guavaonlineshop.com',
  status: 'no_strong_opportunity',
  opportunity_category: null,
  opportunity_observation: null,
  opportunity_evidence: null,
  opportunity_confidence: null,
  outreach_angle: null,
  message: null,
  fallback_message:
    'Hey Guava Lebanon, I was browsing your site and was curious how you are '
    + 'thinking about the overall shopping experience, from finding a product '
    + 'through to deciding whether to buy. I am Hadi from North Bound. Is '
    + 'that something you have been looking at?',
  fallback_category: 'generic_shopping_experience',
  fallback_verified: false,
  model: 'openai/gpt-oss-120b',
  pages_fetched: 1,
  analyzed_at: '2026-10-07T12:00:00+00:00',
}

// ============================================================
// A FALLBACK SURVIVES THE ROUND TRIP
// ============================================================

describe('a stored fallback survives a read-back', () => {
  const loaded = storedToResponse(FALLBACK_ROW)

  it('comes back with its message intact', () => {
    expect(loaded.fallback_message).toBe(FALLBACK_ROW.fallback_message)
  })

  it('comes back still marked unverified', () => {
    expect(loaded.fallback_verified).toBe(false)
    expect(loaded.fallback_category).toBe('generic_shopping_experience')
  })

  it('renders through the same branch as a fresh fallback', () => {
    // The UI keys off status + fallback_message, so a reloaded fallback hits
    // the same unverified banner rather than a quieter variant.
    expect(loaded.status).toBe('no_strong_opportunity')
    expect(loaded.message).toBeNull()
    expect(loaded.opportunity).toBeNull()
  })

  it('is byte-identical to the response a fresh analysis would return', () => {
    expect(loaded).toEqual({
      status: 'no_strong_opportunity',
      opportunity: null,
      outreach_angle: null,
      message: null,
      fallback_message: FALLBACK_ROW.fallback_message,
      fallback_category: 'generic_shopping_experience',
      fallback_verified: false,
      pages_fetched: 1,
    })
  })
})

describe('a stored verified result survives a read-back', () => {
  const loaded = storedToResponse(VERIFIED_ROW)

  it('comes back with its evidence-backed message and opportunity', () => {
    expect(loaded.status).toBe('success')
    expect(loaded.message).toBe(VERIFIED_ROW.message)
    expect(loaded.opportunity).toEqual({
      category: 'price visibility',
      observation: 'None of the product cards show a price.',
      evidence: 'the Cold-Shoulder Ribbed Top card shows no price',
      confidence: 0.8,
    })
  })

  it('carries no fallback', () => {
    expect(loaded.fallback_message).toBeNull()
    expect(loaded.fallback_category).toBeNull()
    expect(loaded.fallback_verified).toBe(false)
  })
})

// ============================================================
// THE TWO MODES CANNOT BLEED INTO EACH OTHER ON READ
// ============================================================

describe('the read path refuses to mix the modes', () => {
  it('ignores a fallback stored on a success row', () => {
    // Should be impossible to write, but a reader must not trust the row:
    // presenting an unverified opener beside a verified finding is exactly
    // the confusion the separate fields exist to prevent.
    const corrupt: StoredAnalysis = {
      ...VERIFIED_ROW,
      fallback_message: 'a generic opener that should never appear here',
      fallback_category: 'generic_shopping_experience',
    }
    const loaded = storedToResponse(corrupt)
    expect(loaded.status).toBe('success')
    expect(loaded.message).toBe(VERIFIED_ROW.message)
    expect(loaded.fallback_message).toBeNull()
    expect(loaded.fallback_category).toBeNull()
  })

  it('ignores a verified message stored on a declined row', () => {
    const corrupt: StoredAnalysis = {
      ...FALLBACK_ROW,
      message: 'an evidence-backed message that should never appear here',
    }
    const loaded = storedToResponse(corrupt)
    expect(loaded.status).toBe('no_strong_opportunity')
    expect(loaded.message).toBeNull()
    expect(loaded.fallback_message).toBe(FALLBACK_ROW.fallback_message)
  })

  it('never presents opportunity columns on a declined row', () => {
    const corrupt: StoredAnalysis = {
      ...FALLBACK_ROW,
      opportunity_category: 'price visibility',
      opportunity_observation: 'The cards do not show a price.',
      opportunity_confidence: 0.9,
    }
    expect(storedToResponse(corrupt).opportunity).toBeNull()
  })

  it('forces fallback_verified to false even if the column says true', () => {
    const corrupt: StoredAnalysis = {
      ...FALLBACK_ROW, fallback_verified: true,
    }
    expect(storedToResponse(corrupt).fallback_verified).toBe(false)
  })

  it('tolerates a null fallback_verified from an older row', () => {
    const legacy: StoredAnalysis = {
      ...FALLBACK_ROW, fallback_verified: null,
    }
    expect(storedToResponse(legacy).fallback_verified).toBe(false)
    expect(storedToResponse(legacy).fallback_message)
      .toBe(FALLBACK_ROW.fallback_message)
  })

  it('handles a pre-migration row with no fallback at all', () => {
    const legacy = {
      ...FALLBACK_ROW, fallback_message: null, fallback_category: null,
      fallback_verified: null,
    } as StoredAnalysis
    const loaded = storedToResponse(legacy)
    expect(loaded.status).toBe('no_strong_opportunity')
    expect(loaded.fallback_message).toBeNull()
    expect(loaded.message).toBeNull()
  })
})
