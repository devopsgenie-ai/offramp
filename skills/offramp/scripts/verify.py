#!/usr/bin/env python3
"""verify: re-derive everything and diff against what is on disk.

    verify.py <scan-dir> --tree <dir> [--repo <path>]

Exits non-zero on any difference. A non-empty diff is a bug report against a
renderer, never something to reconcile by editing the output (RFC-0001).

Two things are checked, not one:

  the output tree   re-rendered from the AppSpec and compared byte for byte.

  the scan output   #9: the gap report is, in v1, the entire description of the
                    non-declarative work -- RFC-0001 says so -- and it sits outside
                    the output tree, so nothing protected it. With --repo, scan is
                    re-run and its four files are compared too. Without --repo they
                    are checked against scan.json, which at least catches an edit.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from render import MANIFEST_PATH, build_manifest, load_appspec, sha256   # noqa: E402
from renderers import render_tree                                        # noqa: E402


def read_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def report(path: str, expected: str, actual: str) -> None:
    print(f"MODIFIED {path}")
    for line in difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile=f"a/{path} (re-derived)",
        tofile=f"b/{path} (on disk)",
    ):
        print("  " + line.rstrip("\n"))


def check_scan_outputs(scan_dir: Path, repo: Path | None, answers_path: Path | None) -> int:
    problems = 0
    manifest = scan_dir / "scan.json"
    if not manifest.exists():
        print("MISSING  scan.json")
        return 1
    recorded = json.loads(manifest.read_text(encoding="utf-8"))["files"]

    if repo is not None:
        from detect import scan_repo
        from scan import render_gap_report
        from spec import canonical_json

        from answers import load as load_answers

        stored = load_answers(answers_path) if answers_path else None
        appspec, gaps, evidence, detected, redundant, _skipped = scan_repo(repo, stored)
        from detect import answerable_set

        declared, _ = answerable_set(gaps, appspec, stored)
        expected = {
            "appspec.json": canonical_json(appspec),
            "gaps.json": canonical_json(gaps),
            "gaps.md": render_gap_report(appspec, gaps, redundant),
            "evidence.json": canonical_json(evidence),
            "answerable.json": canonical_json(declared),
            "detected.json": canonical_json(detected),
        }
        for name in sorted(expected):
            on_disk = scan_dir / name
            if not on_disk.exists():
                print(f"MISSING  {name}")
                problems += 1
                continue
            actual = on_disk.read_text(encoding="utf-8")
            if actual != expected[name]:
                report(name, expected[name], actual)
                problems += 1
        return problems

    for name in sorted(recorded):
        on_disk = scan_dir / name
        if not on_disk.exists():
            print(f"MISSING  {name}")
            problems += 1
        elif sha256(on_disk.read_text(encoding="utf-8")) != recorded[name]:
            print(f"MODIFIED {name}  (hash differs from scan.json)")
            problems += 1
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan_dir", type=Path, help="directory scan wrote its output to")
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--repo", type=Path, help="re-run scan against this repository")
    parser.add_argument("--answers", type=Path, help="answers.json the tree was rendered with")
    args = parser.parse_args()

    appspec, content_sha = load_appspec(args.scan_dir / "appspec.json")
    with tempfile.TemporaryDirectory() as scratch:
        expected = render_tree(appspec)
        expected[MANIFEST_PATH] = build_manifest(expected, content_sha)
        # Written and read back rather than compared in memory, so that anything
        # the filesystem does to the bytes is caught too.
        for path, content in sorted(expected.items()):
            target = Path(scratch) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        expected = read_tree(Path(scratch))

    actual = read_tree(args.tree)
    tracked = set(expected)
    problems = 0
    for path in sorted(tracked):
        if path not in actual:
            print(f"MISSING  {path}")
            problems += 1
        elif sha256(actual[path]) != sha256(expected[path]):
            report(path, expected[path], actual[path])
            problems += 1

    problems += check_scan_outputs(args.scan_dir, args.repo, args.answers)

    if problems:
        print(f"\nverify FAILED: {problems} artifact(s) differ from what the scripts produce.")
        print("This is a bug report against a renderer. Do not fix it by editing the output.")
        return 1
    scope = f"{len(tracked)} rendered files and 6 scan artifacts"
    print(f"verify OK: {scope} match byte for byte.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
