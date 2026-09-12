/**
 * GET /api/generate-status?after=<runId> - progress of a generation run.
 *
 * Returns only a coarse state and the run id. No GitHub payload, no logs, no
 * job details, no credentials.
 *
 * Signature is Vercel's Node (req, res) pair - see the note in generate.ts.
 */
import {
  type ApiRequest,
  type ApiResponse,
  type GenerationState,
  listRuns,
  queryValue,
  readEnv,
  requireUser,
  runState,
  sendJson,
  // .js extension required under Node ESM - see the note in generate.ts.
} from './_shared.js'

interface StatusBody {
  state: GenerationState
  runId: number | null
}

export default async function handler(
  req: ApiRequest,
  res: ApiResponse,
): Promise<void> {
  if (req.method !== 'GET') {
    sendJson(res, { error: 'Method not allowed.' }, 405)
    return
  }

  const env = readEnv()
  if (!env) {
    sendJson(res, { error: 'Server is not configured.' }, 500)
    return
  }

  const auth = await requireUser(req, env)
  if (!auth.ok) {
    sendJson(res, { error: auth.error }, auth.status)
    return
  }

  const after = Number(queryValue(req, 'after') ?? '0')
  if (!Number.isInteger(after) || after < 0) {
    sendJson(res, { error: 'Invalid request.' }, 400)
    return
  }

  const runs = await listRuns(env)
  if (runs === null) {
    sendJson(res, { error: 'Could not reach GitHub.' }, 502)
    return
  }

  const newer = runs.filter((run) => run.id > after)

  // Dispatch was accepted but GitHub has not materialised the run yet. That
  // is a normal few-second window, not an error.
  if (newer.length === 0) {
    const body: StatusBody = { state: 'queued', runId: null }
    sendJson(res, body)
    return
  }

  const run = newer.reduce((latest, candidate) =>
    candidate.id > latest.id ? candidate : latest,
  )

  const body: StatusBody = { state: runState(run), runId: run.id }
  sendJson(res, body)
}
