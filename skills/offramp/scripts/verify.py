#!/usr/bin/env python3
"""verify: re-render and diff against what is on disk. RFC-0001, RFC-0003.

    verify.py <repo> --out <dir>

RFC-0001 requires `verify` to land with the first renderer, and `fix` is the first. It
enforces the invariant the output tree rests on:

    Every byte under the output tree is a pure function of (AppSpec, answers, renderer
    version). A non-empty verify diff is a bug report against a renderer, never
    something to reconcile by editing the output.

It re-renders with the version recorded in `.offramp/manifest.json`, in memory, and
compares every file the manifest names and every file the renderer produces. Run it
right after `fix`, before copying the migration: once the migration is in the
repository, the findings it fixed are gone and a re-render rightly differs.

Exit status: 0 identical, 1 a difference or no manifest, 2 the tool itself failed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fix import MANIFEST, FixError, build   # noqa: E402

_DIFF_LINES = 40


def diff_tree(out: Path, files: dict[str, str], recorded: list[str]) -> list[str]:
    """Human-readable differences between `files` and the tree under `out`."""
    report: list[str] = []
    for path in sorted(set(files) | set(recorded)):
        target = out / path
        actual = target.read_text(encoding="utf-8") if target.is_file() else None
        expected = files.get(path)
        if actual == expected:
            continue
        if expected is None:
            report.append(f"{path}: on disk and in the manifest, but no longer rendered")
            continue
        if actual is None:
            report.append(f"{path}: rendered, but missing on disk")
            continue
        lines = list(difflib.unified_diff(
            expected.splitlines(), actual.splitlines(),
            fromfile=f"rendered/{path}", tofile=f"on-disk/{path}", lineterm=""))
        report.append(f"{path}: differs from what the renderer produces")
        report += lines[:_DIFF_LINES] + (["..."] if len(lines) > _DIFF_LINES else [])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest_path = args.out / MANIFEST
    if not manifest_path.is_file():
        print(f"verify: no {MANIFEST} under {args.out}; there is nothing offramp wrote to "
              f"check")
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recorded = sorted(manifest["files"]) + [MANIFEST]
        built = build(args.repo, manifest["version"])
    except FixError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: verify failed: {error}", file=sys.stderr)
        return 2

    report = diff_tree(args.out, built.files, recorded)
    if report:
        print(f"verify: {args.out} is not what the renderer produces for {args.repo}:")
        print("\n".join(report))
        return 1
    print(f"verify: {args.out} matches the renderer ({len(built.files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
