"""The check registry. RFC-0002, "Checks in the first implementation".

A check is a pure function `(root, scan) -> (findings, assessment)`. It reads the
repository only through `walk.py`, never the network, never the clock, and it never
writes. `audit.py` is the only module in the audit path that writes files.

Every registered check emits exactly one Assessment per run, including the ones that
find nothing, so a clean report can show what it checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from checks.credentials import check_committed_credentials
from checks.frontend import check_secret_in_bundle
from checks.health import check_no_health_endpoint
from checks.platform import check_platform_urls
from checks.supabase import (
    check_rls_disabled, check_rls_permissive_write, check_rls_public_read,
)
from findings import FINDING_SEVERITY_ORDER, Assessment, Finding


@dataclass(frozen=True)
class Check:
    id: str
    category: str
    run: Callable[[Path, object], "tuple[list[Finding], Assessment]"]


#: Sorted by id, so the assessment list does not depend on registration order.
CHECKS: tuple[Check, ...] = tuple(sorted((
    Check("credential.committed", "security", check_committed_credentials),
    Check("frontend.secret_in_bundle", "security", check_secret_in_bundle),
    Check("platform.hardcoded_url", "portability", check_platform_urls),
    Check("service.no_health_endpoint", "operability", check_no_health_endpoint),
    Check("supabase.rls.disabled", "security", check_rls_disabled),
    Check("supabase.rls.permissive_write", "security", check_rls_permissive_write),
    Check("supabase.rls.public_read", "security", check_rls_public_read),
), key=lambda check: check.id))


def run_checks(root: Path, scan) -> tuple[list[Finding], list[Assessment]]:
    findings: list[Finding] = []
    assessments: list[Assessment] = []
    for check in CHECKS:
        found, assessment = check.run(root, scan)
        findings.extend(found)
        assessments.append(assessment)
    unique = {finding.id: finding for finding in findings}
    ordered = sorted(
        unique.values(),
        key=lambda item: (FINDING_SEVERITY_ORDER.index(item.severity), item.id),
    )
    return ordered, assessments


def assessed(check: str, findings: list[Finding]) -> Assessment:
    """The assessment for a check that looked: found if it found anything, else clean."""
    return Assessment(check=check, status="found" if findings else "clean")
