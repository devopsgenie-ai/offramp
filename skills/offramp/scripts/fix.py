#!/usr/bin/env python3
"""fix: turn the audit's row-level-security findings into one new migration. RFC-0003.

    fix.py <repo> --out <dir> [--version <n>]

Runs `scan` and the audit checks, renders, checks the rendering by round trip, and writes
a staging tree under --out for a human to review, copy and apply:

    supabase/migrations/<version>_offramp_rls.sql   only when something can be fixed
    FIXES.md                                       what each block changes, and why
    fixes.json                                     the same, for machines
    .offramp/manifest.json                         sha256 of each file above

It applies nothing, opens no database connection, needs no credential and makes no
network request. It never edits an existing migration: an applied migration is history,
so every fix is a new file. It writes only under --out, never into the repository.

The version defaults to the repository's latest migration version plus one, so no clock
is read. A project whose database holds migrations the repository lacks passes
--version; it must sort after every migration in the repository.

Exit status: 0 written (or nothing to fix), 1 a conflict with a file offramp did not
write or that was edited since, 2 the tool failed or the round trip refused the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit import audit_repo                                   # noqa: E402
from checks.supabase import (                                  # noqa: E402
    permissive_write_findings, public_read_findings, rls_disabled_findings,
)
from detect.migrations import detect_schema, split_statements  # noqa: E402
from fix_plan import RLS_CHECKS                                # noqa: E402
from fix_render import (                                       # noqa: E402
    MIGRATION_SUFFIX, SCHEMA, Rendering, next_version, render_fix,
)
from scan import scan_repo                                     # noqa: E402
from spec import canonical_json                                # noqa: E402

MANIFEST = ".offramp/manifest.json"


class FixError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Built:
    files: dict[str, str]      # every file of the tree, the manifest included
    rendering: Rendering
    version: str | None


def _sha(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def resolve_version(latest: str | None, requested: str | None,
                    existing: list[str]) -> str | None:
    """The migration version: --version, or latest + 1. Either way it must sort after
    every migration in the repository, by number and by file name, because both the
    replay and Supabase's tooling run migrations in that order."""
    version = requested if requested is not None else next_version(latest)
    if version is None:
        return None
    if not version.isdigit():
        raise FixError(2, f"--version must be digits, like a migration's leading version; "
                          f"got {version!r}")
    name = f"{version}{MIGRATION_SUFFIX}"
    last = max(existing, default=None)
    if latest is not None and (int(version) <= int(latest) or (last and name <= last)):
        raise FixError(2, f"--version {version} must sort after the repository's latest "
                          f"migration ({latest}), or it would run before migrations it fixes")
    return version


def round_trip(root: Path, rendering: Rendering, document: dict) -> list[str]:
    """Replay the migrations plus the rendered file, run the RLS checks, and compare."""
    if rendering.migration is None:
        return []
    sql = rendering.files[rendering.migration]
    problems = []
    counted = len(split_statements(sql))
    if counted != rendering.statements:
        problems.append(f"the migration holds {counted} statements, but the renderer wrote "
                        f"{rendering.statements}: an identifier may have escaped its quotes")
    schema = detect_schema(root, extra=[(rendering.migration, sql)])
    if schema.unreplayable or schema.unordered or schema.tables is None:
        reasons = schema.unreplayable + schema.unordered
        return problems + ["the migrations plus the rendered file could not be replayed: "
                           + "; ".join(reasons or ["no tables"])]
    after = {f.id for f in rls_disabled_findings(schema) + permissive_write_findings(schema)
             + public_read_findings(schema)}
    before = {f["id"] for f in document["findings"] if f["check"] in RLS_CHECKS}
    for outcome in rendering.outcomes:
        if outcome.status == "fixed" and outcome.id in after:
            problems.append(f"{outcome.id} is marked fixed but is still present")
        if outcome.status != "fixed" and outcome.id not in after:
            problems.append(f"{outcome.id} is marked {outcome.status} but is gone")
    expected = set(rendering.expected_after)
    for new in sorted(after - before - expected):
        problems.append(f"{new} is new and was not predicted")
    for missing in sorted(expected - (after - before)):
        problems.append(f"{missing} was predicted but did not appear")
    return problems


