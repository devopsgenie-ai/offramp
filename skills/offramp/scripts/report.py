"""The human-readable gap list.

Until the generated runbook exists -- deferred past v1 -- this file is the only place
the non-declarative work is described. That raises the bar on wording: a gap has to be
enough for someone to act on alone.
"""

from __future__ import annotations

import json

from spec import SEVERITY_ORDER, AppSpec, Gap, to_jsonable


def render_gap_report(appspec: AppSpec, gaps: list[Gap]) -> str:
    counts = {level: sum(1 for gap in gaps if gap.severity == level)
              for level in SEVERITY_ORDER}
    actions = sum(1 for gap in gaps if gap.kind == "action")
    lines = [
        f"# Gaps — {appspec.name or '(unnamed: see app.name below)'}",
        "",
        "  ".join(f"{level}: {counts[level]}" for level in SEVERITY_ORDER)
        + f"  (total {len(gaps)})",
        "",
        f"{actions} of these are actions rather than values: there is nothing to record, "
        "you do the thing and confirm it.",
        "",
    ]

    for level in SEVERITY_ORDER:
        selected = [gap for gap in gaps if gap.severity == level]
        if not selected:
            continue
        lines += [f"## {level}", ""]
        for gap in sorted(selected, key=lambda item: item.id):
            lines += [f"### {gap.id}", "", gap.question, ""]
            if gap.depends_on:
                lines.append(
                    "- **answer "
                    + ", ".join(f"`{item}`" for item in gap.depends_on)
                    + " first** — this question may not need an answer at all once it is"
                )
            if gap.kind == "action":
                lines.append(
                    "- kind: action — nothing goes in the answers file; do it and say so"
                )
            if gap.proposed is None:
                lines.append("- proposed: none — this one has no sensible default")
            elif isinstance(gap.proposed, (dict, list)):
                lines += ["- proposed:", "", "  ```json"]
                lines += [f"  {row}" for row in
                          json.dumps(to_jsonable(gap.proposed), indent=2).splitlines()]
                lines.append("  ```")
            else:
                lines.append(f"- proposed: `{gap.proposed}`")
            lines.append(f"- confidence: {gap.confidence}")
            if gap.evidence:
                lines.append("- evidence: " + ", ".join(f"`{item}`" for item in gap.evidence))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
