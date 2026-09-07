"""The answers file, and merging it into a scan.

SPIKE revision 2. RFC-0001 specifies this; revision 1 skipped it, so every claim
the spike made about staleness and orphaning was read off the document rather than
run. This implements enough of it to test those claims.

Two deviations from the RFC, both noted in SPIKE.md:

  The file is JSON, not YAML. It is committed to the user's repository and the
  tool rewrites it, so it has to be byte-stable across versions; YAML has many
  valid serialisations of the same data and JSON has one obvious one.

  `Answer.kind` distinguishes a value from an acknowledged action (#20). RFC-0001's
  Answer can only record a value at a target, so the four gaps that are
  instructions -- rotate this credential, commit a lock file -- could never be
  resolved and would reappear on every run forever.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spec import Gap, gap_id, to_jsonable

ANSWERS_VERSION = 1


@dataclass
class AnswerEvidence:
    path: str
    line: int
    sha256: str


@dataclass
class Answer:
    target: str                    # a Gap.id -- the stable id, never a pointer
    kind: str                      # value | action
    value: Any                     # null for an acknowledged action
    detected: Any                  # what the detector produced when this was answered
    # #34: only in-scope citations. These are hashed, and moving one re-asks.
    evidence: list[AnswerEvidence]
    # #34: everything else the plan cited. Shown to a reviewer, never hashed, so a
    # padded citation costs the user nothing instead of becoming a permanent
    # re-ask trigger for a question it has nothing to do with.
    context: list[str]
    detectors_version: str
    answered_at: str               # the clock is an argument at the edge, never read inside
    # #26: who accepted this. RFC-0001 calls the answers file "the accumulated,
    # human-accepted result of applied plans", and that is too narrow: a per-build
    # value like the image tag has to reach the output tree somehow, and the only
    # route that keeps verify's invariant intact is for CI to answer it and
    # re-render. So a machine may write answers -- and a reviewer must be able to
    # see at a glance which answers a person actually decided.
    accepted_by: str = "human"     # human | ci


@dataclass
class AnswersFile:
    version: int
    answers: list[Answer]


@dataclass
class MergeResult:
    applied: set[str] = field(default_factory=set)
    gaps: list[Gap] = field(default_factory=list)
    # #31: answers a detector has caught up with. Not gaps -- there is nothing in
    # dispute -- but reported, so a reviewer can see a detector has taken a field
    # over and can retire the answer when they feel like it.
    redundant: list[dict] = field(default_factory=list)


def load(path: Path) -> AnswersFile:
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != ANSWERS_VERSION:
        # RFC-0001: scan hard-fails on a version it does not recognise rather than
        # attempting a migration. This file outlives every version of the tool.
        raise SystemExit(
            f"{path}: answers file version {data.get('version')!r} is not supported "
            f"(this build understands version {ANSWERS_VERSION}). Do not hand-edit the "
            "version; re-run apply with a build that matches."
        )
    return AnswersFile(
        version=data["version"],
        answers=[
            Answer(
                target=item["target"],
                kind=item["kind"],
                value=item.get("value"),
                detected=item.get("detected"),
                evidence=[AnswerEvidence(**e) for e in item.get("evidence", [])],
                context=list(item.get("context", [])),
                detectors_version=item["detectors_version"],
                answered_at=item["answered_at"],
                # Absent means human: adding an optional field with a safe default
                # is not a breaking change, so the format version does not move.
                accepted_by=item.get("accepted_by", "human"),
            )
            for item in data["answers"]
        ],
    )


# ---------------------------------------------------------------------------
# JSON pointers into the dataclass tree
# ---------------------------------------------------------------------------

def pointer_get(root: Any, pointer: str) -> Any:
    current = root
    for token in pointer.strip("/").split("/"):
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            current = getattr(current, token)
    return current


def pointer_set(root: Any, pointer: str, value: Any) -> None:
    tokens = pointer.strip("/").split("/")
    current = root
    for token in tokens[:-1]:
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            current = getattr(current, token)
    last = tokens[-1]
    if isinstance(current, list):
        current[int(last)] = value
    elif isinstance(current, dict):
        current[last] = value
    else:
        setattr(current, last, value)


def hash_line(root: Path, path: str, line: int) -> str | None:
    target = root / path
    if not target.exists():
        return None
    lines = target.read_text(encoding="utf-8").splitlines()
    if not 0 < line <= len(lines):
        return None
    content = lines[line - 1]
    if Path(path).name.startswith(".env"):
        content = content.split("=", 1)[0]
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The merge rule
# ---------------------------------------------------------------------------

def merge(
    root: Path,
    appspec,
    bare_gaps: list[Gap],
    answers: AnswersFile | None,
    resolve_pointers=None,
    subject_exists=None,
) -> MergeResult:
    """Apply answers to the AppSpec.

    RFC-0001's rule, implemented literally: a live detector value that disagrees
    with a stored answer never wins silently, in either direction -- it becomes a
    gap. An answer merges only when the detector still produces what `detected`
    recorded, and every cited evidence range still hashes to what was recorded.
    """
    result = MergeResult()
    if answers is None:
        return result

    by_id = {gap.id: gap for gap in bare_gaps}
    for answer in sorted(answers.answers, key=lambda a: a.target):
        gap = by_id.get(answer.target)

        # #30: resolve the target against the AppSpec, not against the gap list. A
        # gap that has disappeared may mean the answer is orphaned -- or it may mean
        # a detector graduated and now produces the value, which is the improvement
        # the whole design is for. Only the AppSpec can tell the two apart.
        pointers = gap.pointers if gap else (
            resolve_pointers(answer.target, appspec) if resolve_pointers else []
        )
        alive = subject_exists(answer.target, appspec) if subject_exists else gap is not None

        # Orphan: the thing this answer was about is gone. It passes the staleness
        # test rather than failing it -- detected is null, the detector still
        # produces nothing, and unmoved evidence still hashes -- so without its own
        # origin it is silently dropped. A human accepted it, and its
        # disappearance is information.
        if not alive:
            result.gaps.append(
                Gap(
                    id=gap_id("orphaned", answer.target),
                    kind="value",
                    pointers=[],
                    question=(
                        f"An answer is stored for '{answer.target}', but nothing in this "
                        "run's scan asks that question any more -- the service or field it "
                        f"referred to is gone. The stored value was {answer.value!r}. "
                        "Confirm it is no longer needed, or find out what renamed it."
                    ),
                    proposed=answer.value,
                    confidence="low",
                    severity="important",
                    evidence=[f"{e.path}:{e.line}" for e in answer.evidence],
                    origin="orphaned_answer",
                )
            )
            continue

        stale_reason = None
        live = None
        if pointers:
            live = pointer_get(appspec, pointers[0])
            # #28: `detected` was a live AppSpec value when apply recorded it and is
            # a plain JSON value when it comes back off disk. Comparing the two
            # representations directly marks every structured answer stale on the
            # very next scan -- which looks exactly like the detector disagreeing,
            # and is the failure the staleness rule exists to report. RFC-0001
            # specifies the rule and never says what the comparison is over.
            if to_jsonable(live) != to_jsonable(answer.detected):
                stale_reason = (
                    f"the detector now produces {to_jsonable(live)!r}, but this answer was "
                    f"given when it produced {to_jsonable(answer.detected)!r}"
                )
        if stale_reason is None:
            for item in answer.evidence:
                current = hash_line(root, item.path, item.line)
                if current != item.sha256:
                    stale_reason = f"{item.path}:{item.line} has changed since this was answered"
                    break

        # #31: a detector that now produces exactly the answered value has not
        # disagreed with anything. RFC-0001's rule re-asks anyway, because `detected`
        # moved -- which means a detector *improving* raises the answered gap count
        # and sends every user who answered that question a confirm-what-you-already-
        # said gap. That is the same metric inversion the RFC rejects "answers win
        # over detection" for, with the sign flipped, and it taxes detector
        # graduation, which is the payoff the whole gap-tracking exercise is for.
        #
        # So agreement wins over both staleness reasons: when the detector produces
        # the answered value, the detector *is* the evidence, and the citation the
        # human made no longer has to hold. Action answers are excluded -- they carry
        # no value and no pointer, so "agreement" would be a null comparing equal to
        # a null.
        agreed = (
            stale_reason is not None
            and answer.kind == "value"
            and bool(pointers)
            and to_jsonable(live) == to_jsonable(answer.value)
        )
        if agreed:
            result.redundant.append(
                {
                    "target": answer.target,
                    "value": to_jsonable(answer.value),
                    "note": (
                        "a detector now determines this by itself and produces the same "
                        "value. The answer is kept as a guard: if the detector later moves "
                        "to something else, that disagreement still becomes a gap."
                    ),
                }
            )
            result.applied.add(answer.target)
            continue

        if stale_reason:
            question = (
                gap.question
                if gap
                else (
                    f"A detector now determines '{answer.target}' by itself, and what it "
                    "produces is not what was answered here."
                )
            )
            result.gaps.append(
                Gap(
                    id=answer.target,
                    kind=gap.kind if gap else "value",
                    pointers=pointers,
                    question=(
                        f"{question}\n\nA stored answer of {answer.value!r} was not used: "
                        f"{stale_reason}. Confirm the stored answer, or replace it."
                    ),
                    proposed=answer.value,
                    confidence="low",
                    severity=gap.severity if gap else "important",
                    evidence=gap.evidence if gap else [],
                    origin="stale_answer",
                    depends_on=gap.depends_on if gap else [],
                )
            )
            continue

        if answer.kind == "value":
            for pointer in pointers:
                pointer_set(appspec, pointer, answer.value)
        result.applied.add(answer.target)
    return result
