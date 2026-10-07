/**
 * POST /api/analyze - analyse one lead's website.
 *
 *   { "domain": "qatfa.com" }  ->  { status, opportunity?, message?, ... }
 *
 * Human-triggered only: nothing in the generator calls this, and one click
 * is one analysis. That is what keeps the free provider tier viable.
 *
 * THE TRUST BOUNDARY
 *
 * The browser supplies a domain and nothing else. The website URL is read
 * from `public.leads` server-side, so a client cannot point the fetcher at
 * an arbitrary host, and it cannot supply evidence - the evidence is
 * collected here, from the lead's own site. Website content is treated as
 * untrusted data throughout (see _evidence.ts and _prompt.ts).
 *
 * Signature is Vercel's Node (req, res) pair - see the note in generate.ts.
 */
import { collectEvidence } from './_evidence.js'
import { complete } from './_llm.js'
import {
  type Analysis,
  RESPONSE_SCHEMA,
  buildMessages,
  hasEnoughEvidence,
  validateAnalysis,
} from './_prompt.js'
import {
  type ApiRequest,
  type ApiResponse,
  type ServerEnv,
  readEnv,
  requireUser,
  sendJson,
} from './_shared.js'

// ============================================================
// BUDGET
// ============================================================

/**
 * Total wall-clock budget for one analysis, website fetch and model call
 * together.
 *
 * Deliberately conservative: this project has no vercel.json and no
 * maxDuration export, so its functions run at whatever the platform default
 * is for the account's plan - a number not discoverable from the repository.
 * 8s fits inside the smallest realistic default with headroom.
 *
 * RAISE THIS FIRST if analyses start timing out on slow storefronts, after
 * confirming the real ceiling in Vercel under
 * Project Settings -> Functions -> Max Duration. It is the single knob.
 */
export const ANALYZE_BUDGET_MS = 8_000

/** Below this much remaining there is no point starting the model call. */
const MIN_MODEL_BUDGET_MS = 2_500

// ============================================================
// CONCURRENCY
// ============================================================

/**
 * Domains being analysed right now, so a double-click does not spend two
 * requests of a 30/min quota on the same lead.
 *
 * Per-instance, not global - serverless gives no shared memory, so this
 * stops the common case (one impatient user) rather than enforcing a
 * cluster-wide limit. The real quota ceiling is the provider's own 429,
 * which _llm.ts handles.
 */
const inFlight = new Set<string>()

// ============================================================
// LEAD LOOKUP
// ============================================================

interface LeadRow {
  domain: string
  business_name: string
  website_url: string
}

/**
 * The lead, read through the caller's own JWT so RLS applies as that user.
 *
 * No service-role key is used anywhere in this application, and must not be:
 * it would bypass RLS entirely.
 */
async function loadLead(
  domain: string,
  token: string,
  env: ServerEnv,
): Promise<LeadRow | null> {
  const query =
    `${env.supabaseUrl}/rest/v1/leads`
    + `?select=domain,business_name,website_url`
    + `&domain=eq.${encodeURIComponent(domain)}&limit=1`

  let response: Response
  try {
    response = await fetch(query, {
      headers: {
        apikey: env.supabaseAnonKey,
        authorization: `Bearer ${token}`,
      },
    })
  } catch (cause) {
    console.error(
      'Lead lookup failed:',
      cause instanceof Error ? cause.message : 'unknown',
    )
    return null
  }

  if (!response.ok) {
    console.error('Lead lookup returned', response.status)
    return null
  }

  const rows = (await response.json().catch(() => null)) as LeadRow[] | null
  return rows?.[0] ?? null
}

