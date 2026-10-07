/**
 * The completion-token budget.
 *
 * Regression: 700 was too low. gpt-oss is a reasoning model, so its internal
 * reasoning is drawn from the same budget, and Groq returned HTTP 400
 * "max completion tokens reached before generating a valid document" having
 * emitted only the opening fields of the JSON object.
 */
import { describe, expect, it, vi } from 'vitest'

import { MODEL, complete } from '../api/_llm.js'

declare const process: { env: Record<string, string | undefined> }

describe('completion budget', () => {
  it('requests enough tokens for reasoning plus the JSON, and stays bounded', async () => {
    process.env.GROQ_API_KEY = 'gsk_test_not_a_real_key_0000000000000000'
    let sent: Record<string, unknown> = {}

    vi.stubGlobal('fetch', vi.fn(async (_u: unknown, init?: { body?: string }) => {
      sent = JSON.parse(init?.body ?? '{}')
      return new Response(JSON.stringify({
        choices: [{ message: { content: '{"ok":true}' } }],
      }), { status: 200 })
    }))

    await complete([{ role: 'user', content: 'x' }],
      { name: 'n', schema: { type: 'object' } }, Date.now() + 10_000)

    const budget = Number(sent.max_tokens)
    // Comfortably above the 700 that truncated in production...
    expect(budget).toBeGreaterThanOrEqual(2_000)
    // ...and still a bound, leaving room inside the 8,000 tokens/min free tier
    // for the ~1,400-token prompt and another analysis in the same minute.
    expect(budget).toBeLessThanOrEqual(4_000)
    expect(sent.model).toBe(MODEL)
    // The strict schema is unchanged.
    expect((sent.response_format as Record<string, unknown>).type)
      .toBe('json_schema')
    expect(((sent.response_format as Record<string, Record<string, unknown>>)
      .json_schema).strict).toBe(true)

    vi.unstubAllGlobals()
  })
})
