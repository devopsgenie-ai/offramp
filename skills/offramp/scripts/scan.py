#!/usr/bin/env python3
"""scan: read a repository, emit AppSpec JSON, a gap list, and an evidence sidecar.

    scan.py <repo> --out <dir>

SPIKE revision 2. `scan` in RFC-0001 also merges an answers file; this branch does
not implement answers, so what comes out is the "bare" spec of the RFC's two-number
quality metric.

Outputs, all under --out:

    appspec.json    the IR
    gaps.json       the gap list, for machines
    gaps.md         the same list, for a human -- see #9 below
    evidence.json   file:line:sha256 behind each detected fact -- #11
    scan.json       hashes of the four above, so verify can check them -- #9
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from answers import load as load_answers                      # noqa: E402
from detect import answerable_set, scan_repo                  # noqa: E402
from spec import canonical_json, check_enums, to_jsonable     # noqa: E402

SEVERITY_ORDER = ("blocking", "important", "cosmetic")
SCAN_MANIFEST = "scan.json"


def render_gap_report(appspec, gaps, redundant=()) -> str:
    """The human-readable half of the gap list.

    #9: note where this lives. `scan` writes it, not `render`, because a renderer
    takes the AppSpec and nothing else and gaps are not in the AppSpec. In v1 this
    file is the entire runbook -- RFC-0001 says the gap report is "the only place
    the non-declarative work is described" -- so it being outside the output tree,
    and therefore outside what `verify` protects, is a real hole. It is closed
    here by scan.json, which verify checks.
    """
    counts = {level: sum(1 for gap in gaps if gap.severity == level) for level in SEVERITY_ORDER}
    lines = [
        f"# Gaps — {appspec.name or '(unnamed: see app.name below)'}",
        "",
        "  ".join(f"{level}: {counts[level]}" for level in SEVERITY_ORDER)
        + f"  (total {len(gaps)})",
        "",
        f"{sum(1 for g in gaps if g.kind == 'action')} of these are actions rather than "
        "values: there is nothing to record, you do the thing and confirm it.",
        "",
    ]
    if redundant:
        # #31: not gaps. A detector has caught up with an answer and produces the
        # same value, so nothing is in dispute -- but a reviewer should be able to
        # see that a field changed hands.
        lines += [
            "## answers a detector has caught up with",
            "",
            "Nothing to do. These were answered by hand and a detector now determines them",
            "by itself, producing the same value. They are kept as guards: if a detector",
            "later moves to a different value, that disagreement still becomes a gap. Delete",
            "them from the answers file whenever you like.",
            "",
        ]
        for item in redundant:
            lines.append(f"- `{item['target']}` — {json.dumps(item['value'])}")
        lines.append("")

    for level in SEVERITY_ORDER:
        selected = [gap for gap in gaps if gap.severity == level]
        if not selected:
            continue
        lines += [f"## {level}", ""]
        for gap in selected:
            lines += [f"### {gap.id}", ""]
            lines += gap.question.split("\n")
            lines.append("")
            if gap.depends_on:
                lines.append(
                    "- **answer "
                    + ", ".join(f"`{item}`" for item in gap.depends_on)
                    + " first** — this question may not need an answer at all once it is"
                )
            if gap.kind == "action":
                lines.append("- kind: action — nothing goes in the answers file; do it and say so")
            if gap.proposed is None:
                lines.append("- proposed: none — this one has no sensible default")
            elif gap.proposed == "":
                lines.append('- proposed: `""` (the empty string)')
            elif isinstance(gap.proposed, (dict, list)):
                lines += ["- proposed:", "", "  ```json"]
                lines += [
                    f"  {row}" for row in json.dumps(to_jsonable(gap.proposed), indent=2).splitlines()
                ]
                lines.append("  ```")
            else:
                lines.append(f"- proposed: `{gap.proposed}`")
            lines.append(f"- confidence: {gap.confidence}")
            if gap.evidence:
                lines.append("- evidence: " + ", ".join(f"`{item}`" for item in gap.evidence))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--answers", type=Path, help="answers.json to merge")
    args = parser.parse_args()

    stored = load_answers(args.answers) if args.answers else None
    appspec, gaps, evidence, detected, redundant, skipped = scan_repo(args.repo, stored)
    declared, address_problems = answerable_set(gaps, appspec, stored)
    problems = check_enums(appspec, gaps) + address_problems
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    written = {
        "appspec.json": canonical_json(appspec),
        "gaps.json": canonical_json(gaps),
        "gaps.md": render_gap_report(appspec, gaps, redundant),
        "evidence.json": canonical_json(evidence),
        "answerable.json": canonical_json(declared),
        "detected.json": canonical_json(detected),
    }
    for name, content in sorted(written.items()):
        (args.out / name).write_text(content, encoding="utf-8")
    (args.out / SCAN_MANIFEST).write_text(
        canonical_json(
            {
                "files": {
                    name: hashlib.sha256(content.encode("utf-8")).hexdigest()
                    for name, content in sorted(written.items())
                }
            }
        ),
        encoding="utf-8",
    )

    counts = {level: sum(1 for gap in gaps if gap.severity == level) for level in SEVERITY_ORDER}
    print(f"scanned {args.repo} -> {args.out}")
    for path, reason in skipped:
        print(f"  skipped:    {path}/ — {reason}")
    if not appspec.services:
        print(
            "\n  WARNING: no services were detected, so the output tree will be empty of\n"
            "  anything to deploy. v1 detects one scenario -- a FastAPI backend in a\n"
            "  directory holding server.py and requirements.txt, and a Node frontend in a\n"
            "  directory holding package.json. If this repository is shaped differently,\n"
            "  that is a detector gap and worth reporting, not a result.\n",
            file=sys.stderr,
        )
    print(f"  name:       {appspec.name or '(none — blocking gap)'}")
    print(f"  services:   {', '.join(s.name for s in appspec.services)}")
    print(f"  datastores: {', '.join(d.name for d in appspec.datastores) or 'none'}")
    print("  gaps:       " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    if redundant:
        print(
            f"  answers a detector has caught up with: "
            + ", ".join(item["target"] for item in redundant)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
