/**
 * PRESENT is not the same as EXTRACTED. ABSENT is not the same as MISSED.
 *
 * Regression suite for the worst failure this project has produced. A live
 * analysis told Guava Lebanon:
 *
 *   "product cards on the homepage do not display any prices"
 *
 * at 97% confidence. Every card on that homepage visibly showed a price.
 * The chain was: rendered page -> extraction dropped the prices -> evidence
 * said they were absent -> the model reasoned correctly from false evidence
 * -> a criticism that was simply untrue.
 *
 * The cause was de-duplication. Shopify renders each card as TWO anchors to
 * the same product, image then title. The price sits ~330 characters after
 * the title anchor but >1,100 after the image one, outside the search
 * window. De-duplicating on product NAME kept the first anchor seen - the
 * image - so all 75 products stored price: null while all 75 had a price.
 *
 * The rule these tests defend: an extraction miss may never become a
 * website criticism. Where we cannot establish the state, it is 'unknown',
 * and 'unknown' can never support a finding.
 */
import { describe, expect, it } from 'vitest'

import { extractPage } from '../api/_evidence.js'
import type { Evidence } from '../api/_evidence.js'
import { checkGrounding, validateAnalysis } from '../api/_prompt.js'

// ============================================================
// THE GUAVA SHAPE: TWO ANCHORS PER CARD, PRICE AFTER THE SECOND
// ============================================================

/**
 * A faithful reduction of guavaonlineshop.com's homepage markup: an image
 * anchor, a large block of srcset/sizing attributes, then a title anchor,
 * then the price. Verified against the real 535KB document.
 */
function guavaCard(handle: string, name: string, price: string): string {
  const filler = `<img src="/cdn/shop/files/${handle}.jpg" srcset="${
    [180, 360, 540, 720, 900, 1080]
      .map((w) => `//guavaonlineshop.com/cdn/shop/files/${handle}_${w}x.jpg ${w}w`)
      .join(', ')
  }" sizes="(min-width: 1200px) 267px, (min-width: 750px) calc((100vw - 130px) / 4)" loading="lazy">`

  return `
    <div class="card-wrapper product-card-wrapper">
      <a href="/products/${handle}" class="card__media-link">${filler}</a>
      <div class="card__content">
        <a href="/products/${handle}" class="card__heading-link">${name}</a>
        <div class="price"><span class="price-item">${price}</span></div>
      </div>
    </div>`
}

const GUAVA_HOME = `<!doctype html><html><head>
  <title>Guava Online Shop Lebanon</title></head><body>
  <header><nav>
    <a href="/collections/dresses">Dresses</a>
    <a href="/collections/tops">Tops</a>
    <a href="/collections/pants">Pants</a>
  </nav><form action="/search"><input type="search" name="q"></form></header>
  ${guavaCard('one-shoulder-top', 'One-Shoulder Feather-Trim Top', '$19.00')}
  ${guavaCard('cold-shoulder-top', 'Cold-Shoulder Ribbed Top', '$18.00')}
  ${guavaCard('cutout-top', 'Long-Sleeve Cutout Top', '$18.00')}
  ${guavaCard('ruched-blouse', 'High-Neck Ruched Blouse', '$19.00')}
</body></html>`

describe('the Guava regression: visible prices must be extracted', () => {
  const page = extractPage(GUAVA_HOME, 'https://guavaonlineshop.com/', 'homepage')

  it('finds each product exactly once despite two anchors per card', () => {
    expect(page.products.map((p) => p.name)).toEqual([
      'One-Shoulder Feather-Trim Top',
      'Cold-Shoulder Ribbed Top',
      'Long-Sleeve Cutout Top',
      'High-Neck Ruched Blouse',
    ])
  })

  it('recovers the price for every product', () => {
    expect(page.products.map((p) => p.price)).toEqual([
      '$19.00', '$18.00', '$18.00', '$19.00',
    ])
  })

  it('reports prices as PRESENT, not absent', () => {
    expect(page.price_detection).toBe('present')
  })

  it('never reports absent for a page whose cards carry prices', () => {
    expect(page.price_detection).not.toBe('absent')
  })
})

// ============================================================
// price: null MUST NOT MEAN "NO PRICE"
// ============================================================

