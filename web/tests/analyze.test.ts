/**
 * The /api/analyze endpoint: authorization, the trust boundary, and what
 * reaches the browser.
 *
 * `fetch` is stubbed, so no real request is made to Supabase or to Groq and
 * no API key is needed. GROQ_API_KEY is set to a dummy value in the tests
 * that reach the model, purely to get past the not-configured branch - the
 * point of those tests is that the value never appears in a response.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import handler, { ANALYZE_BUDGET_MS } from '../api/analyze.js'
import type { ApiRequest, ApiResponse } from '../api/_shared.js'

// Declared locally rather than pulling in @types/node, matching the pattern
// _shared.ts and _llm.ts already use. The api/ tsconfig provides only
// ES2022 + DOM on purpose.
declare const process: { env: Record<string, string | undefined> }

// ============================================================
// HARNESS
// ============================================================

const DUMMY_KEY = 'gsk_test_not_a_real_key_0000000000000000'

function res() {
  const captured = { status: 200, body: undefined as unknown, headers: {} as Record<string, string> }
  const api: ApiResponse = {
    status(code) { captured.status = code; return api },
    json(body) { captured.body = body },
    setHeader(name, value) { captured.headers[name] = value },
  }
  return { api, captured }
}

function req(body: unknown, token = 'user-jwt'): ApiRequest {
  return {
    method: 'POST',
    url: '/api/analyze',
    headers: token ? { authorization: `Bearer ${token}` } : {},
    ...({ body } as object),
  } as ApiRequest
}

const LEAD = {
  domain: 'qatfa.com',
  business_name: 'Qatfa',
  website_url: 'https://qatfa.com',
}

const STOREFRONT = `
  <title>Qatfa — Ceramics</title>
  <nav><a href="/collections/mugs">Mugs</a><a href="/pages/about">About</a>
       <a href="/collections/bowls">Bowls</a></nav>
  <a href="/products/cedar-mug">Cedar Mug</a>
  <a href="/products/olive-bowl">Olive Bowl</a>`

const MODEL_REPLY = {
  status: 'success',
  website_opportunity: {
    category: 'price visibility',
    problem_type: 'hidden',
    observation: 'Product cards show no price until you open the product.',
    shopper_impact: 'Shoppers cannot compare without clicking each one.',
    evidence: 'Cedar Mug and Olive Bowl have no price on the card',
    evidence_refs: ['pages[0].products[0].name', 'pages[0].products[1].name'],
    confidence: 0.8,
  },
  outreach_angle: 'price visibility',
  message:
    'Hey! I came across Qatfa and noticed the product cards do not show a '
    + 'price until you click through. It caught my eye because people '
    + 'browsing ceramics tend to compare first. I am Hadi from North Bound '
    + '. Would you be open to me showing you what I mean?',
}

/**
 * Route stubbed fetches by URL. Returns the calls made, so a test can prove
 * which host was contacted.
 */
function stubFetch(options: {
  lead?: unknown
  html?: string | null
  model?: unknown
  modelStatus?: number
} = {}) {
  const calls: string[] = []

  vi.stubGlobal('fetch', vi.fn(async (input: unknown) => {
    const url = String(input)
    calls.push(url)

    // Supabase auth check performed by requireUser().
    if (url.includes('/auth/v1/user')) {
      return new Response(JSON.stringify({ id: 'user-1' }), { status: 200 })
    }
    // Lead lookup.
    if (url.includes('/rest/v1/leads')) {
      const rows = options.lead === undefined ? [LEAD] : options.lead
      return new Response(JSON.stringify(rows), { status: 200 })
    }
    // Analysis upsert.
    if (url.includes('/rest/v1/lead_analyses')) {
      return new Response('', { status: 201 })
    }
    // The model.
    if (url.includes('api.groq.com')) {
      if (options.modelStatus && options.modelStatus !== 200) {
        return new Response('upstream detail that must not leak', {
          status: options.modelStatus,
          headers: { 'retry-after': '0' },
        })
      }
      return new Response(JSON.stringify({
        choices: [{ message: { content: JSON.stringify(options.model ?? MODEL_REPLY) } }],
      }), { status: 200 })
    }
    // The storefront.
    if (options.html === null) return new Response('', { status: 500 })
    return new Response(options.html ?? STOREFRONT, {
      status: 200,
      headers: { 'content-type': 'text/html' },
    })
  }))

  return { calls }
}

