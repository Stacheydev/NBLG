/**
 * END-TO-END test of POST /api/analyze against the real production path.
 *
 * SKIPPED unless NBLG_TEST_JWT holds a real Supabase user access token.
 * It cannot be synthesised here: the project's RLS grants `leads` SELECT and
 * `lead_analyses` INSERT to the `authenticated` role only, the anon key is
 * correctly refused (verified: 401 / 42501), and signups are disabled. A
 * service-role key is deliberately absent from this application and must
 * stay absent - it bypasses RLS entirely.
 *
 * To run it:
 *   1. Sign in to the dashboard.
 *   2. In the browser console:
 *      (await window.supabase?.auth.getSession())?.data.session.access_token
 *      ...or read sb-<project-ref>-auth-token from localStorage and take
 *      .access_token from the JSON.
 *   3. NBLG_TEST_JWT=<token> npx vitest run tests/e2e-analyze.test.ts
 *
 * The token is read from the environment, never written to disk, and never
 * logged - only whether one is present.
 */
import { loadEnv } from 'vite'
import { expect, it } from 'vitest'

import handler from '../api/analyze.js'
import type { ApiRequest, ApiResponse } from '../api/_shared.js'

// Declared locally rather than adding @types/node, which this project
// deliberately does without - the same approach _shared.ts and _llm.ts take.
declare const process: { env: Record<string, string | undefined> }

// Load the gitignored .env.local the way `vercel dev` would. Vite's own
// loader is used rather than node:fs, which has no types here; an empty
// prefix means "every variable", not just VITE_ ones.
for (const [key, value] of Object.entries(loadEnv('development', '.', ''))) {
  process.env[key] ??= value
}

// The server-side names readEnv() expects, mirroring the Vercel dashboard.
process.env.SUPABASE_URL ??= process.env.VITE_SUPABASE_URL
process.env.SUPABASE_ANON_KEY ??= process.env.VITE_SUPABASE_ANON_KEY
// analyze.ts never calls GitHub, but readEnv() requires the name to be set.
process.env.GITHUB_DISPATCH_TOKEN ??= 'unused-by-analyze'

/**
 * Trimmed and de-quoted.
 *
 * A token pasted into a shell arrives with surrounding quotes or a trailing
 * newline more often than not, and `Bearer "eyJ..."` is not a bearer token.
 * Supabase answers 401 with no hint as to which of the two went wrong.
 */
