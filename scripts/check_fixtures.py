#!/usr/bin/env python3
"""Check fixtures against their ground truth, and guard the assertions themselves.

    truth                      every fixture's detected facts must match truth.yaml
    guard --base REF [--body-file F]
                               report what this change does to the assertions CI
                               gates on, and fail unless the body acknowledges it

SPIKE.md #23. RFC-0001 has CI gate on three series -- golden trees, agreement with
`truth.yaml`, and bare gap count by severity -- and every one of them compares the
code against a file under `fixtures/`, which the RFC gate does not cover. Two of
those files are assertions rather than outputs, so lowering them makes a regression
green and nothing notices. That is the failure the RFC's own Problem section names:
a golden test locking in a wrong answer and defending it against correction.

The fix here is deliberately not "gate fixtures/". Requiring an accepted RFC to add
a fixture, or to correct a typo in ground truth, is out of proportion and teaches
people to write throwaway RFCs -- which devalues the gate it is meant to protect.
Instead this makes weakening an assertion *loud*: it costs one sentence in the pull
request, which is the same shape RFC-0001 already chose for gap counts ("an increase
in blocking gaps fails CI unless the authorising RFC says why").

There is also no checked-in gap-count baseline. A number a human can edit is a
number a human can lower; the baseline is computed from the base commit instead.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "fixtures"
SCRIPTS = REPO_ROOT / "skills" / "offramp" / "scripts"
ACK_RE = re.compile(r"^Assertion-change:\s*\S", re.MULTILINE)


def fixture_dirs(root: Path) -> list[Path]:
    base = root / "fixtures"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "truth.yaml").exists())


def bare_scan(scripts: Path, fixture: Path):
    sys.path.insert(0, str(scripts))
    for module in ("detect", "spec", "answers", "render", "renderers", "scan"):
        sys.modules.pop(module, None)
    from detect import scan_repo  # noqa: E402

    appspec, gaps, _evidence, _detected, _redundant, _skipped = scan_repo(fixture)
    sys.path.remove(str(scripts))
    return appspec, gaps


# ---------------------------------------------------------------------------
# truth
# ---------------------------------------------------------------------------

def compare(truth: dict, appspec, gaps) -> list[str]:
    problems: list[str] = []

    def check(label, expected, actual):
        if expected != actual:
            problems.append(f"{label}: truth says {expected!r}, detected {actual!r}")

    check("platform", truth["platform"], appspec.source.platform)

    by_name = {service.name: service for service in appspec.services}
    check("services", sorted(truth["services"]), sorted(by_name))
    for name, expected in sorted(truth.get("services", {}).items()):
        service = by_name.get(name)
        if service is None:
            continue
        check(f"{name}.role", expected["role"], service.role)
        check(f"{name}.language", expected["language"], service.runtime.language)
        check(f"{name}.ports", expected.get("ports", []), service.ports)
        if "start_cmd" in expected:
            check(f"{name}.start_cmd", expected["start_cmd"], service.build.start_cmd)
        if "build_output" in expected:
            check(f"{name}.build_output", expected["build_output"], service.build.output_dir)
        probes = {probe.role: probe.path for probe in service.probes}
        check(f"{name}.probes", expected.get("probes", {}), probes)
        env = {var.name: var for var in service.env}
        check(f"{name}.env keys", sorted(expected.get("env", {})), sorted(env))
        for key, spec in sorted(expected.get("env", {}).items()):
            var = env.get(key)
            if var is None:
                continue
            check(f"{name}.env.{key}.source", spec["source"], var.source)
            check(f"{name}.env.{key}.binding", spec["binding"], var.binding)
            check(f"{name}.env.{key}.sensitive", spec["sensitive"], var.sensitive)
            if "value" in spec:
                check(f"{name}.env.{key}.value", spec["value"], var.value)

    stores = {store.name: store for store in appspec.datastores}
    check("datastores", sorted(truth.get("datastores", {})), sorted(stores))
    for name, expected in sorted(truth.get("datastores", {}).items()):
        store = stores.get(name)
        if store is None:
            continue
        check(f"datastore.{name}.kind", expected["kind"], store.kind)
        check(f"datastore.{name}.consumed_by", expected["consumed_by"], store.consumed_by)
        check(f"datastore.{name}.env_keys", expected["env_keys"], store.env_keys)

    check(
        "routes",
        sorted((r["path"], r["service"]) for r in truth.get("routes", [])),
        sorted((route.path, route.service) for route in appspec.routes),
    )

    # Every committed credential must surface as a blocking gap naming key and file.
    for entry in truth.get("committed_credentials", []):
        hit = [
            gap
            for gap in gaps
            if gap.id.startswith("secret.")
            and entry["key"] in gap.question
            and entry["path"] in gap.question
        ]
        if not hit:
            problems.append(
                f"committed credential {entry['path']}:{entry['key']} was not reported"
            )
        elif hit[0].severity != "blocking":
            problems.append(
                f"committed credential {entry['path']}:{entry['key']} reported as "
                f"{hit[0].severity}, must be blocking"
            )
    return problems


def cmd_truth() -> int:
    fixtures = fixture_dirs(REPO_ROOT)
    if not fixtures:
        print("no fixtures carry a truth.yaml", file=sys.stderr)
        return 1
    failures = 0
    for fixture in fixtures:
        truth = yaml.safe_load((fixture / "truth.yaml").read_text(encoding="utf-8"))
        appspec, gaps = bare_scan(SCRIPTS, fixture)
        problems = compare(truth, appspec, gaps)
        counts = {
            level: sum(1 for gap in gaps if gap.severity == level)
            for level in ("blocking", "important", "cosmetic")
        }
        status = "OK" if not problems else f"{len(problems)} MISMATCH"
        print(
            f"{fixture.name}: {status}  "
            + "  ".join(f"{k}={v}" for k, v in counts.items())
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        failures += len(problems)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------

def base_counts(base: str) -> dict[str, dict[str, int]] | None:
    """Bare gap counts at the base commit, measured with that commit's own scripts.

    Each side is measured with its own detectors, because the series RFC-0001 wants
    is "did the tool get better", not "did the fixtures move under a fixed tool".
    Returns None when the base has nothing to measure.
    """
    with tempfile.TemporaryDirectory() as scratch:
        worktree = Path(scratch) / "base"
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(worktree), base],
                cwd=REPO_ROOT, check=True, capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            print(f"note: cannot read base {base}: {exc.stderr.decode().strip()}")
            return None
        try:
            scripts = worktree / "skills" / "offramp" / "scripts"
            fixtures = fixture_dirs(worktree)
            if not scripts.is_dir() or not fixtures:
                return None
            result = {}
            for fixture in fixtures:
                _appspec, gaps = bare_scan(scripts, fixture)
                result[fixture.name] = {
                    level: sum(1 for gap in gaps if gap.severity == level)
                    for level in ("blocking", "important", "cosmetic")
                }
            return result
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=REPO_ROOT, capture_output=True,
            )


def read_at(base: str, path: str) -> str | None:
    done = subprocess.run(
        ["git", "show", f"{base}:{path}"], cwd=REPO_ROOT, capture_output=True
    )
    return done.stdout.decode("utf-8") if done.returncode == 0 else None


def cmd_guard(base: str, body_file: str | None) -> int:
    notes: list[str] = []

    for fixture in fixture_dirs(REPO_ROOT):
        relative = f"fixtures/{fixture.name}/truth.yaml"
        before = read_at(base, relative)
        after = (fixture / "truth.yaml").read_text(encoding="utf-8")
        if before is None:
            print(f"{relative}: new ground truth, no baseline to compare")
            continue
        if before == after:
            continue
        old, new = yaml.safe_load(before) or {}, yaml.safe_load(after) or {}
        changed = sorted(
            key for key in set(old) | set(new) if old.get(key) != new.get(key)
        )
        notes.append(
            f"{relative}: ground truth changed under {', '.join(changed) or 'formatting'}"
        )

    baseline = base_counts(base)
    current = {}
    for fixture in fixture_dirs(REPO_ROOT):
        _appspec, gaps = bare_scan(SCRIPTS, fixture)
        current[fixture.name] = {
            level: sum(1 for gap in gaps if gap.severity == level)
            for level in ("blocking", "important", "cosmetic")
        }
    if baseline is None:
        print(f"no gap-count baseline at {base}; nothing to compare")
    else:
        for name, counts in sorted(current.items()):
            was = baseline.get(name)
            if was is None:
                print(f"{name}: new fixture, bare gaps {counts}")
                continue
            print(f"{name}: bare gaps {was} -> {counts}")
            if counts["blocking"] > was["blocking"]:
                notes.append(
                    f"{name}: blocking gaps rose {was['blocking']} -> {counts['blocking']}"
                )

    if not notes:
        print("no assertion was weakened")
        return 0

    body = Path(body_file).read_text(encoding="utf-8") if body_file else ""
    print("\nthis change moves what CI compares against:")
    for note in notes:
        print(f"  {note}")
    if ACK_RE.search(body):
        print("\nacknowledged in the pull-request body")
        return 0
    print(
        "\nerror: say why, in the pull-request body, on a line beginning\n"
        "  Assertion-change: <reason>\n"
        "Lowering the bar is sometimes right. It should never be silent.",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("truth", help="assert detected facts against truth.yaml")
    guard = sub.add_parser("guard", help="report changes to the assertions CI gates on")
    guard.add_argument("--base", required=True)
    guard.add_argument("--body-file")
    args = parser.parse_args()
    return cmd_truth() if args.command == "truth" else cmd_guard(args.base, args.body_file)


if __name__ == "__main__":
    sys.exit(main())
