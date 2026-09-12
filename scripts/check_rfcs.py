#!/usr/bin/env python3
"""Validate RFC documents and enforce the RFC gate.

Two subcommands:

    validate            check every rfcs/NNNN-*.md against the frontmatter contract
    gate                check that a change touching implementation cites an accepted RFC

The gate reads the changed-file list from --changed (one path per line, "-" for stdin)
and the pull-request body from --body-file. It is intended to run in CI; see
.github/workflows/rfc.yml.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
RFC_DIR = REPO_ROOT / "rfcs"

STATUSES = {"draft", "review", "accepted", "rejected", "implemented", "superseded"}
REQUIRED = ("rfc", "title", "status", "authors", "created", "updated")
OPTIONAL = ("supersedes", "superseded_by", "tracking_issue")

FILENAME_RE = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
RFC_REF_RE = re.compile(r"\bRFC-(\d{4})\b")

# A change to any of these paths requires an accepted RFC.
GATED_PREFIXES = ("src/", "schemas/", "templates/")

# Changes confined to these never require one, even under a gated prefix.
EXEMPT_SUFFIXES = (".md", ".txt")

# Gated with no suffix exemption at all. Everything under skills/ is program text, its
# Markdown included: SKILL.md instructs the model and is not documentation.
GATED_ALWAYS = ("skills/",)


def _split_frontmatter(text: str, path: Path) -> dict:
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: must begin with a YAML frontmatter block")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"{path.name}: frontmatter block is not closed")
    data = yaml.safe_load(text[4:end])
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: frontmatter must be a mapping")
    return data


def _check_date(value: object, field: str, path: Path) -> dt.date:
    if isinstance(value, dt.date):
        return value
    raise ValueError(f"{path.name}: '{field}' must be a YYYY-MM-DD date, got {value!r}")


def validate_one(path: Path) -> list[str]:
    errors: list[str] = []
    match = FILENAME_RE.match(path.name)
    if not match:
        return [f"{path.name}: filename must be NNNN-lowercase-slug.md"]

    try:
        fm = _split_frontmatter(path.read_text(encoding="utf-8"), path)
    except ValueError as exc:
        return [str(exc)]

    unknown = set(fm) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        errors.append(f"{path.name}: unknown frontmatter keys: {', '.join(sorted(unknown))}")

    for field in REQUIRED:
        if fm.get(field) in (None, "", []):
            errors.append(f"{path.name}: missing required field '{field}'")

    number = fm.get("rfc")
    if isinstance(number, int):
        if number != int(match.group(1)):
            errors.append(
                f"{path.name}: 'rfc: {number}' does not match filename number {match.group(1)}"
            )
    elif "rfc" in fm:
        errors.append(f"{path.name}: 'rfc' must be an integer, got {number!r}")

    status = fm.get("status")
    if status not in STATUSES:
        errors.append(
            f"{path.name}: status {status!r} is not one of {', '.join(sorted(STATUSES))}"
        )
    if status == "superseded" and not fm.get("superseded_by"):
        errors.append(f"{path.name}: status 'superseded' requires 'superseded_by'")

    authors = fm.get("authors")
    if authors is not None and (
        not isinstance(authors, list) or not all(isinstance(a, str) and a for a in authors)
    ):
        errors.append(f"{path.name}: 'authors' must be a non-empty list of strings")

    created = updated = None
    for field in ("created", "updated"):
        if field in fm:
            try:
                parsed = _check_date(fm[field], field, path)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                if field == "created":
                    created = parsed
                else:
                    updated = parsed
    if created and updated and updated < created:
        errors.append(f"{path.name}: 'updated' ({updated}) precedes 'created' ({created})")

    title = fm.get("title")
    if isinstance(title, str) and title.endswith("."):
        errors.append(f"{path.name}: 'title' must not end with a period")

    return errors


def cmd_validate() -> int:
    paths = sorted(p for p in RFC_DIR.glob("*.md") if p.name != "README.md")
    if not paths:
        print("no RFCs found", file=sys.stderr)
        return 1

    errors: list[str] = []
    seen: dict[int, str] = {}
    for path in paths:
        errors.extend(validate_one(path))
        match = FILENAME_RE.match(path.name)
        if match:
            number = int(match.group(1))
            if number in seen and number != 0:
                errors.append(f"{path.name}: duplicate RFC number, also used by {seen[number]}")
            seen[number] = path.name

    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    print(f"checked {len(paths)} RFC(s), {len(errors)} error(s)")
    return 1 if errors else 0


def accepted_numbers() -> set[int]:
    numbers = set()
    for path in RFC_DIR.glob("*.md"):
        if path.name == "README.md":
            continue
        try:
            fm = _split_frontmatter(path.read_text(encoding="utf-8"), path)
        except ValueError:
            continue
        if fm.get("status") in {"accepted", "implemented"} and isinstance(fm.get("rfc"), int):
            numbers.add(fm["rfc"])
    return numbers


def cmd_gate(changed_arg: str, body_file: str | None) -> int:
    raw = sys.stdin.read() if changed_arg == "-" else Path(changed_arg).read_text()
    changed = [line.strip() for line in raw.splitlines() if line.strip()]

    gated = [
        path
        for path in changed
        if path.startswith(GATED_ALWAYS)
        or (path.startswith(GATED_PREFIXES) and not path.endswith(EXEMPT_SUFFIXES))
    ]
    if not gated:
        print("no gated paths changed; RFC not required")
        return 0

    body = Path(body_file).read_text(encoding="utf-8") if body_file else ""
    cited = {int(n) for n in RFC_REF_RE.findall(body)}
    if not cited:
        print(
            "error: this change touches implementation:\n  "
            + "\n  ".join(gated)
            + "\n\nCite the authorising RFC in the pull-request body as RFC-NNNN.\n"
            "See rfcs/README.md.",
            file=sys.stderr,
        )
        return 1

    approved = accepted_numbers()
    usable = cited & approved
    if not usable:
        listed = ", ".join(f"RFC-{n:04d}" for n in sorted(cited))
        print(
            f"error: cited {listed}, but no cited RFC has status 'accepted' or "
            "'implemented'.\nImplementation may not begin until the RFC is accepted.",
            file=sys.stderr,
        )
        return 1

    print("authorised by " + ", ".join(f"RFC-{n:04d}" for n in sorted(usable)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate RFC frontmatter")
    gate = sub.add_parser("gate", help="enforce the RFC gate for a pull request")
    gate.add_argument("--changed", required=True, help="file of changed paths, or '-'")
    gate.add_argument("--body-file", help="file containing the pull-request body")

    args = parser.parse_args()
    if args.command == "validate":
        return cmd_validate()
    return cmd_gate(args.changed, args.body_file)


if __name__ == "__main__":
    sys.exit(main())
