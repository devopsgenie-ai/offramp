"""Findings and assessments: what `audit` knows about the application as it runs today.

RFC-0002, "Findings are not gaps". A gap is something the tool does not know and needs
in order to render. A finding is something it does know about the application as it
runs today. They are different axes, and the project's quality metric depends on keeping
them apart: if findings counted as gaps, an application with more problems would look
like a worse detector.

`severity` here is deliberately a different scale from gap severity. `blocking` asks
"will the output work?"; `critical` asks "is someone exposed right now?". Nothing maps
one onto the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FINDING_CATEGORIES = ("security", "portability", "operability")
FINDING_SEVERITIES = ("critical", "high", "medium", "low")
ASSESSMENT_STATUSES = ("found", "clean", "not_applicable", "could_not_assess")

#: Report order. Critical first, because critical means someone is exposed now.
FINDING_SEVERITY_ORDER = FINDING_SEVERITIES


@dataclass
class Finding:
    id: str                # "<check>.<subject>", stable and name-based
    check: str             # the check that produced it
    category: str          # security | portability | operability
    severity: str          # critical | high | medium | low
    title: str             # one line, in the user's terms
    detail: str            # what, and why it matters; enough to act on alone
    remedy: str            # generic; names no vendor, product or service
    evidence: list[str]    # path:line, never a value
    related_gaps: list[str] = field(default_factory=list)


@dataclass
class Assessment:
    """One per check, per run -- including the checks that found nothing.

    A clean report is a claim, and it has to show what it checked. A check that could not
    look reports `could_not_assess` with a reason and never `clean`.
    """

    check: str
    status: str            # found | clean | not_applicable | could_not_assess
    reason: str | None = None


def check_finding_enums(findings: list[Finding], assessments: list[Assessment]) -> list[str]:
    problems: list[str] = []
    for finding in findings:
        if finding.category not in FINDING_CATEGORIES:
            problems.append(f"{finding.id}: category={finding.category!r}")
        if finding.severity not in FINDING_SEVERITIES:
            problems.append(f"{finding.id}: severity={finding.severity!r}")
        if not finding.id.startswith(finding.check + "."):
            problems.append(f"{finding.id}: id does not start with its check {finding.check!r}")
    for assessment in assessments:
        if assessment.status not in ASSESSMENT_STATUSES:
            problems.append(f"{assessment.check}: status={assessment.status!r}")
        needs_reason = assessment.status in ("not_applicable", "could_not_assess")
        if needs_reason and not assessment.reason:
            problems.append(f"{assessment.check}: {assessment.status} needs a reason")
    return problems