/** Persist the analysis. A write failure must not lose the result. */
async function saveAnalysis(
  domain: string,
  analysis: Analysis,
  model: string | null,
  pagesFetched: number | null,
  token: string,
  env: ServerEnv,
): Promise<void> {
  // EVERY column is written explicitly, with `?? null` rather than a
  // possibly-undefined value.
  //
  // This is not defensive style, it is required. PostgREST builds the
  // ON CONFLICT DO UPDATE SET list from the keys PRESENT in the payload, and
  // JSON.stringify drops undefined ones. A re-analysis that omitted
  // `message` therefore left the previous run's message in place while
  // setting status to something else - tripping
  // lead_analyses_message_requires_success (23514) in production.
  // sync_to_supabase.py documents the same hazard for the leads upsert.
  const row = {
    domain,
    status: analysis.status,
    opportunity_category: analysis.opportunity_category ?? null,
    opportunity_observation: analysis.opportunity_observation ?? null,
    opportunity_evidence: analysis.opportunity_evidence ?? null,
    evidence_refs: analysis.evidence_refs ?? null,
    opportunity_confidence: analysis.opportunity_confidence ?? null,
    outreach_angle: analysis.outreach_angle ?? null,
    message: analysis.message ?? null,
    model,
    pages_fetched: pagesFetched,
    analyzed_at: new Date().toISOString(),
  }

  try {
    const response = await fetch(
      `${env.supabaseUrl}/rest/v1/lead_analyses?on_conflict=domain`,
      {
        method: 'POST',
        headers: {
          apikey: env.supabaseAnonKey,
          authorization: `Bearer ${token}`,
          'content-type': 'application/json',
          prefer: 'resolution=merge-duplicates,return=minimal',
        },
        body: JSON.stringify([row]),
      },
    )
    if (!response.ok) {
      console.error(
        'Saving the analysis failed:',
        response.status,
        await response.text().catch(() => '(no body)'),
      )
    }
  } catch (cause) {
    console.error(
      'Saving the analysis threw:',
      cause instanceof Error ? cause.message : 'unknown',
    )
  }
}

// ============================================================
// HANDLER
// ============================================================

/**
 * A complete `failed` Analysis.
 *
 * A real object rather than `{ status: 'failed' } as Analysis`: that cast
 * left every other field undefined, which JSON.stringify then dropped from
 * the upsert payload - see the note in saveAnalysis.
 */
function failedAnalysis(reason: string): Analysis {
  return {
    status: 'failed',
    opportunity_category: null,
    opportunity_observation: null,
    opportunity_evidence: null,
    evidence_refs: null,
    opportunity_confidence: null,
    outreach_angle: null,
    message: null,
    rejected_reason: reason,
  }
}

/** What the browser is told when a stage fails. No upstream detail. */
const FAILURE_MESSAGE: Record<string, string> = {
  unreachable: 'Could not reach this website.',
  timeout: 'The website took too long to respond.',
  rate_limited: 'Analysis is rate-limited right now. Try again in a minute.',
  not_configured: 'Analysis is not configured on the server.',
  unavailable: 'Could not analyze this lead right now.',
  invalid_json: 'Could not analyze this lead right now.',
  timed_out: 'Analysis took too long. Try again.',
  no_budget: 'Analysis took too long. Try again.',
}

