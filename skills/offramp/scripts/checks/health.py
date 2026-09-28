"""service.no_health_endpoint: a backend nothing can ask "are you working?".

Reuses the probe detector's condition. Two refusals carry over from it. A static
frontend has no health endpoint of its own by design, so it is not applicable here. And
when a service's routes could not be determined -- a router prefix read from settings --
"no health route found" is not the same as "no health route", so the check reports
could_not_assess rather than a finding it cannot stand behind.
"""

from __future__ import annotations

from pathlib import Path

from findings import Assessment, Finding

CHECK = "service.no_health_endpoint"
_BACKEND_ROLES = ("api", "worker")


def check_no_health_endpoint(root: Path, scan) -> tuple[list[Finding], Assessment]:
    backends = [s for s in scan.appspec.services if s.role in _BACKEND_ROLES]
    if not backends:
        return [], Assessment(check=CHECK, status="not_applicable",
                              reason="no backend service in the repository")
    gap_ids = {gap.id for gap in scan.gaps}
    findings: list[Finding] = []
    undecided: list[str] = []
    for service in backends:
        if f"service.{service.name}.probes" not in gap_ids:
            continue
        if any(gap_id.startswith(f"service.{service.name}.routes.") for gap_id in gap_ids):
            undecided.append(service.name)
            continue
        findings.append(Finding(
            id=f"{CHECK}.{service.name}",
            check=CHECK,
            category="operability",
            severity="low",
            title=f"`{service.name}` has no health endpoint",
            detail=(
                f"Nothing can ask `{service.name}` whether it is working. A hosting "
                f"platform, a load balancer or an uptime monitor can only tell that the "
                f"process started, so a deploy can report success while the service is "
                f"broken."
            ),
            remedy=(
                "Add a route such as `/health` that returns 200 without touching the "
                "database, and point your platform's health check at it."
            ),
            evidence=[],
            related_gaps=[f"service.{service.name}.probes"],
        ))
    if findings:
        return findings, Assessment(check=CHECK, status="found")
    if undecided:
        return [], Assessment(
            check=CHECK, status="could_not_assess",
            reason=("routes could not be determined for "
                    + ", ".join(f"`{name}`" for name in undecided)
                    + ": a router prefix is read from configuration at runtime"),
        )
    return [], Assessment(check=CHECK, status="clean")
