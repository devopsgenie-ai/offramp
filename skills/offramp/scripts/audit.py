#!/usr/bin/env python3
"""audit: a read-only readiness report on an app-platform repository. RFC-0002.

    audit.py <repo> --out <dir> [--fail-on critical|high|medium|low]

Runs `scan`, evaluates every registered check, and writes two files under --out:

    report.json   findings, assessments and what the audit cannot see, as canonical JSON
    report.md     the same content for a person to read

Exit status: 0 when no finding is at or above --fail-on (default: none, so always 0),
1 when one is, 2 when the tool itself fails. It makes no network request, needs no
credential and writes nothing else.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_report import SCHEMA, not_visible, render_audit_markdown, summarise  # noqa: E402
from checks import run_checks                                                  # noqa: E402
from findings import FINDING_SEVERITY_ORDER, check_finding_enums               # noqa: E402
from scan import scan_repo                                                     # noqa: E402
from spec import canonical_json, to_jsonable                                   # noqa: E402

AUDIT_OUTPUT_NAMES = ("report.json", "report.md")


def audit_repo(root: Path) -> dict:
    """Pure: reads the tree, writes nothing. Returns the report document."""
    scan = scan_repo(root)
    findings, assessments = run_checks(root, scan)
    problems = check_finding_enums(findings, assessments)
    if problems:
        raise ValueError("; ".join(problems))
    finding_rows = to_jsonable(findings)
    assessment_rows = to_jsonable(assessments)
    source = scan.appspec.source
    return {
        "schema": SCHEMA,
        "app": {"name": scan.appspec.name, "platform": source.platform,
                "commit": source.commit},
        "summary": summarise(finding_rows, assessment_rows),
        "findings": finding_rows,
        "assessments": assessment_rows,
        "not_visible": not_visible(scan.appspec, (root / "supabase").is_dir()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fail-on", choices=FINDING_SEVERITY_ORDER, default=None)
    args = parser.parse_args()

    if not args.repo.is_dir():
        print(f"error: {args.repo} does not exist or is not a directory", file=sys.stderr)
        return 2
    try:
        document = audit_repo(args.repo)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"error: audit failed: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(canonical_json(document), encoding="utf-8")
    (args.out / "report.md").write_text(render_audit_markdown(document), encoding="utf-8")

    counts = document["summary"]["findings"]
    print(f"audited {args.repo} -> {args.out}")
    print("  findings: " + "  ".join(f"{level}={n}" for level, n in counts.items()))

    if args.fail_on is None:
        return 0
    threshold = FINDING_SEVERITY_ORDER.index(args.fail_on)
    at_or_above = sum(counts[level] for level in FINDING_SEVERITY_ORDER[:threshold + 1])
    return 1 if at_or_above else 0


if __name__ == "__main__":
    sys.exit(main())
