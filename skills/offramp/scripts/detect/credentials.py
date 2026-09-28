"""Credentials committed to the repository.

27 of 100 sampled Emergent repositories commit a working .env, because the platform's
own .gitignore has a section headed "Environment files" with no .env pattern under it.
This is the normal case.

The tool does not refuse to run. The user already has the problem, and refusing would
exclude roughly a third of the people this exists to serve. What it must not do is stay
silent: a migration is the one moment when rotating costs least, because the database is
moving anyway.

AGENTS.md §4: the value never leaves this module. Not into the AppSpec, not into a gap,
not into evidence, not into a log line. The gap names the key and the file.
"""

from __future__ import annotations

from pathlib import Path

from detect.env import CREDENTIAL_URL_RE, is_sensitive, parse_env_file
from spec import Evidence, Gap, gap_id
from walk import rel, walk_files

_SOURCE_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".json", ".yml", ".yaml")

_ROTATE = (
    "Rotate this credential before cutover, and treat the current value as disclosed: "
    "it is in the git history, so deleting the file does not remove it. Change it at the "
    "source (the database user's password, the signing key, the provider's token), then "
    "supply the new value to the cluster as a Secret rather than a committed file. Doing "
    "it now costs least -- the datastore is moving anyway, so one cutover covers both."
)


def _identifier(relative: str) -> str:
    """A gap-id fragment from a path: stable, name-based, no slashes.

    Segments are stripped of a leading dot so that `backend/.env` yields
    `backend.env` rather than `backend..env`. The id is the durable key every answer
    and every renderer reference uses, so an empty segment in it is not cosmetic.
    """
    segments = [segment.lstrip(".") for segment in relative.split("/")]
    return ".".join(segment for segment in segments if segment).replace("-", "_")


def detect_committed_credentials(root: Path) -> tuple[list[Gap], list[Evidence]]:
    gaps: list[Gap] = []
    evidence: list[Evidence] = []

    # walk_files prunes node_modules, .venv and the rest, so a dependency's own example
    # .env is never mistaken for the user's committed credential.
    for path in walk_files(root):
        relative = rel(root, path)

        if path.name == ".env" or path.name.startswith(".env."):
            for key, value, line in parse_env_file(path):
                if not value or not is_sensitive(key, value):
                    continue
                gaps.append(Gap(
                    id=gap_id("credential", _identifier(relative), key),
                    kind="action",
                    pointers=[],
                    question=(
                        f"`{key}` is committed with a real-looking value in "
                        f"`{relative}` (line {line}). {_ROTATE} Confirm here once the "
                        f"credential has been rotated. The value is not recorded "
                        f"anywhere in this tool's output."
                    ),
                    proposed=None,
                    confidence="high",
                    severity="blocking",
                    evidence=[f"{relative}:{line}"],
                    evidence_scope=[relative],
                ))
                evidence.append(Evidence(path=relative, line=line))
            continue

        if path.suffix not in _SOURCE_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line_text in enumerate(text.splitlines(), start=1):
            for token in line_text.replace("'", '"').split('"'):
                if not CREDENTIAL_URL_RE.match(token.strip()):
                    continue
                gaps.append(Gap(
                    id=gap_id("credential", _identifier(relative), "literal"),
                    kind="action",
                    pointers=[],
                    question=(
                        f"`{relative}` line {number} contains a connection string with "
                        f"an embedded password, written as a string literal in the "
                        f"source. {_ROTATE} Replace the literal with a value read from "
                        f"the environment, then confirm here."
                    ),
                    proposed=None,
                    confidence="high",
                    severity="blocking",
                    evidence=[f"{relative}:{number}"],
                    evidence_scope=[relative],
                ))
                evidence.append(Evidence(path=relative, line=number))
                break

    # One gap per id, first occurrence wins, sorted for byte-stable output.
    unique: dict[str, Gap] = {}
    for gap in gaps:
        unique.setdefault(gap.id, gap)
    ordered = [unique[key] for key in sorted(unique)]
    evidence.sort(key=lambda item: (item.path, item.line))
    return ordered, evidence
