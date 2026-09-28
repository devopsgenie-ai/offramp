# Readiness audit: task-board (lovable)

critical: 1  high: 2  medium: 2  low: 1 · 7 checks: 5 found, 1 clean, 1 not applicable, 0 could not assess

This report is produced by reading the repository only. It sends no request to the running application and holds no credential. Secret values are never copied into it; findings cite a file and line instead.

## critical

### Table `public.notes` has no row-level security

`public.notes` is created in the migrations and row-level security is never enabled on it. Supabase exposes tables in `public` through its API, and the key that reaches that API is in every visitor's browser. Anyone can read, change and delete every row.

- evidence: `supabase/migrations/20260110093000_init.sql:45`
- remedy: Enable row-level security on the table and add policies scoped to the row's owner. Until then, treat the data in it as exposed.
- id: `supabase.rls.disabled.public.notes`

## high

### `VITE_OPENAI_API_KEY` looks like a secret, and it is shipped to every browser

`VITE_OPENAI_API_KEY` is substituted into the JavaScript bundle at build time, so whatever value it holds is readable by anyone who loads the page. Its name says it is a secret. If it is, it has been public for as long as the application has been deployed.

- evidence: `src/lib/ai.ts:2`
- remedy: If this is a real credential, revoke it and move the call that needs it behind a server-side function. Rebuilding with a new value does not help: the new value is baked into the new bundle too.
- id: `frontend.secret_in_bundle.web.VITE_OPENAI_API_KEY`

### Policy "Authenticated users can update tasks" lets any signed-in user change rows in `public.tasks`

The policy "Authenticated users can update tasks" on `public.tasks` allows UPDATE for any signed-in user. If sign-up is open, that is anyone. Its condition is simply `true`, so it never checks whose row it is.

- evidence: `supabase/migrations/20260110093000_init.sql:31`
- remedy: Replace `true` with a condition on the row's owner, for example `auth.uid() = user_id`, or remove the policy if nobody should do this.
- id: `supabase.rls.permissive_write.public.tasks.authenticated-users-can-update-tasks`

## medium

### AI calls go through Lovable's AI gateway

Model calls are sent to Lovable's AI gateway with a Lovable key. They stop working outside Lovable, and usage is billed through the platform.

- evidence: `supabase/functions/send-digest/index.ts:6`
- remedy: Call the model provider directly with your own key, from a server-side function.
- id: `platform.hardcoded_url.lovable-ai-gateway`

### Every row in `public.profiles` is readable without signing in

The policy "Profiles are viewable by everyone" lets anyone read every row of `public.profiles`. That can be intended -- a public catalogue, published posts -- but it also applies to every column, including any added later.

- evidence: `supabase/migrations/20260110093000_init.sql:10`
- remedy: Confirm the table holds nothing private. If it does, restrict the policy, or move the private columns to a table with its own policies.
- id: `supabase.rls.public_read.public.profiles.profiles-are-viewable-by-everyone`

## low

### Lovable's editor script loads in production

`index.html` loads `gptengineer.js` from Lovable's CDN. Every visitor downloads a third-party script that the app does not need to run.

- evidence: `index.html:7`
- remedy: Remove the script tag from `index.html` once you no longer edit in Lovable.
- id: `platform.hardcoded_url.lovable-editor-script`

## Checked and clean

- `credential.committed`

## Not applicable

- `service.no_health_endpoint`: no backend service in the repository

## Not visible from the repository

- **Settings made in the platform's dashboard.** Environment variables, secrets and domains configured outside the repository. Review them in the platform's project settings before relying on this report.
- **Git history.** This audit reads the files as they are now. A secret removed in a later commit is still in the history. Run a history-wide secret scanner, for example gitleaks or trufflehog.
- **Who has access.** Members of the platform account, collaborators on the repository, and anyone with database dashboard access. List them, and remove anyone who no longer needs access.
- **Backups.** Whether the database is backed up, and whether a restore has ever been tested. Check the database provider's backup settings and do one test restore.
- **Row-level security changed in the Supabase dashboard.** Policies and RLS settings created in the dashboard never appear in `supabase/migrations/`, so the RLS checks cannot see them. Run the Security Advisor in the Supabase dashboard and fix what it reports.
- **Supabase auth settings.** Whether anyone can sign up, whether email confirmation is required, and which redirect URLs are allowed. Review Authentication settings in the Supabase dashboard. Open sign-up makes every `authenticated` policy effectively public.
- **Storage bucket policies.** Which buckets are public and who can upload. Review Storage policies in the Supabase dashboard.

---

Generated by [offramp](https://github.com/devopsgenie-ai/offramp) audit. Re-run it on the same commit to reproduce this report.
