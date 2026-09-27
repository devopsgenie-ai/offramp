"""The audit report: one document, rendered as JSON for machines and Markdown for people.

Byte-stable. It carries the source commit instead of a timestamp, and orders findings by
severity, then id. It names no vendor: remedies describe what to do, not whom to pay
(ROADMAP.md: `offramp` "does not steer you").

The "not visible from the repository" section is static text chosen by what the scan
found. It is the most honest part of the report: a policy changed in a dashboard never
reaches a migration file, and a clean result says nothing about it.
"""

from __future__ import annotations

from findings import ASSESSMENT_STATUSES, FINDING_SEVERITY_ORDER

SCHEMA = "offramp.audit/1"

_COMMON = (
    ("Settings made in the platform's dashboard",
     "Environment variables, secrets and domains configured outside the repository.",
     "Review them in the platform's project settings before relying on this report."),
    ("Git history",
     "This audit reads the files as they are now. A secret removed in a later commit is "
     "still in the history.",
     "Run a history-wide secret scanner, for example gitleaks or trufflehog."),
    ("Who has access",
     "Members of the platform account, collaborators on the repository, and anyone "
     "with database dashboard access.",
     "List them, and remove anyone who no longer needs access."),
    ("Backups",
     "Whether the database is backed up, and whether a restore has ever been tested.",
     "Check the database provider's backup settings and do one test restore."),
)

_SUPABASE = (
    ("Row-level security changed in the Supabase dashboard",
     "Policies and RLS settings created in the dashboard never appear in "
     "`supabase/migrations/`, so the RLS checks cannot see them.",
     "Run the Security Advisor in the Supabase dashboard and fix what it reports."),
    ("Supabase auth settings",
     "Whether anyone can sign up, whether email confirmation is required, and which "
     "redirect URLs are allowed.",
     "Review Authentication settings in the Supabase dashboard. Open sign-up makes "
     "every `authenticated` policy effectively public."),
    ("Storage bucket policies",
     "Which buckets are public and who can upload.",
     "Review Storage policies in the Supabase dashboard."),
)

_MONGODB = (
    ("Database network access",
     "Whether the MongoDB instance accepts connections from any address.",
     "Restrict network access to the addresses your backend runs from."),
)


def not_visible(appspec, has_supabase: bool) -> list[dict]:
    rows = list(_COMMON)
    if has_supabase:
        rows.extend(_SUPABASE)
    if any(store.kind == "mongodb" for store in appspec.datastores):
        rows.extend(_MONGODB)
    return [{"topic": topic, "why": why, "how": how} for topic, why, how in rows]


def summarise(findings: list[dict], assessments: list[dict]) -> dict:
    return {
        "findings": {level: sum(1 for f in findings if f["severity"] == level)
                     for level in FINDING_SEVERITY_ORDER},
        "checks": {status: sum(1 for a in assessments if a["status"] == status)
                   for status in ASSESSMENT_STATUSES},
    }


def _heading(app: dict) -> str:
    detail = [app["platform"]]
    if app.get("commit"):
        detail.append(app["commit"][:7])
    return f"# Readiness audit: {app['name'] or '(unnamed)'} ({', '.join(detail)})"


def render_audit_markdown(document: dict) -> str:
    summary = document["summary"]
    counts = "  ".join(f"{level}: {n}" for level, n in summary["findings"].items())
    checks = summary["checks"]
    lines = [
        _heading(document["app"]),
        "",
        f"{counts} · {sum(checks.values())} checks: {checks['found']} found, "
        f"{checks['clean']} clean, {checks['not_applicable']} not applicable, "
        f"{checks['could_not_assess']} could not assess",
        "",
        "This report is produced by reading the repository only. It sends no request to "
        "the running application and holds no credential. Secret values are never "
        "copied into it; findings cite a file and line instead.",
        "",
    ]

    for level in FINDING_SEVERITY_ORDER:
        selected = [f for f in document["findings"] if f["severity"] == level]
        if not selected:
            continue
        lines += [f"## {level}", ""]
        for finding in selected:
            lines += [f"### {finding['title']}", "", finding["detail"], ""]
            if finding["evidence"]:
                lines.append("- evidence: " + ", ".join(f"`{e}`" for e in finding["evidence"]))
            lines.append(f"- remedy: {finding['remedy']}")
            lines.append(f"- id: `{finding['id']}`")
            lines.append("")

    by_status = {status: [a for a in document["assessments"] if a["status"] == status]
                 for status in ASSESSMENT_STATUSES}
    if by_status["clean"]:
        lines += ["## Checked and clean", ""]
        lines += [f"- `{a['check']}`" for a in by_status["clean"]]
        lines.append("")
    if by_status["could_not_assess"]:
        lines += ["## Could not assess", ""]
        lines += [f"- `{a['check']}`: {a['reason']}" for a in by_status["could_not_assess"]]
        lines.append("")
    if by_status["not_applicable"]:
        lines += ["## Not applicable", ""]
        lines += [f"- `{a['check']}`: {a['reason']}" for a in by_status["not_applicable"]]
        lines.append("")

    lines += ["## Not visible from the repository", ""]
    for row in document["not_visible"]:
        lines.append(f"- **{row['topic']}.** {row['why']} {row['how']}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"
