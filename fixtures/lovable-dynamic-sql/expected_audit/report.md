# Readiness audit: booking-desk (lovable)

critical: 0  high: 0  medium: 0  low: 0 · 6 checks: 0 found, 2 clean, 1 not applicable, 3 could not assess

This report is produced by reading the repository only. It sends no request to the running application and holds no credential. Secret values are never copied into it; findings cite a file and line instead.

## Checked and clean

- `credential.committed`
- `frontend.secret_in_bundle`

## Could not assess

- `supabase.rls.disabled`: a migration could not be replayed, so any answer would be partial: supabase/migrations/20260203090000_per_tenant.sql:3: do block with dynamic SQL touching tables or RLS
- `supabase.rls.permissive_write`: a migration could not be replayed, so any answer would be partial: supabase/migrations/20260203090000_per_tenant.sql:3: do block with dynamic SQL touching tables or RLS
- `supabase.rls.public_read`: a migration could not be replayed, so any answer would be partial: supabase/migrations/20260203090000_per_tenant.sql:3: do block with dynamic SQL touching tables or RLS

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