export default async function handler(
  req: ApiRequest,
  res: ApiResponse,
): Promise<void> {
  const deadline = Date.now() + ANALYZE_BUDGET_MS

  if (req.method !== 'POST') {
    sendJson(res, { error: 'Method not allowed.' }, 405)
    return
  }

  const env = readEnv()
  if (!env) {
    sendJson(res, { error: 'Server is not configured.' }, 500)
    return
  }

  // Only signed-in users may spend an analysis.
  const auth = await requireUser(req, env)
  if (!auth.ok) {
    sendJson(res, { error: auth.error }, auth.status)
    return
  }

  // requireUser has already verified this token against Supabase; reuse it
  // so every read and write happens as the caller under RLS.
  const header = (req.headers as Record<string, string | undefined>)
  const token = String(
    header['authorization'] ?? header['Authorization'] ?? '',
  ).replace(/^bearer\s+/i, '').trim()

  const body = (req as { body?: unknown }).body
  const parsed =
    typeof body === 'string'
      ? (() => { try { return JSON.parse(body) } catch { return null } })()
      : body
  const domain =
    parsed && typeof parsed === 'object'
      ? String((parsed as Record<string, unknown>).domain ?? '').trim().toLowerCase()
      : ''

  if (!domain || domain.length > 253 || !/^[a-z0-9.-]+$/.test(domain)) {
    sendJson(res, { error: 'A valid lead domain is required.' }, 400)
    return
  }

  // The lead - and therefore the website - comes from the database, never
  // from the request. A client cannot redirect the fetcher.
  const lead = await loadLead(domain, token, env)
  if (!lead) {
    sendJson(res, { error: 'That lead was not found.' }, 404)
    return
  }

  if (inFlight.has(domain)) {
    sendJson(res, { error: 'This lead is already being analyzed.' }, 409)
    return
  }
  inFlight.add(domain)

  try {
    const collected = await collectEvidence(
      lead.website_url,
      lead.domain,
      // Leave room for the model call; the fetcher stops early rather than
      // consuming the whole budget on a slow site.
      deadline - MIN_MODEL_BUDGET_MS,
    )

    if (!collected.evidence) {
      const reason = collected.error ?? 'unreachable'
      await saveAnalysis(
        domain, failedAnalysis(`website fetch failed: ${reason}`),
        null, 0, token, env,
      )
      sendJson(res, { status: 'failed', error: FAILURE_MESSAGE[reason] })
      return
    }

    const evidence = collected.evidence

    // Not worth a model call - and not a failure either.
    if (!hasEnoughEvidence(evidence)) {
      const analysis: Analysis = {
        status: 'insufficient_evidence',
        opportunity_category: null, opportunity_observation: null,
        opportunity_evidence: null, evidence_refs: null,
        opportunity_confidence: null, outreach_angle: null, message: null,
        rejected_reason: 'no products, collections or navigation found',
      }
      await saveAnalysis(domain, analysis, null, evidence.pages_fetched, token, env)
      sendJson(res, publicView(analysis, evidence.pages_fetched))
      return
    }

    if (deadline - Date.now() < MIN_MODEL_BUDGET_MS) {
      sendJson(res, { status: 'failed', error: FAILURE_MESSAGE.no_budget })
      return
    }

    const result = await complete(
      buildMessages(evidence, lead.business_name),
      { name: 'lead_analysis', schema: RESPONSE_SCHEMA },
      deadline,
    )

    if (!result.ok) {
      await saveAnalysis(
        domain, failedAnalysis(`model call failed: ${result.failure}`), null,
        evidence.pages_fetched, token, env,
      )
      sendJson(res, {
        status: 'failed',
        error: FAILURE_MESSAGE[result.failure] ?? FAILURE_MESSAGE.unavailable,
      })
      return
    }

    const analysis = validateAnalysis(result.data, evidence)
    if (analysis.rejected_reason) {
      // Server-side only: useful for tuning, never shown to the prospect
      // or the browser.
      console.error(
        `Analysis for ${domain} downgraded to ${analysis.status}:`,
        analysis.rejected_reason,
      )
    }

    await saveAnalysis(
      domain, analysis, result.model, evidence.pages_fetched, token, env,
    )
    sendJson(res, publicView(analysis, evidence.pages_fetched))
  } finally {
    inFlight.delete(domain)
  }
}

/**
 * The browser's view of an analysis.
 *
 * `rejected_reason` and `evidence_refs` are deliberately omitted: the first
 * is internal tuning detail, the second is only meaningful against the
 * evidence, which the browser never receives.
 */
function publicView(analysis: Analysis, pagesFetched: number) {
  return {
    status: analysis.status,
    opportunity:
      analysis.status === 'success'
        ? {
            category: analysis.opportunity_category,
            observation: analysis.opportunity_observation,
            evidence: analysis.opportunity_evidence,
            confidence: analysis.opportunity_confidence,
          }
        : null,
    outreach_angle: analysis.outreach_angle,
    message: analysis.message,
    pages_fetched: pagesFetched,
  }
}
