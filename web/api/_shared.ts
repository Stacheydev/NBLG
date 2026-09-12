/**
 * Shared server-side helpers for the generation endpoints.
 *
 * The `_` prefix keeps Vercel from exposing this file as a route.
 *
 * Everything privileged lives here and only here: the GitHub token is read
 * from the server environment, used to sign outbound GitHub requests, and is
 * never returned to the browser - not in a success body, not in an error.
 */

// Declared locally so these functions need no @types/node dependency.
declare const process: { env: Record<string, string | undefined> }

// The one existing generator. Deliberately constants rather than environment
// variables: there is exactly one workflow and one branch, and making them
// configurable would invite pointing the button at something else.
export const REPO_OWNER = 'Stacheydev'
export const REPO_NAME = 'NBLG'
export const WORKFLOW_FILE = 'generate-leads.yml'
export const WORKFLOW_REF = 'main'

const GITHUB_API = 'https://api.github.com'

export interface ServerEnv {
  githubToken: string
  supabaseUrl: string
  supabaseAnonKey: string
}

/** JSON response with caching disabled - run state must never be cached. */
export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'content-type': 'application/json',
      'cache-control': 'no-store',
    },
  })
}

/**
 * Server-side configuration, or null when incomplete.
 *
 * Read per request rather than at module scope so a missing variable produces
 * a logged 500 instead of an opaque cold-start crash.
 */
export function readEnv(): ServerEnv | null {
  const githubToken = process.env.GITHUB_DISPATCH_TOKEN
  const supabaseUrl = process.env.SUPABASE_URL
  const supabaseAnonKey = process.env.SUPABASE_ANON_KEY

  const missing: string[] = []
  if (!githubToken) missing.push('GITHUB_DISPATCH_TOKEN')
  if (!supabaseUrl) missing.push('SUPABASE_URL')
  if (!supabaseAnonKey) missing.push('SUPABASE_ANON_KEY')

  if (!githubToken || !supabaseUrl || !supabaseAnonKey) {
    // Names only - never values.
    console.error('Missing server environment variable(s):', missing.join(', '))
    return null
  }

  return {
    githubToken,
    supabaseUrl: supabaseUrl.replace(/\/+$/, ''),
    supabaseAnonKey,
  }
}

export type AuthResult =
  | { ok: true; userId: string }
  | { ok: false; response: Response }

/**
 * Require a valid Supabase session on the request.
 *
 * The browser sends its existing Supabase access token as a bearer token; we
 * hand it to Supabase's own /auth/v1/user endpoint and trust that verdict.
 * This needs only the public anon key, so no service-role key and no JWT
 * secret is introduced anywhere in this application.
 */
export async function requireUser(
  request: Request,
  env: ServerEnv,
): Promise<AuthResult> {
  const header = request.headers.get('authorization') ?? ''
  const token = /^bearer\s+/i.test(header)
    ? header.replace(/^bearer\s+/i, '').trim()
    : ''

  if (!token) {
    return { ok: false, response: json({ error: 'Not signed in.' }, 401) }
  }

  let lookup: Response
  try {
    lookup = await fetch(`${env.supabaseUrl}/auth/v1/user`, {
      headers: {
        apikey: env.supabaseAnonKey,
        authorization: `Bearer ${token}`,
      },
    })
  } catch (cause) {
    console.error('Supabase auth lookup failed:', cause)
    return {
      ok: false,
      response: json({ error: 'Could not verify your session.' }, 503),
    }
  }

  if (!lookup.ok) {
    return { ok: false, response: json({ error: 'Not signed in.' }, 401) }
  }

  const user = (await lookup.json().catch(() => null)) as {
    id?: string
  } | null

  if (!user?.id) {
    return { ok: false, response: json({ error: 'Not signed in.' }, 401) }
  }

  return { ok: true, userId: user.id }
}

/** Authenticated GitHub API call. The token never leaves this function. */
export async function github(
  path: string,
  env: ServerEnv,
  init: { method?: string; body?: string } = {},
): Promise<Response> {
  const headers: Record<string, string> = {
    accept: 'application/vnd.github+json',
    authorization: `Bearer ${env.githubToken}`,
    'x-github-api-version': '2022-11-28',
    // GitHub rejects API requests that send no User-Agent.
    'user-agent': 'nblg-dashboard',
  }
  if (init.body !== undefined) headers['content-type'] = 'application/json'

  return fetch(`${GITHUB_API}${path}`, {
    method: init.method ?? 'GET',
    headers,
    ...(init.body !== undefined ? { body: init.body } : {}),
  })
}

export interface WorkflowRun {
  id: number
  status: string | null
  conclusion: string | null
}

/** The only states the browser is ever told about. */
export type GenerationState =
  | 'queued'
  | 'in_progress'
  | 'completed_success'
  | 'completed_failure'

// Every GitHub status that means "this run has not finished yet".
const ACTIVE_STATUSES = new Set([
  'queued',
  'requested',
  'waiting',
  'pending',
  'in_progress',
])

export function isActive(run: WorkflowRun): boolean {
  return run.status !== null && ACTIVE_STATUSES.has(run.status)
}

export function runState(run: WorkflowRun): GenerationState {
  if (run.status !== 'completed') {
    return run.status === 'in_progress' ? 'in_progress' : 'queued'
  }
  // Anything that is not an outright success is a failure as far as the UI is
  // concerned - cancelled, timed_out and failure all mean "did not complete".
  return run.conclusion === 'success' ? 'completed_success' : 'completed_failure'
}

/**
 * Recent runs of the existing workflow on `main`, newest first, or null when
 * GitHub could not be reached.
 *
 * No event filter: the workflow is workflow_dispatch-only, so every run of it
 * is one of ours, and filtering could silently miss a run.
 */
export async function listRuns(env: ServerEnv): Promise<WorkflowRun[] | null> {
  const path =
    `/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}` +
    `/runs?branch=${WORKFLOW_REF}&per_page=20`

  let response: Response
  try {
    response = await github(path, env)
  } catch (cause) {
    console.error('GitHub run listing threw:', cause)
    return null
  }

  if (!response.ok) {
    console.error(
      'GitHub run listing failed:',
      response.status,
      await response.text().catch(() => '(no body)'),
    )
    return null
  }

  const body = (await response.json().catch(() => null)) as {
    workflow_runs?: WorkflowRun[]
  } | null

  return body?.workflow_runs ?? []
}