beforeEach(() => {
  process.env.GITHUB_DISPATCH_TOKEN = 'dummy'
  process.env.SUPABASE_URL = 'https://project.supabase.co'
  process.env.SUPABASE_ANON_KEY = 'anon'
  delete process.env.GROQ_API_KEY
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ============================================================
// AUTHORIZATION
// ============================================================

describe('authorization', () => {
  it('rejects a non-POST request', async () => {
    const { api, captured } = res()
    await handler({ ...req({ domain: 'qatfa.com' }), method: 'GET' }, api)
    expect(captured.status).toBe(405)
  })

  it('rejects a request with no bearer token', async () => {
    stubFetch()
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }, ''), api)
    expect(captured.status).toBe(401)
    expect(captured.body).toEqual({ error: 'Not signed in.' })
  })

  it('rejects a token Supabase does not recognise', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: unknown) =>
      String(input).includes('/auth/v1/user')
        ? new Response('', { status: 401 })
        : new Response('{}', { status: 200 })))

    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)
    expect(captured.status).toBe(401)
  })

  it('forwards the caller token to Supabase verbatim as "Bearer <token>"', async () => {
    // Isolates a harness/handler bug from a bad token: if this passes, the
    // request shape is right and a 401 can only come from Supabase judging
    // the token itself.
    const seen: { url: string; headers: Record<string, string> }[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: { headers?: Record<string, string> }) => {
      seen.push({ url: String(input), headers: init?.headers ?? {} })
      if (String(input).includes('/auth/v1/user')) {
        return new Response(JSON.stringify({ id: 'user-1' }), { status: 200 })
      }
      return new Response(JSON.stringify([]), { status: 200 })
    }))

    const { api } = res()
    await handler(req({ domain: 'qatfa.com' }, 'the-user-jwt'), api)

    const auth = seen.find((c) => c.url.includes('/auth/v1/user'))
    expect(auth, 'requireUser never called /auth/v1/user').toBeDefined()
    // Exactly the two headers Supabase requires, in the exact format.
    expect(auth!.headers.authorization).toBe('Bearer the-user-jwt')
    expect(auth!.headers.apikey).toBe('anon')

    // And the same token is reused for the RLS-scoped lead read.
    const lead = seen.find((c) => c.url.includes('/rest/v1/leads'))
    expect(lead!.headers.authorization).toBe('Bearer the-user-jwt')
  })

  it('does not mangle a token containing dots and dashes', async () => {
    // A JWT is three base64url segments joined by dots; the Bearer-stripping
    // regex must not touch the token body.
    const jwt = 'eyJhbGc.eyJzdWIi.sig-with_dashes.and.dots'
    const seen: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: { headers?: Record<string, string> }) => {
      if (String(input).includes('/auth/v1/user')) {
        seen.push(init?.headers?.authorization ?? '')
        return new Response(JSON.stringify({ id: 'u' }), { status: 200 })
      }
      return new Response(JSON.stringify([]), { status: 200 })
    }))

    const { api } = res()
    await handler(req({ domain: 'qatfa.com' }, jwt), api)
    expect(seen[0]).toBe(`Bearer ${jwt}`)
  })

  it('refuses to run when the server is not configured', async () => {
    delete process.env.SUPABASE_URL
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)
    expect(captured.status).toBe(500)
    expect(captured.body).toEqual({ error: 'Server is not configured.' })
  })
})

// ============================================================
// THE TRUST BOUNDARY
// ============================================================

