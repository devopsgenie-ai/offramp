# offramp

**Is the app you built on Lovable or Emergent safe to keep running, and what would it take
to own it?** `offramp audit` reads the repository and tells you. It sends no request to
your app and needs no credentials or account. Nothing leaves your machine.

```bash
git clone https://github.com/devopsgenie-ai/offramp
python3 offramp/skills/offramp/scripts/audit.py path/to/your-app --out audit-report
```

That writes `audit-report/report.md` for you to read and `audit-report/report.json` for
tools. It needs Python 3.11 or later and nothing else.

## What it checks

| Check | Severity | What it finds |
|---|---|---|
| `supabase.rls.disabled` | critical | A table the app exposes through Supabase with no row-level security. Anyone with the public key can read and change every row, and every visitor's browser has that key. |
| `supabase.rls.permissive_write` | high / medium | A policy that lets anyone, or any signed-in user, update or delete rows with a condition of `true`. An anonymous insert, such as a contact form, is reported as medium. |
| `supabase.rls.public_read` | medium | A table anyone can read in full. This can be intended; the report asks you to confirm. |
| `credential.committed` | critical | A password, token or `service_role` key committed to the repository. Deleting the file does not help, because it stays in the git history. |
| `frontend.secret_in_bundle` | critical / high | A secret shipped to every browser: a `VITE_*` or `REACT_APP_*` variable with a secret's name, or a `service_role` key in frontend code. |
| `platform.hardcoded_url` | medium / low | What stops working when you leave the platform: sign-in through the platform's own auth, AI calls through its gateway, a frontend built against its preview URL. |
| `service.no_health_endpoint` | low | A backend that nothing can ask "are you working?". |

The row-level-security checks replay `supabase/migrations/` in order. A table whose RLS
is enabled three migrations later is reported as clean, and a dropped table is not
reported. A publishable or anon key is never a finding. It is public by design, which is
why the RLS checks exist.

## What it tells you it could not check

A clean report is only worth something if it says what it looked at. Every check reports
one of four results: found, clean, not applicable, or **could not assess**. When a
migration uses dynamic SQL, or your tables were created in the Supabase dashboard rather
than in migrations, the RLS checks say so and give no answer. They never give a partial
one.

Every report ends with what cannot be seen from a repository at all. That covers policies
changed in the dashboard, auth settings, backups, who has access, and the git history.
For each one it points to where you can check.

## In CI

```bash
python3 skills/offramp/scripts/audit.py . --out audit-report --fail-on high
```

This exits `1` when a finding is at or above the threshold, `0` otherwise, and `2` if the
tool itself fails. The report is byte-stable: the same commit always gives the same
report.

## How accurate it is

Before release we ran it over 190 public repositories built on Lovable and Emergent. We
read a sample of the findings by hand and fixed every false positive we found. The method
and the per-check precision are in
[the pull request that did it](https://github.com/devopsgenie-ai/offramp/pull/16).
Results are only ever published in aggregate. No repository is named.

If it reports something wrong about your app, please
[open an issue](https://github.com/devopsgenie-ai/offramp/issues) with the finding's `id`.
A false positive is a bug.

## What it will never do

`offramp` is a code reader and author, not a deployer. It does not hold cloud credentials,
send requests to your running application, or change anything. It reads files; you decide
what to do. The same line holds for the generators on the [roadmap](ROADMAP.md): they
write configuration for you to review, and your own pipeline applies it.

It also names no vendor in its reports, including the people who maintain it.

## Getting help

The report tells you what to fix and how. If you would rather have someone do it, the
maintainers at [DevOps Genie](https://devopsgenie.ai/contact) offer fixed-price help.
The tool itself will never recommend them, or anyone else.

## Contributing

Read [AGENTS.md](AGENTS.md) first. It applies to humans and coding agents alike. Design
changes go through an [RFC](rfcs/README.md).

## License

MIT. See [LICENSE](LICENSE). Maintained by [DevOps Genie](https://devopsgenie.ai).
