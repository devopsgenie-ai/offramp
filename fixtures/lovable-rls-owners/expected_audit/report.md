# Readiness audit: team-journal (lovable)

critical: 3  high: 5  medium: 1  low: 0 · 7 checks: 2 found, 4 clean, 1 not applicable, 0 could not assess

This report is produced by reading the repository only. It sends no request to the running application and holds no credential. Secret values are never copied into it; findings cite a file and line instead.

## critical

### Table `public.bookmarks` has no row-level security

`public.bookmarks` is created in the migrations and row-level security is never enabled on it. Supabase exposes tables in `public` through its API, and the key that reaches that API is in every visitor's browser. Anyone can read, change and delete every row.

- evidence: `supabase/migrations/20260302100000_tables.sql:12`
- remedy: Enable row-level security on the table and add policies scoped to the row's owner. Until then, treat the data in it as exposed.
- id: `supabase.rls.disabled.public.bookmarks`

### Table `public.journal` has no row-level security

`public.journal` is created in the migrations and row-level security is never enabled on it. Supabase exposes tables in `public` through its API, and the key that reaches that API is in every visitor's browser. Anyone can read, change and delete every row.

- evidence: `supabase/migrations/20260302100000_tables.sql:5`
- remedy: Enable row-level security on the table and add policies scoped to the row's owner. Until then, treat the data in it as exposed.
- id: `supabase.rls.disabled.public.journal`

### Table `public.notes` has no row-level security

`public.notes` is created in the migrations and row-level security is never enabled on it. Supabase exposes tables in `public` through its API, and the key that reaches that API is in every visitor's browser. Anyone can read, change and delete every row.

- evidence: `supabase/migrations/20260302100000_tables.sql:19`
- remedy: Enable row-level security on the table and add policies scoped to the row's owner. Until then, treat the data in it as exposed.
- id: `supabase.rls.disabled.public.notes`

## high

### Policy "Signed-in users can update assignments" lets any signed-in user change rows in `public.assignments`

The policy "Signed-in users can update assignments" on `public.assignments` allows UPDATE for any signed-in user. If sign-up is open, that is anyone. Its condition is simply `true`, so it never checks whose row it is.

- evidence: `supabase/migrations/20260302100000_tables.sql:44`
- remedy: Replace `true` with a condition on the row's owner, for example `auth.uid() = user_id`, or remove the policy if nobody should do this.
- id: `supabase.rls.permissive_write.public.assignments.signed-in-users-can-update-assignments`

### Policy "Anyone can do anything with comments" lets anyone change rows in `public.comments`

The policy "Anyone can do anything with comments" on `public.comments` allows every operation for anyone, without signing in. Its condition is simply `true`, so it never checks whose row it is.

- evidence: `supabase/migrations/20260302100000_tables.sql:33`
- remedy: Replace `true` with a condition on the row's owner, for example `auth.uid() = user_id`, or remove the policy if nobody should do this.
- id: `supabase.rls.permissive_write.public.comments.anyone-can-do-anything-with-comments`

### Policy "Anyone can delete drafts" lets anyone change rows in `public.drafts`

The policy "Anyone can delete drafts" on `public.drafts` allows DELETE for anyone, without signing in. Its condition is simply `true`, so it never checks whose row it is.

- evidence: `supabase/migrations/20260302100000_tables.sql:70`
- remedy: Replace `true` with a condition on the row's owner, for example `auth.uid() = user_id`, or remove the policy if nobody should do this.
- id: `supabase.rls.permissive_write.public.drafts.anyone-can-delete-drafts`

### Policy "Anyone signed in can delete invoices" lets any signed-in user change rows in `public.invoices`

The policy "Anyone signed in can delete invoices" on `public.invoices` allows DELETE for any signed-in user. If sign-up is open, that is anyone. Its condition is simply `true`, so it never checks whose row it is.

- evidence: `supabase/migrations/20260302100000_tables.sql:58`
- remedy: Replace `true` with a condition on the row's owner, for example `auth.uid() = user_id`, or remove the policy if nobody should do this.
- id: `supabase.rls.permissive_write.public.invoices.anyone-signed-in-can-delete-invoices`

### Policy "Members can update projects" lets any signed-in user change rows in `public.projects`

The policy "Members can update projects" on `public.projects` allows UPDATE for any signed-in user. If sign-up is open, that is anyone. Its condition is simply `true`, so it never checks whose row it is.

- evidence: `supabase/migrations/20260302100000_tables.sql:85`
- remedy: Replace `true` with a condition on the row's owner, for example `auth.uid() = user_id`, or remove the policy if nobody should do this.
- id: `supabase.rls.permissive_write.public.projects.members-can-update-projects`

## medium

### Policy "Anyone can join the waitlist" lets anyone add rows in `public.waitlist`

The policy "Anyone can join the waitlist" on `public.waitlist` allows INSERT for anyone, without signing in. Its condition is simply `true`, so it never checks whose row it is.

- evidence: `supabase/migrations/20260302100000_tables.sql:94`
- remedy: Replace `true` with a condition on the row's owner, for example `auth.uid() = user_id`, or remove the policy if nobody should do this.
- id: `supabase.rls.permissive_write.public.waitlist.anyone-can-join-the-waitlist`

## Checked and clean

- `credential.committed`
- `frontend.secret_in_bundle`
- `platform.hardcoded_url`
- `supabase.rls.public_read`

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
