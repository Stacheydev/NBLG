/// <reference types="vite/client" />

// Declared explicitly so the two public Supabase values are typed as strings
// rather than `any` where they are read.
interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string
  readonly VITE_SUPABASE_ANON_KEY: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