def build(root: Path, version: str | None = None, renderer=None) -> Built:
    """Everything but the writing. Pure apart from reading the repository."""
    scan = scan_repo(root)
    document = audit_repo(root)
    schema = scan.schema
    existing = [path.rsplit("/", 1)[-1] for path in (schema.migrations if schema else [])]
    resolved = resolve_version(schema.latest if schema else None, version, existing)
    rendering = (renderer or render_fix)(scan.appspec, document, resolved)
    problems = round_trip(root, rendering, document)
    if problems:
        raise FixError(2, "the rendered migration does not do what it claims:\n  - "
                          + "\n  - ".join(problems))
    files = dict(rendering.files)
    if files:
        files[MANIFEST] = canonical_json({
            "schema": SCHEMA,
            "version": resolved,
            "appspec_sha256": _sha(canonical_json(scan.appspec)),
            "files": {path: _sha(content) for path, content in sorted(files.items())},
        })
    return Built(files=files, rendering=rendering, version=resolved)


def _previous(out: Path) -> tuple[dict[str, str], list[str]]:
    path = out / MANIFEST
    if not path.is_file():
        return {}, []
    try:
        return dict(json.loads(path.read_text(encoding="utf-8"))["files"]), []
    except (ValueError, KeyError, TypeError):
        return {}, [f"{MANIFEST}: not a manifest offramp wrote; refusing to trust it"]


def write_tree(out: Path, files: dict[str, str]) -> list[str]:
    """Write `files` under `out`, or return the conflicts and write nothing.

    RFC-0001: a file whose on-disk hash no longer matches the manifest was edited by a
    person, and a file not in the manifest was never offramp's. Neither is overwritten
    or removed. Files the previous run wrote and this one does not are removed, so a
    changed version leaves no stale migration behind.
    """
    previous, conflicts = _previous(out)
    for path, content in sorted(files.items()):
        target = out / path
        if path == MANIFEST or not target.exists():
            continue
        on_disk = _sha(target.read_bytes())
        if on_disk != _sha(content) and previous.get(path) != on_disk:
            conflicts.append(f"{path}: " + ("edited since offramp wrote it" if path in previous
                                            else "exists, and offramp did not write it"))
    stale = [path for path in sorted(previous) if path not in files]
    for path in stale:
        target = out / path
        if target.exists() and _sha(target.read_bytes()) != previous[path]:
            conflicts.append(f"{path}: edited since offramp wrote it, and this run would "
                             f"remove it")
    if conflicts:
        return conflicts
    for path in stale:
        (out / path).unlink(missing_ok=True)
    for path, content in sorted(files.items()):
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if not files:
        (out / MANIFEST).unlink(missing_ok=True)
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--version", default=None)
    args = parser.parse_args(argv)

    if not args.repo.is_dir():
        print(f"error: {args.repo} does not exist or is not a directory", file=sys.stderr)
        return 2
    if args.out.resolve() == args.repo.resolve():
        print("error: --out must be a staging directory, not the repository: offramp "
              "writes a migration for you to review and copy, never into your "
              "migrations directory", file=sys.stderr)
        return 2
    try:
        built = build(args.repo, args.version, renderer=render_fix)
    except FixError as error:
        print(f"error: refusing to write: {error}", file=sys.stderr)
        return error.code
    except (OSError, ValueError, UnicodeDecodeError) as error:
        print(f"error: fix failed: {error}", file=sys.stderr)
        return 2

    conflicts = write_tree(args.out, built.files)
    if conflicts:
        for conflict in conflicts:
            print(f"conflict: {conflict}", file=sys.stderr)
        print("nothing was written. Move your edits elsewhere, or delete the file, and "
              "run fix again.", file=sys.stderr)
        return 1

    outcomes = built.rendering.outcomes
    if not outcomes:
        print(f"fix {args.repo}: no row-level-security finding to fix; nothing written")
        return 0
    counts = {s: sum(1 for o in outcomes if o.status == s)
              for s in ("fixed", "gap", "not_in_scope")}
    print(f"fix {args.repo} -> {args.out}")
    print(f"  fixed: {counts['fixed']}  needs an answer: {counts['gap']}  "
          f"left for your decision: {counts['not_in_scope']}")
    if built.rendering.migration:
        print(f"  migration: {built.rendering.migration} "
              f"({built.rendering.statements} statements). Review it, then copy it into "
              f"your repository. Nothing has been applied.")
    else:
        print("  no migration: nothing can be fixed without your answer. See FIXES.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