describe('a missed extraction is not an absence', () => {
  it('is unknown, not absent, when the page clearly has price markup', () => {
    // Cards we can parse, prices our patterns cannot tie to them.
    const html = `
      <a href="/products/a">Alpha</a><a href="/products/b">Beta</a>
      <div class="some-remote-container">
        ${'<span>filler</span>'.repeat(120)}
        <span class="price">$42.00</span>
      </div>`
    const page = extractPage(html, 'https://x.com/', 'homepage')

    expect(page.products.every((p) => p.price === null)).toBe(true)
    expect(page.price_detection).toBe('unknown')
    expect(page.price_detection).not.toBe('absent')
  })

  it('is unknown when price markup exists only as a CSS class', () => {
    const html = `<a href="/products/a">Alpha</a>
      <div class="price-box"><span class="money"></span></div>`
    expect(extractPage(html, 'https://x.com/', 'homepage').price_detection)
      .toBe('unknown')
  })

  it('is unknown when there are no product cards at all', () => {
    const page = extractPage('<h1>Coming soon</h1>', 'https://x.com/', 'homepage')
    expect(page.price_detection).toBe('unknown')
  })
})

// ============================================================
// absent IS STILL REACHABLE WHEN PRICES GENUINELY ARE NOT THERE
// ============================================================

describe('genuine absence is still detected', () => {
  const NO_PRICES = `
    <a href="/products/vase">Ceramic Vase</a>
    <a href="/products/bowl">Olive Bowl</a>
    <a href="/products/mug">Cedar Mug</a>
    <p>Contact us for availability.</p>`

  it('reports absent when cards exist and the page has no price markup', () => {
    const page = extractPage(NO_PRICES, 'https://x.com/', 'homepage')
    expect(page.products).toHaveLength(3)
    expect(page.price_detection).toBe('absent')
  })

  it('lets a price finding ground against an established absence', () => {
    const page = extractPage(NO_PRICES, 'https://x.com/', 'homepage')
    const evidence: Evidence = {
      domain: 'x.com', pages: [page], pages_fetched: 1,
    }
    expect(checkGrounding(
      evidence,
      ['pages[0].price_detection'],
      'none of the product cards show a price',
    ).grounded).toBe(true)
  })
})

// ============================================================
// unknown CAN NEVER SUPPORT A FINDING
// ============================================================

function evidenceWith(detection: 'present' | 'absent' | 'unknown'): Evidence {
  return {
    domain: 'x.com',
    pages: [{
      url: 'https://x.com/', kind: 'homepage', title: 'Shop',
      headings: [], nav_labels: ['Shop'], collections: ['Tops'],
      products: [{ name: 'Alpha', price: detection === 'present' ? '$9' : null }],
      ctas: [],
      price_detection: detection,
      search_detection: 'present',
      size_guide_detection: 'unknown',
      product_card_detection: 'present',
    }],
    pages_fetched: 1,
  }
}

describe('unknown is never grounds for a criticism', () => {
  it('refuses a finding that cites an unknown field', () => {
    const result = checkGrounding(
      evidenceWith('unknown'),
      ['pages[0].price_detection'],
      'the product cards do not show a price',
    )
    expect(result.grounded).toBe(false)
    expect(result.reason).toContain('unknown')
  })

  it('refuses a null price when detection is not absent', () => {
    const result = checkGrounding(
      evidenceWith('unknown'),
      ['pages[0].products[0].price'],
      'the product cards do not show a price',
    )
    expect(result.grounded).toBe(false)
    expect(result.reason).toMatch(/not .absent.|unknown/)
  })

  it('refuses a null price even when the page looks priceless', () => {
    // price_detection 'present' with an unextracted price on one card: the
    // shop plainly shows prices, so this card is a parser gap.
    const result = checkGrounding(
      { ...evidenceWith('present'),
        pages: [{ ...evidenceWith('present').pages[0]!,
          products: [{ name: 'Alpha', price: null }] }] },
      ['pages[0].products[0].price'],
      'the product cards do not show a price',
    )
    expect(result.grounded).toBe(false)
  })

  it('turns the whole Guava analysis into no verified opportunity', () => {
    // The exact model output from the live failure, against evidence whose
    // price state is unknown rather than falsely absent.
    const result = validateAnalysis({
      status: 'success',
      website_opportunity: {
        category: 'price visibility',
        problem_type: 'missing',
        observation: 'Product cards on the homepage do not display any prices.',
        shopper_impact: 'Shoppers cannot compare without clicking.',
        evidence:
          'the product cards on the homepage show no price values for each '
          + 'listed product',
        evidence_refs: ['pages[0].price_detection'],
        confidence: 0.97,
      },
      outreach_angle: 'price visibility',
      message:
        'Hey, I was looking at Guava Lebanon and noticed the product cards '
        + 'do not show a price until you open each one. Shoppers comparing '
        + 'tops often give up before that. I am Hadi from North Bound - was '
        + 'that deliberate?',
      fallback_message: null,
    }, evidenceWith('unknown'))

    expect(result.status).not.toBe('success')
    expect(result.message).toBeNull()
    expect(result.opportunity_observation).toBeNull()
  })
})
