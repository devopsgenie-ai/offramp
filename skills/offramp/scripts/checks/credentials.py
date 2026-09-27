"""credential.committed: a working credential is in the repository.

No new detection. The committed-credential detector already raises an `action` gap --
RFC-0001's rotate-before-cutover instruction, a step in a migration. This check states
the same fact as a risk in production today, and links back to the gap rather than
repeating its wording. Gap counts do not change.
"""

from __future__ import annotations

from pathlib import Path

from findings import Assessment, Finding

CHECK = "credential.committed"


def _key_and_file(gap) -> tuple[str, str]:
    path = gap.evidence[0].rsplit(":", 1)[0] if gap.evidence else "(unknown file)"
    return gap.id.rsplit(".", 1)[-1], path


def check_committed_credentials(root: Path, scan) -> tuple[list[Finding], Assessment]:
    findings: list[Finding] = []
    for gap in scan.gaps:
        if not gap.id.startswith("credential."):
            continue
        key, path = _key_and_file(gap)
        subject = "a connection string with an embedded password" if key == "literal" \
            else f"`{key}`"
        findings.append(Finding(
            id=f"{CHECK}.{gap.id.removeprefix('credential.')}",
            check=CHECK,
            category="security",
            severity="critical",
            title=(f"`{key}` is committed to the repository" if key != "literal"
                   else f"A database password is written into `{path}`"),
            detail=(
                f"{subject[0].upper()}{subject[1:]} is committed with a real-looking value "
                f"in `{path}`. Anyone who can read this repository, or any copy or fork "
                f"of it, can use it. Deleting the file does not help: the value stays in "
                f"the git history."
            ),
            remedy=(
                "Rotate the credential at its source, then supply the new value through "
                "the environment rather than a committed file. Treat the old value as "
                "disclosed."
            ),
            evidence=list(gap.evidence),
            related_gaps=[gap.id],
        ))
    return findings, Assessment(check=CHECK, status="found" if findings else "clean")
