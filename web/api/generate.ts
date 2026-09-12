/**
 * POST /api/generate - start a lead-generation run.
 *
 * Triggers the EXISTING .github/workflows/generate-leads.yml on `main` via
 * workflow_dispatch. It does not reimplement any part of the generator: the
 * workflow still owns the baseline id, the Python run, the Supabase sync, the
 * journal check and the database commit.
 *
 * Responds with `after`: the highest workflow run id that existed *before*
 * dispatch. The browser passes it to /api/generate-status, which then treats
 * the first run with a larger id as this run. Run ids are monotonic, so this
 * identifies the run without adding an input to the workflow file.
 *
 * Signature is Vercel's Node (req, res) pair - that is what this runtime
 * actually invokes. A single default export, because named method exports are
 * a Web-handler convention this runtime ignores.
 */
import {
  type ApiRequest,
  type ApiResponse,
  REPO_NAME,
  REPO_OWNER,
  WORKFLOW_FILE,
  WORKFLOW_REF,
  github,
  isActive,
  listRuns,
  readEnv,
  requireUser,
  sendJson,
  // The .js extension is required, not optional: web/package.json sets
  // "type": "module", so Vercel's compiled output is ESM, and Node's ESM
  // resolver does not guess extensions. TypeScript maps './_shared.js' back
  // to './_shared.ts' at check time.
} from './_shared.js'

export default async function handler(
  req: ApiRequest,
  res: ApiResponse,
): Promise<void> {
  if (req.method !== 'POST') {
    sendJson(res, { error: 'Method not allowed.' }, 405)
    return
  }

  const env = readEnv()
  if (!env) {
    sendJson(res, { error: 'Server is not configured.' }, 500)
    return
  }

  // Only signed-in users may spend a generation run.
  const auth = await requireUser(req, env)
  if (!auth.ok) {
    sendJson(res, { error: auth.error }, auth.status)
    return
  }

  const runs = await listRuns(env)
  if (runs === null) {
    sendJson(res, { error: 'Could not reach GitHub.' }, 502)
    return
  }

  // A run is already queued or going. The workflow's own concurrency group
  // would serialise a second dispatch anyway, but queueing one would just
  // burn a redundant run - so report the live one and let the browser track
  // it instead. `- 1` makes the status endpoint's "first id greater than
  // after" find this very run.
  const active = runs.find(isActive)
  if (active) {
    sendJson(res, { state: 'already_running', after: active.id - 1 })
    return
  }

  const latestId = runs.reduce((highest, run) => Math.max(highest, run.id), 0)

  let dispatch: Response
  try {
    dispatch = await github(
      `/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      env,
      { method: 'POST', body: JSON.stringify({ ref: WORKFLOW_REF }) },
    )
  } catch (cause) {
    console.error('Workflow dispatch threw:', cause)
    sendJson(res, { error: 'Could not start generation.' }, 502)
    return
  }

  // A successful dispatch is 204 No Content and carries no run id, which is
  // exactly why `after` exists.
  if (!dispatch.ok) {
    console.error(
      'Workflow dispatch failed:',
      dispatch.status,
      await dispatch.text().catch(() => '(no body)'),
    )
    sendJson(res, { error: 'Could not start generation.' }, 502)
    return
  }

  sendJson(res, { state: 'queued', after: latestId })
}
