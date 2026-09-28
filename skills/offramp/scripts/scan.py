#!/usr/bin/env python3
"""scan: read a repository, emit an AppSpec, a gap list and an evidence sidecar.

    scan.py <repo> --out <dir>

This is the only module here that writes files. Detectors are data in, data out
(AGENTS.md §6), so composition and I/O both live at this edge.

Outputs, all under --out:

    appspec.json     the IR
    gaps.json        the gap list, for machines
    gaps.md          the same list, for a human
    evidence.json    file:line behind each detected fact
    answerable.json  what a plan is permitted to target
    detected.json    the bare spec, which `apply` will read as Answer.detected
    scan.json        sha256 of each file above, so `verify` can check them
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from detect.credentials import detect_committed_credentials       # noqa: E402
from detect.datastores import detect_datastores, tag_datastore_env  # noqa: E402
from detect.env import detect_env                                 # noqa: E402
from detect.identity import detect_identity, detect_platform      # noqa: E402
from detect.node_service import detect_node_service, find_node_services  # noqa: E402
from detect.probes import detect_probes                           # noqa: E402
from detect.python_service import detect_python_service, find_python_services  # noqa: E402
from detect.routes import detect_routes                           # noqa: E402
from detect.supabase import detect_supabase                       # noqa: E402
from gaps import answerable_set, is_moot, spec_level_gaps         # noqa: E402
from report import render_gap_report                              # noqa: E402
from spec import (                                                # noqa: E402
    SEVERITY_ORDER, AppSpec, Delivery, Environment, Evidence, Gap, Source,
    canonical_json, check_enums, evidence_text,
)

OUTPUT_NAMES = (
    "answerable.json", "appspec.json", "detected.json", "evidence.json",
    "gaps.json", "gaps.md", "scan.json",
)
SCAN_MANIFEST = "scan.json"


@dataclass
class ScanResult:
    appspec: AppSpec
    gaps: list[Gap]
    evidence: list[Evidence]


def scan_repo(root: Path) -> ScanResult:
    """Every detector, composed. Pure: reads the tree, writes nothing."""
    gaps: list[Gap] = []
    evidence: list[Evidence] = []

    name, repo_url, commit, identity_evidence = detect_identity(root)
    platform, platform_evidence = detect_platform(root)
    evidence.extend(identity_evidence + platform_evidence)

    services: list = []
    all_routes: list = []

    for directory in find_python_services(root):
        service, service_gaps, service_evidence = detect_python_service(root, directory)
        service.env, env_gaps, env_evidence = detect_env(root, directory, service.name)
        routes, route_gaps, route_evidence = detect_routes(root, directory, service.name)
        port = service.ports[0] if service.ports else None
        service.probes, probe_gaps = detect_probes(routes, service.name, port)
        services.append(service)
        all_routes.extend(routes)
        gaps.extend(service_gaps + env_gaps + route_gaps + probe_gaps)
        evidence.extend(service_evidence + env_evidence + route_evidence)

    for directory in find_node_services(root):
        service, service_gaps, service_evidence = detect_node_service(root, directory)
        service.env, env_gaps, env_evidence = detect_env(root, directory, service.name)
        _, probe_gaps = detect_probes([], service.name, None)
        services.append(service)
        gaps.extend(service_gaps + env_gaps + probe_gaps)
        evidence.extend(service_evidence + env_evidence)

    services.sort(key=lambda item: item.name)

    datastores, datastore_gaps, datastore_evidence = detect_datastores(root, services)
    supabase, supabase_gaps, supabase_evidence = detect_supabase(root, services)
    datastores = sorted(datastores + supabase, key=lambda item: item.name)
    datastore_gaps += supabase_gaps
    datastore_evidence += supabase_evidence
    services = tag_datastore_env(services, datastores)
    gaps.extend(datastore_gaps)
    evidence.extend(datastore_evidence)

    credential_gaps, credential_evidence = detect_committed_credentials(root)
    gaps.extend(credential_gaps)
    evidence.extend(credential_evidence)

    appspec = AppSpec(
        name=name,
        source=Source(platform=platform, repo_url=repo_url, commit=commit),
        services=services,
        datastores=datastores,
        routes=sorted(all_routes, key=lambda route: (route.service, route.path)),
        # One environment, emitted through the full overlay mechanism rather than around
        # it, so adding staging later is additive instead of structural.
        environments=[Environment(name="production", namespace=None)],
        delivery=Delivery(image_registry=None, image_tag=None, gitops_repo_url=None,
                          ingress_class=None, target_cluster="in-cluster"),
    )

    gaps.extend(spec_level_gaps(appspec))
    gaps = [gap for gap in gaps if not is_moot(gap, appspec)]

    # Sorted by (severity rank, id) so the report and the JSON agree and neither
    # depends on detector call order.
    gaps.sort(key=lambda gap: (SEVERITY_ORDER.index(gap.severity), gap.id))
    unique_evidence = sorted({(item.path, item.line) for item in evidence})
    return ScanResult(
        appspec=appspec,
        gaps=gaps,
        evidence=[Evidence(path=path, line=line) for path, line in unique_evidence],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if not args.repo.is_dir():
        print(f"error: {args.repo} does not exist or is not a directory", file=sys.stderr)
        return 2

    result = scan_repo(args.repo)
    declared, address_problems = answerable_set(result.gaps, result.appspec)
    problems = check_enums(result.appspec, result.gaps) + address_problems
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    written = {
        "appspec.json": canonical_json(result.appspec),
        "gaps.json": canonical_json(result.gaps),
        "gaps.md": render_gap_report(result.appspec, result.gaps),
        "evidence.json": canonical_json(evidence_text(result.evidence)),
        "answerable.json": canonical_json(declared),
        # The bare spec. `apply` reads this as Answer.detected -- taking it from a merged
        # spec would record an answer as though a detector had produced it, and every
        # later scan would then report the real detector as disagreeing.
        "detected.json": canonical_json(result.appspec),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    for name, content in sorted(written.items()):
        (args.out / name).write_text(content, encoding="utf-8")
    (args.out / SCAN_MANIFEST).write_text(
        canonical_json({"files": {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for name, content in sorted(written.items())
        }}),
        encoding="utf-8",
    )

    counts = {level: sum(1 for gap in result.gaps if gap.severity == level)
              for level in SEVERITY_ORDER}
    print(f"scanned {args.repo} -> {args.out}")
    print(f"  name:       {result.appspec.name or '(none — blocking gap)'}")
    print(f"  services:   {', '.join(s.name for s in result.appspec.services) or 'none'}")
    print(f"  datastores: {', '.join(d.name for d in result.appspec.datastores) or 'none'}")
    print("  gaps:       " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    if not result.appspec.services:
        print(
            "\n  WARNING: no services were detected, so there is nothing to deploy. v1\n"
            "  detects a Python service from a directory holding requirements.txt and a\n"
            "  Node service from one holding package.json. A differently shaped\n"
            "  repository is a detector gap worth reporting, not a result.\n",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
