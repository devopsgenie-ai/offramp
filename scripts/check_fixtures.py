#!/usr/bin/env python3
"""Assert every fixture's detected facts against its hand-written ground truth.

Three checks, because each one alone rewards the wrong behaviour:

  ground truth   a detector that guesses improves the gap count and the golden tree
                 records whatever it produced, so only a hand-written expectation can
                 object.
  gap count      a detector that emits a gap for everything satisfies ground truth
                 trivially, so the bare count has to fall over time.
  assertion      a failing ground-truth check can be silenced by deleting the
  guard          assertion, so absence of an assertion is itself a failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills/offramp/scripts"))

from scan import scan_repo  # noqa: E402
from spec import SEVERITY_ORDER  # noqa: E402

FIXTURES_DIR = Path("fixtures")


def gap_counts(gaps) -> dict[str, int]:
    counts = {level: sum(1 for gap in gaps if gap.severity == level)
              for level in SEVERITY_ORDER}
    counts["total"] = len(gaps)
    return counts


def assertion_guard(truth: dict, appspec) -> list[str]:
    """Every service the detectors found must be asserted about."""
    problems: list[str] = []
    asserted = {item.get("name") for item in truth.get("services") or []}
    for service in appspec.services:
        if service.name not in asserted:
            problems.append(
                f"truth.yaml asserts nothing about service `{service.name}`, which the "
                f"detectors found. An unasserted field is an untested one."
            )
    if "app" not in truth or "name" not in (truth.get("app") or {}):
        problems.append("truth.yaml does not assert app.name")
    return problems


def check_fixture(directory: Path) -> list[str]:
    truth = yaml.safe_load((directory / "truth.yaml").read_text(encoding="utf-8"))
    result = scan_repo(directory)
    appspec = result.appspec
    problems = assertion_guard(truth, appspec)

    if appspec.name != truth["app"]["name"]:
        problems.append(f"app.name: detected {appspec.name!r}, truth {truth['app']['name']!r}")
    if appspec.source.platform != truth["app"]["platform"]:
        problems.append(
            f"app.platform: detected {appspec.source.platform!r}, "
            f"truth {truth['app']['platform']!r}"
        )

    detected_services = {service.name: service for service in appspec.services}
    for expected in truth.get("services") or []:
        name = expected["name"]
        service = detected_services.get(name)
        if service is None:
            problems.append(f"service {name}: in truth.yaml, not detected")
            continue
        for field, actual in (("role", service.role),
                              ("language", service.runtime.language),
                              ("install_cmd", service.build.install_cmd),
                              ("dockerfile", service.build.dockerfile),
                              ("port", service.ports[0] if service.ports else None)):
            if field in expected and expected[field] != actual:
                problems.append(
                    f"service {name}.{field}: detected {actual!r}, truth {expected[field]!r}"
                )
        if "routes" in expected:
            actual_routes = sorted(r.path for r in appspec.routes if r.service == name)
            if actual_routes != sorted(expected["routes"]):
                problems.append(
                    f"service {name}.routes: detected {actual_routes}, "
                    f"truth {sorted(expected['routes'])}"
                )
        if "env" in expected:
            by_name = {var.name: var for var in service.env}
            for var_truth in expected["env"]:
                var = by_name.get(var_truth["name"])
                if var is None:
                    problems.append(f"service {name}: env {var_truth['name']} not detected")
                    continue
                for key in ("binding", "sensitive"):
                    if key in var_truth and getattr(var, key) != var_truth[key]:
                        problems.append(
                            f"service {name}.env.{var.name}.{key}: "
                            f"detected {getattr(var, key)!r}, truth {var_truth[key]!r}"
                        )
        if "probes" in expected:
            actual_probes = sorted((p.role, p.path) for p in service.probes)
            truth_probes = sorted((p["role"], p["path"]) for p in expected["probes"])
            if actual_probes != truth_probes:
                problems.append(
                    f"service {name}.probes: detected {actual_probes}, truth {truth_probes}"
                )

    detected_stores = {store.name: store for store in appspec.datastores}
    for expected in truth.get("datastores") or []:
        store = detected_stores.get(expected["name"])
        if store is None:
            problems.append(f"datastore {expected['name']}: in truth.yaml, not detected")
            continue
        for field in ("kind", "consumed_by", "env_keys"):
            if field in expected and getattr(store, field) != expected[field]:
                problems.append(
                    f"datastore {store.name}.{field}: detected "
                    f"{getattr(store, field)!r}, truth {expected[field]!r}"
                )
    for name in sorted(set(detected_stores) - {e["name"] for e in truth.get("datastores") or []}):
        problems.append(f"datastore {name}: detected, absent from truth.yaml")

    expected_credentials = {
        (item["path"], item["key"]) for item in truth.get("committed_credentials") or []
    }
    detected_credentials = {
        (gap.evidence[0].split(":")[0], gap.id.rsplit(".", 1)[-1])
        for gap in result.gaps
        if gap.id.startswith("credential.") and gap.evidence
    }
    for missing in sorted(expected_credentials - detected_credentials):
        problems.append(f"committed credential {missing} in truth.yaml, not detected")
    for extra in sorted(detected_credentials - expected_credentials):
        problems.append(f"committed credential {extra} detected, absent from truth.yaml")

    forbidden = truth.get("must_not_detect") or {}
    detected_env = {var.name for service in appspec.services for var in service.env}
    for name in forbidden.get("env_names") or []:
        if name in detected_env:
            problems.append(f"must_not_detect: env {name} was detected")
    for path in forbidden.get("credential_paths") or []:
        if any(path in item for gap in result.gaps for item in gap.evidence):
            problems.append(f"must_not_detect: credential in {path} was reported")
    for route in forbidden.get("routes") or []:
        if route in {item.path for item in appspec.routes}:
            problems.append(f"must_not_detect: route {route} was detected")

    recorded_path = directory / "gap_count.json"
    if recorded_path.is_file():
        recorded = json.loads(recorded_path.read_text(encoding="utf-8"))
        counts = gap_counts(result.gaps)
        if counts["blocking"] > recorded["blocking"]:
            problems.append(
                f"blocking gap count rose from {recorded['blocking']} to "
                f"{counts['blocking']}. A rise needs the authorising RFC to say why; "
                f"otherwise a detector regressed. If the rise is correct, update "
                f"{recorded_path} in the same commit and explain it in the PR body."
            )
        elif counts != recorded:
            problems.append(
                f"gap count changed: recorded {recorded}, now {counts}. If this is an "
                f"improvement, update {recorded_path} in the same commit."
            )
    return problems


def main() -> int:
    directories = sorted(
        path for path in FIXTURES_DIR.iterdir()
        if path.is_dir() and (path / "truth.yaml").is_file()
    )
    if not directories:
        print("error: no fixtures with a truth.yaml", file=sys.stderr)
        return 1
    failures = 0
    for directory in directories:
        problems = check_fixture(directory)
        counts = gap_counts(scan_repo(directory).gaps)
        status = "ok" if not problems else f"{len(problems)} problem(s)"
        print(f"{directory.name}: {status}  gaps: " + "  ".join(
            f"{level}={counts[level]}" for level in SEVERITY_ORDER
        ))
        for problem in problems:
            print(f"  - {problem}")
        failures += len(problems)
    print(f"checked {len(directories)} fixture(s), {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
