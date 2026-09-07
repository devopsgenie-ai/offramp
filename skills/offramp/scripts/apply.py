#!/usr/bin/env python3
"""apply: fold accepted plan entries into the answers file.

    apply.py <plan.json> --scan <scan-dir> --repo <path> --now <iso8601>
             [--answers <in>] --out <answers.json>
             [--accept-resolve ID ...] [--accept-all-resolves]

`apply(appspec, plan, answers, accepted_resolves, accepted_overrides, now)` is a
pure function; this file is the edge that reads and writes. The clock is an
argument, never read inside -- AGENTS.md §3.

SPIKE revision 2. v1 of RFC-0001 neither emits nor accepts `kind: override`, and
neither does this.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from answers import (                                                     # noqa: E402
    ANSWERS_VERSION,
    Answer,
    AnswerEvidence,
    AnswersFile,
    hash_line,
    pointer_get,
)
from answers import load as load_answers                                  # noqa: E402
from detect import in_scope                                               # noqa: E402
from render import load_appspec                                           # noqa: E402
from spec import canonical_json, to_jsonable                              # noqa: E402

DETECTORS_VERSION = "spike-2"


def apply_plan(
    appspec,
    content_sha: str,
    plan: dict,
    declared: dict,
    detected: dict,
    stored: AnswersFile,
    accepted_resolves: set[str],
    accepted_overrides: set[str],
    now: str,
    repo: Path,
    accepted_by: str = "human",
) -> tuple[AnswersFile, list[str]]:
    """-> (answers', rejections). Pure: no clock, no filesystem writes."""
    rejections: list[str] = []

    basis = plan.get("basis", {})
    if basis.get("content_sha256") != content_sha:
        # RFC-0001: no override flag. Re-scan and re-plan. A plan proposing
        # /services/0 while a teammate adds a worker would otherwise write the
        # API's probe path onto the worker and type-check cleanly.
        return stored, [
            "basis does not match this scan: the AppSpec has changed since the plan "
            f"was made (plan {basis.get('content_sha256')!r}, scan {content_sha!r}). "
            "Re-scan, re-plan; there is no flag for this."
        ]
    if basis.get("detectors_version") != DETECTORS_VERSION:
        return stored, [
            f"plan was made against detectors {basis.get('detectors_version')!r}, "
            f"this build is {DETECTORS_VERSION!r}"
        ]

    seen: set[str] = set()
    accepted: list[Answer] = []
    for index, entry in enumerate(plan.get("entries", [])):
        target = entry.get("target")
        label = f"entries[{index}] {target}"

        if entry.get("kind") == "override":
            if target in accepted_resolves:
                rejections.append(f"{label}: is an override but was accepted as a resolve")
            else:
                rejections.append(f"{label}: kind 'override' is not accepted in v1")
            continue
        if entry.get("kind") != "resolve":
            rejections.append(f"{label}: unknown kind {entry.get('kind')!r}")
            continue
        if target in accepted_overrides:
            rejections.append(f"{label}: is a resolve but was accepted as an override")
            continue
        if target not in accepted_resolves:
            continue                                   # simply not accepted; not an error
        if target in seen:
            rejections.append(f"{label}: a second entry targets the same id")
            continue
        seen.add(target)

        declaration = declared.get(target)
        if declaration is None:
            rejections.append(
                f"{label}: target is outside the declared answerable set "
                "(nothing in this scan asks that question)"
            )
            continue

        pointers = declaration["pointers"]
        for pointer in pointers:
            if pointer.startswith("/source") or pointer.endswith("/name") and "/services/" in pointer:
                rejections.append(f"{label}: {pointer} is never answerable, by invariant")
                break
        else:
            live = pointer_get(appspec, pointers[0]) if pointers else None
            # #28: the AppSpec is a tree of dataclasses and a plan is JSON, so the
            # precondition has to be compared in one representation or a structured
            # value can never pass. RFC-0001 specifies compare-and-swap without
            # saying what the comparison is over.
            if declaration["kind"] == "action":
                # Nothing is being replaced, so there is nothing to swap on.
                if entry.get("proposed") is not None:
                    rejections.append(
                        f"{label}: is an action; it is acknowledged, not given a value"
                    )
                    continue
            elif to_jsonable(entry.get("current", None)) != to_jsonable(live):
                # Compare-and-swap. The plan was made against a value; if the value
                # moved under it, the proposal may no longer make sense.
                rejections.append(
                    f"{label}: compare-and-swap failed -- plan saw {entry.get('current')!r}, "
                    f"the AppSpec now holds {live!r}"
                )
                continue
            if _targets_secret(appspec, pointers):
                # AGENTS.md §4 and RFC-0001. Keyed on EnvVar.sensitive, not on
                # EnvVar.source -- see SPIKE.md #4. Under the RFC's single enum
                # MONGO_URL is `source: datastore` and this rule never fires for
                # the one value it most needs to protect.
                rejections.append(
                    f"{label}: refuses to answer a sensitive value. The answers file is "
                    "committed to your repository; an answer may say where a secret comes "
                    "from, never what it is."
                )
                continue

            evidence, context, bad = _resolve_evidence(
                repo, entry.get("evidence", []), declaration.get("evidence_scope", [])
            )
            if bad:
                rejections.append(f"{label}: {bad}")
                continue

            accepted.append(
                Answer(
                    target=target,
                    kind=declaration["kind"],
                    value=None if declaration["kind"] == "action" else entry.get("proposed"),
                    # #33: what the *detector* produced, not the live value the
                    # human compared against. They differ the moment an answer is
                    # revised, and only this one keeps staleness honest.
                    detected=detected.get(target),
                    evidence=evidence,
                    context=context,
                    detectors_version=DETECTORS_VERSION,
                    answered_at=now,
                    accepted_by=accepted_by,
                )
            )

    merged = {answer.target: answer for answer in stored.answers}
    for answer in accepted:
        merged[answer.target] = answer
    return (
        AnswersFile(
            version=ANSWERS_VERSION,
            answers=sorted(merged.values(), key=lambda a: a.target),
        ),
        rejections,
    )


def _targets_secret(appspec, pointers: list[str]) -> bool:
    for pointer in pointers:
        parts = pointer.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "services" and parts[2] == "env":
            var = appspec.services[int(parts[1])].env[int(parts[3])]
            if var.sensitive:
                return True
    return False


def _resolve_evidence(
    repo: Path, cited: list, scope: list[str]
) -> tuple[list[AnswerEvidence], list[str], str | None]:
    """-> (load-bearing evidence, context citations, rejection).

    #34, two halves.

    *Matching.* RFC-0001 says evidence is "checked by apply to exist and to match"
    and never says match what, so it matched nothing: apply re-hashed whatever was
    on disk at apply time and stored that. A cited line could change between the
    plan being reviewed and the plan being applied, and the answer would record
    evidence no human ever saw. The basis check does not cover it -- a dependency
    bump moves no AppSpec field, so `content_sha256` is unchanged and the basis
    passes, correctly. The basis guards the AppSpec; nothing guarded the files.
    A citation now carries the hash the plan saw, and a mismatch is a rejection.

    *Scope.* A citation outside the gap's scope is kept as context rather than
    refused. A model reasoning across the repository is the thing it is better at
    than the detectors -- the plan that names this app cites the backend routes and
    the frontend component, which is good work -- and a scope rule tight enough to
    catch real padding would reject that too. So padding is made harmless instead of
    forbidden: only in-scope citations are hashed into the answer, so only they can
    ever re-ask the question.
    """
    resolved: list[AnswerEvidence] = []
    context: list[str] = []
    for item in sorted(cited, key=lambda c: c if isinstance(c, str) else c.get("path", "")):
        if isinstance(item, str):
            path, _, raw = item.rpartition(":")
            claimed = None
        elif isinstance(item, dict):
            path, raw, claimed = item.get("path", ""), str(item.get("line", "")), item.get("sha256")
        else:
            return [], [], f"evidence {item!r} is not path:line or {{path, line, sha256}}"
        if not path or not raw.isdigit():
            return [], [], f"evidence {item!r} is not path:line"
        line = int(raw)
        digest = hash_line(repo, path, line)
        if digest is None:
            return [], [], f"evidence {path}:{line} does not resolve to a real file and line"
        if not in_scope(path, scope):
            context.append(f"{path}:{line}")
            continue
        if claimed is None:
            return (
                [],
                [],
                f"evidence {path}:{line} is load-bearing for this question and carries no "
                "hash, so apply cannot tell whether it still says what the plan read. Stamp "
                "the plan (scripts/stamp_plan.py) before applying it",
            )
        if claimed != digest:
            return (
                [],
                [],
                f"evidence {path}:{line} has changed since the plan was written. Re-read it, "
                "re-plan; the value proposed here was reasoned from something else",
            )
        resolved.append(AnswerEvidence(path=path, line=line, sha256=digest))
    return resolved, context, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--scan", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--now", required=True, help="ISO-8601; the clock is an argument")
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--accept-resolve", action="append", default=[])
    parser.add_argument("--accept-override", action="append", default=[])
    parser.add_argument("--accept-all-resolves", action="store_true")
    parser.add_argument(
        "--accepted-by",
        choices=("human", "ci"),
        default="human",
        help="who accepted these entries. #26: CI answers per-build values such as "
        "delivery.image_tag and re-renders, which is what keeps verify honest; the "
        "marker keeps a reviewer able to tell those apart from a human decision.",
    )
    args = parser.parse_args()

    appspec, content_sha = load_appspec(args.scan / "appspec.json")
    declared = json.loads((args.scan / "answerable.json").read_text(encoding="utf-8"))
    detected = json.loads((args.scan / "detected.json").read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    stored = (
        load_answers(args.answers)
        if args.answers and args.answers.exists()
        else AnswersFile(version=ANSWERS_VERSION, answers=[])
    )
    accepted = set(args.accept_resolve)
    if args.accept_all_resolves:
        accepted |= {
            entry["target"] for entry in plan.get("entries", []) if entry.get("kind") == "resolve"
        }

    result, rejections = apply_plan(
        appspec,
        content_sha,
        plan,
        declared,
        detected,
        stored,
        accepted,
        set(args.accept_override),
        args.now,
        args.repo,
        args.accepted_by,
    )
    for rejection in rejections:
        print(f"rejected: {rejection}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(canonical_json(result), encoding="utf-8")
    print(f"answers: {len(result.answers)} stored -> {args.out}")
    return 1 if rejections else 0


if __name__ == "__main__":
    sys.exit(main())
