/**
 * The LLM provider, behind one function.
 *
 * This is the ONLY file that knows the provider is Groq. Nothing else
 * mentions the endpoint, the model id, the wire format, or the API key -
 * swapping providers means rewriting `complete()` and nothing else.
 *
 * The key is read from the server environment on each call and never
 * leaves this module: it is not returned, not attached to a thrown error,
 * and not logged. Upstream error bodies are logged here (they are useful
 * and contain no credential) and are never handed back to the caller, which
 * receives a fixed reason code instead.
 */

// Declared locally so this file needs no @types/node dependency, matching
// the pattern already used in _shared.ts.
declare const process: { env: Record<string, string | undefined> }

// ============================================================
// PROVIDER CONFIGURATION
// ============================================================

const ENDPOINT = 'https://api.groq.com/openai/v1/chat/completions'

/**
 * openai/gpt-oss-120b.
 *
 * Chosen because it is one of Groq's PRODUCTION models (their preview
 * models are documented as evaluation-only) and because it is on the list
 * that supports strict structured outputs - constrained decoding against our
 * JSON schema, which is the analyzer's main defence against both malformed
 * output and prompt injection. The 20B shares the same free-tier limits but
 * follows the long list of copy constraints in _prompt.ts less reliably.
 */
export const MODEL = 'openai/gpt-oss-120b'

/**
 * Free-tier limits at time of writing: 30 req/min, 1,000 req/day,
 * 8,000 tokens/min, 200,000 tokens/day.
 *
 * TOKENS PER MINUTE IS THE BINDING CONSTRAINT, not request count. One
 * analysis is roughly 3,500 tokens, so about two per minute - which is why
 * _evidence.ts caps its fields rather than filling the 131k context window.
 */
/**
 * Completion budget, covering the model's internal reasoning AND the JSON.
 *
 * 700 was too low and failed in production: gpt-oss is a reasoning model, so
 * reasoning tokens are drawn from this same budget, and Groq returned
 * HTTP 400 "max completion tokens reached before generating a valid
 * document" having emitted only the opening fields of the object.
 *
 * Still bounded, and deliberately: the free tier allows 8,000 tokens per
 * minute, and the prompt is ~1,400, so this leaves headroom for one more
 * analysis inside the same minute rather than consuming the whole quota.
 */
const MAX_OUTPUT_TOKENS = 3_500
const TEMPERATURE = 0.4

// ============================================================
// RESULT
// ============================================================

export type LlmFailure =
  | 'not_configured'   // no key in the server environment
  | 'rate_limited'     // 429 after one retry
  | 'unavailable'      // 5xx, network failure, or a bad response shape
  | 'timed_out'        // the deadline passed
  | 'invalid_json'     // 200 whose content would not parse

export type LlmResult =
  | { ok: true; data: unknown; model: string }
  | { ok: false; failure: LlmFailure }

export interface JsonSchemaSpec {
  name: string
  schema: unknown
}

// ============================================================
// THE CALL
// ============================================================

/** Retry once on 429/5xx only. Never on a 4xx we caused. */
const MAX_ATTEMPTS = 2

/** Upper bound on honouring a retry-after, so one retry cannot eat the budget. */
const MAX_RETRY_WAIT_MS = 2_000

function retryWaitMs(response: Response): number {
  const header = response.headers.get('retry-after')
  const seconds = header ? Number(header) : NaN
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(seconds * 1_000, MAX_RETRY_WAIT_MS)
  }
  return 500
}

/**
 * One structured-output completion.
 *
 * `deadlineMs` is an absolute epoch milliseconds value, not a duration: the
 * caller owns the overall request budget and this call takes whatever is
 * left of it.
 */
export async function complete(
  messages: { role: string; content: string }[],
  schema: JsonSchemaSpec,
  deadlineMs: number,
): Promise<LlmResult> {
  const apiKey = process.env.GROQ_API_KEY
  if (!apiKey) {
    // Name only. Never the value, and never a prefix of it.
    console.error('GROQ_API_KEY is not set in the server environment.')
    return { ok: false, failure: 'not_configured' }
  }

  const body = JSON.stringify({
    model: MODEL,
    messages,
    max_tokens: MAX_OUTPUT_TOKENS,
    temperature: TEMPERATURE,
    // Strict mode: the output is constrained to the schema by construction,
    // so the model cannot emit arbitrary text even if the website content it
    // was given tried to redirect it.
    response_format: {
      type: 'json_schema',
      json_schema: { name: schema.name, strict: true, schema: schema.schema },
    },
  })

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    const remaining = deadlineMs - Date.now()
    if (remaining <= 300) return { ok: false, failure: 'timed_out' }

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), remaining)

    let response: Response
    try {
      response = await fetch(ENDPOINT, {
        method: 'POST',
        signal: controller.signal,
        headers: {
          authorization: `Bearer ${apiKey}`,
          'content-type': 'application/json',
        },
        body,
      })
    } catch (cause) {
      clearTimeout(timer)
      // Only the message, never the whole error: a rejected fetch can carry
      // the request - and therefore the Authorization header - on some
      // runtimes.
      const detail = cause instanceof Error ? cause.message : 'unknown'
      if (Date.now() >= deadlineMs || /abort/i.test(detail)) {
        return { ok: false, failure: 'timed_out' }
      }
      console.error('LLM request failed before a response:', detail)
      if (attempt === MAX_ATTEMPTS) return { ok: false, failure: 'unavailable' }
      continue
    }
    clearTimeout(timer)

    // Retryable: rate limit and upstream faults.
    if (response.status === 429 || response.status >= 500) {
      const retryable = response.status === 429 ? 'rate_limited' : 'unavailable'
      console.error(
        'LLM request was rejected:',
        response.status,
        await response.text().catch(() => '(no body)'),
      )
      if (attempt === MAX_ATTEMPTS) return { ok: false, failure: retryable }

      const wait = retryWaitMs(response)
      if (deadlineMs - Date.now() <= wait + 500) {
        return { ok: false, failure: retryable }
      }
      await new Promise((resolve) => setTimeout(resolve, wait))
      continue
    }

    // Anything else 4xx is our bug - a bad schema or a malformed body. A
    // retry would fail identically, so do not spend the budget on one.
    if (!response.ok) {
      console.error(
        'LLM request was invalid:',
        response.status,
        await response.text().catch(() => '(no body)'),
      )
      return { ok: false, failure: 'unavailable' }
    }

    const payload = (await response.json().catch(() => null)) as {
      choices?: { message?: { content?: unknown } }[]
    } | null

    const content = payload?.choices?.[0]?.message?.content
    if (typeof content !== 'string' || !content.trim()) {
      console.error('LLM response carried no message content.')
      return { ok: false, failure: 'unavailable' }
    }

    try {
      return { ok: true, data: JSON.parse(content), model: MODEL }
    } catch {
      // Strict mode should make this unreachable; treated as a provider
      // fault rather than retried, because the same prompt would repeat it.
      console.error('LLM response content was not valid JSON.')
      return { ok: false, failure: 'invalid_json' }
    }
  }

  return { ok: false, failure: 'unavailable' }
}
