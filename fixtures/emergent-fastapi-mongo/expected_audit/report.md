# Readiness audit: widgets (emergent)

critical: 2  high: 0  medium: 0  low: 0 · 3 checks: 1 found, 2 clean, 0 not applicable, 0 could not assess

This report is produced by reading the repository only. It sends no request to the running application and holds no credential. Secret values are never copied into it; findings cite a file and line instead.

## critical

### `JWT_SECRET` is committed to the repository

`JWT_SECRET` is committed with a real-looking value in `backend/.env`. Anyone who can read this repository, or any copy or fork of it, can use it. Deleting the file does not help: the value stays in the git history.

- evidence: `backend/.env:3`
- remedy: Rotate the credential at its source, then supply the new value through the environment rather than a committed file. Treat the old value as disclosed.
- id: `credential.committed.backend.env.JWT_SECRET`

### `MONGO_URL` is committed to the repository

`MONGO_URL` is committed with a real-looking value in `backend/.env`. Anyone who can read this repository, or any copy or fork of it, can use it. Deleting the file does not help: the value stays in the git history.

- evidence: `backend/.env:2`
- remedy: Rotate the credential at its source, then supply the new value through the environment rather than a committed file. Treat the old value as disclosed.
- id: `credential.committed.backend.env.MONGO_URL`

## Checked and clean

- `frontend.secret_in_bundle`
- `service.no_health_endpoint`

## Not visible from the repository

- **Settings made in the platform's dashboard.** Environment variables, secrets and domains configured outside the repository. Review them in the platform's project settings before relying on this report.
- **Git history.** This audit reads the files as they are now. A secret removed in a later commit is still in the history. Run a history-wide secret scanner, for example gitleaks or trufflehog.
- **Who has access.** Members of the platform account, collaborators on the repository, and anyone with database dashboard access. List them, and remove anyone who no longer needs access.
- **Backups.** Whether the database is backed up, and whether a restore has ever been tested. Check the database provider's backup settings and do one test restore.
- **Database network access.** Whether the MongoDB instance accepts connections from any address. Restrict network access to the addresses your backend runs from.
