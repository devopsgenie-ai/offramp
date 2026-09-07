#!/usr/bin/env python3
"""SPIKE ONLY. Stamp a plan's evidence with the hashes it was written against.

    stamp_plan.py <plan.json> <repo> [--out <plan.json>]

SPIKE.md #34: `apply` must be able to tell whether a cited line still says what the
plan read, so a citation carries the hash the plan saw. A model should not be asked
to compute SHA-256 by hand, and it should not have to: in the finished design this
belongs in `show`, which is the script that renders a plan for review and is
therefore the thing that knows what the human actually read. `show` is not built in
this spike, so this stands in for it.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path


def hash_line(repo: Path, path: str, line: int) -> str | None:
    target = repo / path
    if not target.exists():
        return None
    rows = target.read_text(encoding="utf-8").splitlines()
    if not 0 < line <= len(rows):
        return None
    content = rows[line - 1]
    if Path(path).name.startswith(".env"):
        # Never hash a credential's value, only its key. AGENTS.md §4.
        content = content.split("=", 1)[0]
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    stamped = 0
    for entry in plan.get("entries", []):
        rows = []
        for item in entry.get("evidence", []):
            if isinstance(item, dict):
                rows.append(item)
                continue
            path, _, raw = str(item).rpartition(":")
            if not raw.isdigit():
                print(f"error: evidence {item!r} is not path:line", file=sys.stderr)
                return 1
            digest = hash_line(args.repo, path, int(raw))
            if digest is None:
                print(f"error: evidence {item!r} does not resolve", file=sys.stderr)
                return 1
            rows.append({"path": path, "line": int(raw), "sha256": digest})
            stamped += 1
        if rows:
            entry["evidence"] = rows
    (args.out or args.plan).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"stamped {stamped} citation(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