describe('the trust boundary', () => {
  it('rejects an unknown lead', async () => {
    stubFetch({ lead: [] })
    const { api, captured } = res()
    await handler(req({ domain: 'not-a-lead.com' }), api)
    expect(captured.status).toBe(404)
    expect(captured.body).toEqual({ error: 'That lead was not found.' })
  })

  it.each([
    ['missing', {}],
    ['empty', { domain: '' }],
    ['a url rather than a domain', { domain: 'https://evil.com/path' }],
    ['containing a path', { domain: 'qatfa.com/../../etc' }],
    ['over-long', { domain: `${'a'.repeat(300)}.com` }],
  ])('rejects a domain that is %s', async (_label, body) => {
    stubFetch()
    const { api, captured } = res()
    await handler(req(body), api)
    expect(captured.status).toBe(400)
  })

  it('fetches the website from the DATABASE, not from the request', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    // The client asks for qatfa.com but also supplies a hostile website_url
    // and pre-made evidence. Both must be ignored.
    const { calls } = stubFetch()
    const { api } = res()
    await handler(req({
      domain: 'qatfa.com',
      website_url: 'https://attacker.example/payload',
      evidence: { pages: [{ products: [{ name: 'INJECTED' }] }] },
    }), api)

    expect(calls.some((u) => u.startsWith('https://qatfa.com'))).toBe(true)
    expect(calls.some((u) => u.includes('attacker.example'))).toBe(false)
  })

  it('ignores client-supplied evidence entirely', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    stubFetch()

    const { api, captured } = res()
    await handler(req({
      domain: 'qatfa.com',
      evidence: { pages: [{ products: [{ name: 'FABRICATED PRODUCT' }] }] },
    }), api)

    // The analysis is grounded in the real fetched page, so a reference to
    // the fabricated product could not have resolved.
    expect(JSON.stringify(captured.body)).not.toContain('FABRICATED')
  })
})

// ============================================================
// THE KEY NEVER REACHES THE CLIENT
// ============================================================

describe('secret containment', () => {
  it('never puts the API key in a successful response', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    stubFetch()
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    const serialised = JSON.stringify(captured.body)
    expect(serialised).not.toContain(DUMMY_KEY)
    expect(serialised).not.toContain('gsk_')
    expect(serialised).not.toContain('GROQ')
  })

  it('never puts the API key or upstream detail in an error response', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    stubFetch({ modelStatus: 500 })
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    const serialised = JSON.stringify(captured.body)
    expect(serialised).not.toContain(DUMMY_KEY)
    expect(serialised).not.toContain('upstream detail')
    expect(captured.body).toEqual({
      status: 'failed',
      error: 'Could not analyze this lead right now.',
    })
  })

  it('reports a missing key without revealing anything about it', async () => {
    stubFetch()   // GROQ_API_KEY deliberately unset
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)
    expect(captured.body).toEqual({
      status: 'failed',
      error: 'Analysis is not configured on the server.',
    })
  })
})

// ============================================================
// OUTCOMES
// ============================================================

describe('outcomes', () => {
  it('returns a grounded success with a message', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    stubFetch()
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    const body = captured.body as Record<string, unknown>
    expect(body.status).toBe('success')
    expect(body.message).toContain('Qatfa')
    expect((body.opportunity as Record<string, unknown>).category)
      .toBe('price visibility')
  })

  it('never exposes evidence_refs or the rejection reason', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    stubFetch()
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    const serialised = JSON.stringify(captured.body)
    expect(serialised).not.toContain('evidence_refs')
    expect(serialised).not.toContain('rejected_reason')
  })

  it('returns no_strong_opportunity with no message', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    stubFetch({
      model: {
        status: 'no_strong_opportunity', website_opportunity: null,
        outreach_angle: null, message: null,
      },
    })
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    const body = captured.body as Record<string, unknown>
    expect(body.status).toBe('no_strong_opportunity')
    expect(body.message).toBeNull()
    expect(body.opportunity).toBeNull()
  })

  it('downgrades an ungrounded success to no message', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    stubFetch({
      model: {
        ...MODEL_REPLY,
        website_opportunity: {
          ...MODEL_REPLY.website_opportunity,
          evidence_refs: ['pages[0].checkout.steps'],
        },
      },
    })
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    const body = captured.body as Record<string, unknown>
    expect(body.status).toBe('insufficient_evidence')
    expect(body.message).toBeNull()
  })

  it('fails cleanly when the model returns nonsense', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    stubFetch({ model: { unexpected: 'shape' } })
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)
    expect((captured.body as Record<string, unknown>).status).toBe('failed')
  })

  it('reports a rate limit as a retryable message', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    stubFetch({ modelStatus: 429 })
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)
    expect(captured.body).toEqual({
      status: 'failed',
      error: 'Analysis is rate-limited right now. Try again in a minute.',
    })
  })

  it('reports an unreachable website without calling the model', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    const { calls } = stubFetch({ html: null })
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    expect(captured.body).toEqual({
      status: 'failed',
      error: 'Could not reach this website.',
    })
    expect(calls.some((u) => u.includes('api.groq.com'))).toBe(false)
  })

  it('skips the model when the page has nothing to observe', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    const { calls } = stubFetch({ html: '<html><body><p>Hello</p></body></html>' })
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    expect((captured.body as Record<string, unknown>).status)
      .toBe('insufficient_evidence')
    expect(calls.some((u) => u.includes('api.groq.com'))).toBe(false)
  })
})

