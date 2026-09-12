/**
 * GET /api/generate-status?after=<runId> - progress of a generation run.
 *
 * Returns only a coarse state and the run id. No GitHub payload, no logs, no
 * job details, no credentials.
 */
import {
  type GenerationState,
  json,
  listRuns,
  readEnv,
  requireUser,
  runState,
  // .js extension required under Node ESM - see the note in generate.ts.
} from './_shared.js'

interface StatusBody {
  state: GenerationState
  runId: number | null
}

async function handler(request: Request): Promise<Response> {
  if (request.method !== 'GET') {
    return json({ error: 'Method not allowed.' }, 405)
  }

  const env = readEnv()
  if (!env) return json({ error: 'Server is not configured.' }, 500)

  const auth = await requireUser(request, env)
  if (!auth.ok) return auth.response

  const raw = new URL(request.url).searchParams.get('after') ?? '0'
  const after = Number(raw)
  if (!Number.isInteger(after) || after < 0) {
    return json({ error: 'Invalid request.' }, 400)
  }

  const runs = await listRuns(env)
  if (runs === null) {
    return json({ error: 'Could not reach GitHub.' }, 502)
  }

  const newer = runs.filter((run) => run.id > after)

  // Dispatch was accepted but GitHub has not materialised the run yet. That
  // is a normal few-second window, not an error.
  if (newer.length === 0) {
    const body: StatusBody = { state: 'queued', runId: null }
    return json(body)
  }

  const run = newer.reduce((latest, candidate) =>
    candidate.id > latest.id ? candidate : latest,
  )

  const body: StatusBody = { state: runState(run), runId: run.id }
  return json(body)
}

export default handler
export const GET = handler
