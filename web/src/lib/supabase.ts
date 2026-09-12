import { createClient } from '@supabase/supabase-js'

// Both values are public by design. The anon key identifies the project and
// grants nothing on its own: every read is authorised by RLS against the
// signed-in user's JWT, inside Postgres. There is no service-role key in this
// application and there must never be one - it bypasses RLS entirely.
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error(
    'Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY. ' +
      'Copy web/.env.example to web/.env.local and fill in both values ' +
      '(or set them in the hosting provider for a deployed build).',
  )
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: {
    // Keeps the session in localStorage so a reload stays signed in.
    persistSession: true,
    autoRefreshToken: true,
    // No OAuth/magic-link redirects in V1, so there is no URL hash to parse.
    detectSessionInUrl: false,
  },
})