const JWT = (process.env.NBLG_TEST_JWT ?? '')
  .trim()
  .replace(/^["']|["']$/g, '')
  .trim() || undefined

const DOMAIN = 'guavaonlineshop.com'

/**
 * What is wrong with this token, without revealing it.
 *
 * Reports only structural facts and the non-identifying claims (role, issuer,
 * expiry). Never the token, never `sub`, never `email`. Decoding is local -
 * no signature check, no network.
 */
function describeToken(token: string): string[] {
  const notes: string[] = []
  const parts = token.split('.')
  notes.push(`length ${token.length}, ${parts.length} dot-separated segments`)

  if (parts.length !== 3) {
    notes.push('NOT A JWT: a JWT has exactly 3 segments. A Supabase refresh '
      + 'token is a short opaque string, and the stored session is a JSON '
      + 'object - neither is the access token.')
    return notes
  }
  if (!/^[A-Za-z0-9_-]+$/.test(parts[0] ?? '')) {
    notes.push('first segment is not base64url - the value may be quoted, '
      + 'truncated, or URL-encoded')
    return notes
  }

  try {
    const pad = (s: string) => s + '='.repeat((4 - (s.length % 4)) % 4)
    const decode = (s: string) =>
      JSON.parse(atob(pad(s).replace(/-/g, '+').replace(/_/g, '/')))
    const payload = decode(parts[1] ?? '') as Record<string, unknown>

    notes.push(`role claim: ${String(payload.role ?? '(none)')}`)
    notes.push(`issuer    : ${String(payload.iss ?? '(none)')}`)

    const exp = Number(payload.exp)
    if (Number.isFinite(exp)) {
      const secondsLeft = exp - Math.floor(Date.now() / 1000)
      notes.push(
        secondsLeft > 0
          ? `expires in ${Math.round(secondsLeft / 60)} min - still valid`
          : `EXPIRED ${Math.round(-secondsLeft / 60)} min ago `
            + '(Supabase access tokens last ~1 hour; grab a fresh one)',
      )
    }
    if (payload.role !== 'authenticated') {
      notes.push('role is not "authenticated" - RLS policies on leads and '
        + 'lead_analyses grant only that role')
    }
  } catch {
    notes.push('payload did not decode as base64url JSON')
  }
  return notes
}

function capture() {
  const out = { status: 200, body: undefined as unknown }
  const res: ApiResponse = {
    status(code) { out.status = code; return res },
    json(body) { out.body = body },
    setHeader() { /* not asserted here */ },
  }
  return { res, out }
}

it.skipIf(!JWT)('analyzes one real lead end to end via /api/analyze', async () => {
  const log = (...a: unknown[]) => console.log(...a)
  const url = process.env.SUPABASE_URL!
  const anon = process.env.SUPABASE_ANON_KEY!

  log('\n============ END-TO-END /api/analyze ============')
  log(`lead             : ${DOMAIN}`)
  log(`groq key         : ${process.env.GROQ_API_KEY ? 'present' : 'MISSING'} (never printed)`)
  log(`supabase url     : ${url ? 'resolved' : 'MISSING'}`)

  // --- token triage, before anything else ------------------------------
  log(`\n-- token (never printed) -----------------------`)
  for (const note of describeToken(JWT!)) log(`  ${note}`)

  // Ask Supabase directly what it makes of the token. This is the same call
  // requireUser() makes, so its verdict is the authoritative one.
  const probe = await fetch(`${url}/auth/v1/user`, {
    headers: { apikey: anon, authorization: `Bearer ${JWT}` },
  })
  const probeBody = await probe.text()
  log(`\n-- supabase /auth/v1/user ----------------------`)
  log(`  status : ${probe.status}`)
  if (probe.status !== 200) {
    log(`  body   : ${probeBody.slice(0, 220)}`)
    log('\n  The token is being sent correctly as "Bearer <token>" with the')
    log('  apikey header - verified by unit test - so this verdict is about')
    log('  the token value itself, not the request shape.')
  } else {
    const who = JSON.parse(probeBody) as { role?: string; aud?: string }
    log(`  role   : ${who.role}`)
    log(`  aud    : ${who.aud}`)
  }
  expect(probe.status, 'Supabase rejected the access token').toBe(200)

  // --- 0. clear any previous row so the write is unambiguous -------------
  await fetch(`${url}/rest/v1/lead_analyses?domain=eq.${DOMAIN}`, {
    method: 'DELETE',
    headers: { apikey: anon, authorization: `Bearer ${JWT}` },
  })

  // --- 1-8. the real handler --------------------------------------------
  const { res, out } = capture()
  const started = Date.now()
  await handler(
    {
      method: 'POST',
      url: '/api/analyze',
      headers: { authorization: `Bearer ${JWT}` },
      ...({ body: { domain: DOMAIN } } as object),
    } as ApiRequest,
    res,
  )
  const elapsed = Date.now() - started

  log(`\n-- handler -------------------------------------`)
  log(`http status      : ${out.status}`)
  log(`elapsed          : ${elapsed}ms`)
  log(`response body    : ${JSON.stringify(out.body, null, 1)}`)

  const body = out.body as Record<string, unknown>

  // --- secret containment ------------------------------------------------
  const serialised = JSON.stringify(out.body)
  for (const forbidden of [process.env.GROQ_API_KEY, anon, JWT]) {
    if (forbidden) expect(serialised).not.toContain(forbidden)
  }
  expect(serialised).not.toContain('gsk_')
  log(`\nsecret containment: no key or token in the response body`)

  expect(out.status).toBe(200)
  expect(body.status).toBe('success')

  // --- read the saved row BACK from Supabase -----------------------------
  const readBack = await fetch(
    `${url}/rest/v1/lead_analyses?domain=eq.${DOMAIN}&select=*`,
    { headers: { apikey: anon, authorization: `Bearer ${JWT}` } },
  )
  const rows = (await readBack.json()) as Record<string, unknown>[]

  log(`\n-- persistence ---------------------------------`)
  log(`read-back status : ${readBack.status}`)
  log(`rows found       : ${rows.length}`)
  log(`saved row        : ${JSON.stringify(rows[0], null, 1)}`)

  expect(rows).toHaveLength(1)
  const row = rows[0]!
  expect(row.domain).toBe(DOMAIN)
  expect(row.status).toBe('success')
  expect(row.message).toBeTruthy()
  expect(row.model).toBe('openai/gpt-oss-120b')
  expect(Array.isArray(row.evidence_refs)).toBe(true)
  expect(row.analyzed_at).toBeTruthy()

  const words = String(row.message).trim().split(/\s+/).length
  log(`\n--- SAVED MESSAGE (${words} words) ---`)
  log(row.message)
  log('--- END ---')
  log(`\nobservation      : ${row.opportunity_observation}`)
  log(`evidence         : ${row.opportunity_evidence}`)
  log(`evidence_refs    : ${JSON.stringify(row.evidence_refs)}`)
  log(`confidence       : ${row.opportunity_confidence}`)
  log(`pages_fetched    : ${row.pages_fetched}`)
  log(`analyzed_by      : ${row.analyzed_by ? 'set (user id)' : 'null'}`)
  log('================================================\n')

  expect(words).toBeGreaterThanOrEqual(25)
  expect(words).toBeLessThanOrEqual(80)
}, 60_000)

it.runIf(!JWT)('is skipped without NBLG_TEST_JWT', () => {
  // Documents why the suite is green without having run the live path.
  expect(JWT).toBeUndefined()
})
