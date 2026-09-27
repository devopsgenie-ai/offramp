# Readiness audit: settings-prefix (unknown)

critical: 0  high: 0  medium: 0  low: 0 · 7 checks: 0 found, 2 clean, 4 not applicable, 1 could not assess

This report is produced by reading the repository only. It sends no request to the running application and holds no credential. Secret values are never copied into it; findings cite a file and line instead.

## Checked and clean

- `credential.committed`
- `platform.hardcoded_url`

## Could not assess

- `service.no_health_endpoint`: routes could not be determined for `backend`: a router prefix is read from configuration at runtime

## Not applicable

- `frontend.secret_in_bundle`: no frontend service in the repository
- `supabase.rls.disabled`: the application does not use Supabase
- `supabase.rls.permissive_write`: the application does not use Supabase
- `supabase.rls.public_read`: the application does not use Supabase

## Not visible from the repository

- **Settings made in the platform's dashboard.** Environment variables, secrets and domains configured outside the repository. Review them in the platform's project settings before relying on this report.
- **Git history.** This audit reads the files as they are now. A secret removed in a later commit is still in the history. Run a history-wide secret scanner, for example gitleaks or trufflehog.
- **Who has access.** Members of the platform account, collaborators on the repository, and anyone with database dashboard access. List them, and remove anyone who no longer needs access.
- **Backups.** Whether the database is backed up, and whether a restore has ever been tested. Check the database provider's backup settings and do one test restore.