// ============================================================
// PERSISTENCE PAYLOAD SHAPE
// ============================================================

/** Capture the row posted to lead_analyses. */
function stubFetchCapturingUpsert(options: Parameters<typeof stubFetch>[0] = {}) {
  const upserts: Record<string, unknown>[] = []
  const base = stubFetch(options)
  const inner = globalThis.fetch as unknown as (...a: unknown[]) => Promise<Response>

  vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: { body?: string }) => {
    if (String(input).includes('/rest/v1/lead_analyses') && init?.body) {
      for (const row of JSON.parse(init.body) as Record<string, unknown>[]) {
        upserts.push(row)
      }
    }
    return inner(input, init)
  }))

  return { ...base, upserts }
}

const ALL_COLUMNS = [
  'domain', 'status', 'opportunity_category', 'opportunity_observation',
  'opportunity_evidence', 'evidence_refs', 'opportunity_confidence',
  'outreach_angle', 'message',
  'fallback_message', 'fallback_category', 'fallback_verified',
  'model', 'pages_fetched', 'analyzed_at',
]

describe('upsert payload', () => {
  it('writes EVERY column explicitly on a failure, never omitting keys', async () => {
    // Regression, hit in production as 23514
    // lead_analyses_message_requires_success: PostgREST builds its
    // ON CONFLICT DO UPDATE SET list from the keys PRESENT in the payload,
    // and JSON.stringify drops undefined ones. A failure row that omitted
    // `message` left the PREVIOUS run's message in place while setting
    // status='failed', violating the CHECK constraint.
    process.env.GROQ_API_KEY = DUMMY_KEY
    const { upserts } = stubFetchCapturingUpsert({ modelStatus: 500 })
    const { api } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    expect(upserts).toHaveLength(1)
    const row = upserts[0]!
    for (const column of ALL_COLUMNS) {
      expect(Object.prototype.hasOwnProperty.call(row, column),
        `"${column}" was omitted from the upsert payload`).toBe(true)
    }
    expect(row.status).toBe('failed')
    // The field that caused the constraint violation must be an explicit null.
    expect(row.message).toBeNull()
    expect(row.opportunity_observation).toBeNull()
    expect(row.evidence_refs).toBeNull()
  })

  it('writes every column explicitly when the website is unreachable', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    const { upserts } = stubFetchCapturingUpsert({ html: null })
    const { api } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    expect(upserts).toHaveLength(1)
    for (const column of ALL_COLUMNS) {
      expect(Object.prototype.hasOwnProperty.call(upserts[0]!, column),
        `"${column}" was omitted`).toBe(true)
    }
    expect(upserts[0]!.message).toBeNull()
  })

  it('never writes a message unless the status is success', async () => {
    // The application-side half of the database CHECK constraint.
    process.env.GROQ_API_KEY = DUMMY_KEY
    const cases: Parameters<typeof stubFetch>[0][] = [
      { modelStatus: 500 },
      { modelStatus: 429 },
      { html: null },
      { model: { unexpected: 'shape' } },
      { model: { status: 'no_strong_opportunity', website_opportunity: null,
                 outreach_angle: null, message: 'should never be stored' } },
      { model: { ...MODEL_REPLY, website_opportunity: {
          ...MODEL_REPLY.website_opportunity,
          evidence_refs: ['pages[0].invented'] } } },
    ]

    for (const scenario of cases) {
      const { upserts } = stubFetchCapturingUpsert(scenario)
      const { api } = res()
      await handler(req({ domain: 'qatfa.com' }), api)
      for (const row of upserts) {
        if (row.status !== 'success') {
          expect(row.message, `status=${row.status} carried a message`).toBeNull()
        }
      }
      vi.unstubAllGlobals()
    }
  })

  it('persists all three fallback columns on a declined analysis', async () => {
    // A fallback used to exist only in the API response and vanished on
    // reload. It must now reach the row, explicitly, every time.
    process.env.GROQ_API_KEY = DUMMY_KEY
    const fallback =
      'Hey Qatfa, I was browsing your site and was curious how you are '
      + 'thinking about the overall shopping experience, from finding a '
      + 'product through to deciding whether to buy. I am Hadi from North '
      + 'Bound. Is that something you have been looking at?'

    const { upserts } = stubFetchCapturingUpsert({
      model: {
        status: 'no_strong_opportunity', website_opportunity: null,
        outreach_angle: null, message: null, fallback_message: fallback,
      },
    })
    const { api, captured } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    expect(upserts).toHaveLength(1)
    const row = upserts[0]!
    expect(row.status).toBe('no_strong_opportunity')
    expect(row.fallback_message).toBe(fallback)
    expect(row.fallback_category).toBe('generic_shopping_experience')
    expect(row.fallback_verified).toBe(false)
    // The verified field stays null - the two modes never share a column.
    expect(row.message).toBeNull()

    // And the same values reach the browser.
    const body = captured.body as Record<string, unknown>
    expect(body.fallback_message).toBe(fallback)
    expect(body.fallback_verified).toBe(false)
  })

  it('writes explicit nulls for the fallback columns on a verified success', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    const { upserts } = stubFetchCapturingUpsert()
    const { api } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    const row = upserts[0]!
    expect(row.status).toBe('success')
    expect(row.message).toBeTruthy()
    expect(row.fallback_message).toBeNull()
    expect(row.fallback_category).toBeNull()
    expect(row.fallback_verified).toBe(false)
  })

  it('never stores fallback_verified as true, in any scenario', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    for (const scenario of [
      {},
      { modelStatus: 500 },
      { html: null },
      { model: { status: 'no_strong_opportunity', website_opportunity: null,
                 outreach_angle: null, message: null,
                 fallback_message: 'Hey Qatfa, I was browsing your site and '
                   + 'was curious how you are thinking about the shopping '
                   + 'experience overall. I am Hadi from North Bound. Is '
                   + 'that something you have looked at?' } },
    ] as Parameters<typeof stubFetch>[0][]) {
      const { upserts } = stubFetchCapturingUpsert(scenario)
      const { api } = res()
      await handler(req({ domain: 'qatfa.com' }), api)
      for (const row of upserts) {
        expect(row.fallback_verified).toBe(false)
        if (row.status === 'success') expect(row.fallback_message).toBeNull()
        else expect(row.message).toBeNull()
      }
      vi.unstubAllGlobals()
    }
  })

  it('writes a message only alongside status success', async () => {
    process.env.GROQ_API_KEY = DUMMY_KEY
    const { upserts } = stubFetchCapturingUpsert()
    const { api } = res()
    await handler(req({ domain: 'qatfa.com' }), api)

    expect(upserts[0]!.status).toBe('success')
    expect(upserts[0]!.message).toBeTruthy()
    expect(upserts[0]!.model).toBe('openai/gpt-oss-120b')
  })
})

// ============================================================
// BUDGET
// ============================================================

describe('budget', () => {
  it('exposes one obvious constant to raise after checking Vercel', () => {
    expect(ANALYZE_BUDGET_MS).toBe(8_000)
  })
})
