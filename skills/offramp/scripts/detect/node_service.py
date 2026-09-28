"""The Node service: how it builds, and what it produces.

The modal generated application is a single-page app compiled to static files. It has
no port and no health endpoint of its own -- the renderer chooses a web server and owns
both. So ports and probes stay empty here, and the gap for them is raised where the
renderer's choice is made rather than pretended into the AppSpec.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from detect.python_service import DEFAULT_RESOURCES
from spec import Build, Evidence, Gap, Runtime, Service, gap_id
from walk import dns_label, rel, walk_files

PROPOSED_NODE = "20"

#: (dependency name, conventional output directory), checked in order.
BUNDLER_OUTPUT = (
    ("react-scripts", "build"),
    ("vite", "dist"),
    ("next", ".next"),
    ("parcel", "dist"),
)


def find_node_services(root: Path) -> list[Path]:
    """Directories holding a package.json. walk_files prunes node_modules, so a
    dependency's own package.json can never be mistaken for a service."""
    return sorted({path.parent for path in walk_files(root, suffixes=(".json",))
                   if path.name == "package.json"})


def _node_version(package: dict, directory: Path) -> tuple["str | None", "str | None"]:
    nvmrc = directory / ".nvmrc"
    if nvmrc.is_file():
        text = nvmrc.read_text(encoding="utf-8").strip().lstrip("v")
        if text:
            return text, ".nvmrc"
    declared = package.get("engines", {}).get("node")
    if isinstance(declared, str):
        match = re.search(r"(\d+(?:\.\d+)*)", declared)
        if match:
            return match.group(1), "package.json"
    return None, None


def detect_node_service(
    root: Path, directory: Path
) -> tuple[Service, list[Gap], list[Evidence]]:
    # A service at the repository root has no directory of its own to be named after,
    # and the checkout directory is not stable (RFC-0001) -- so it is named by its role.
    name = dns_label(directory.name) if directory != root else "web"
    context = rel(root, directory)
    manifest = directory / "package.json"
    manifest_rel = rel(root, manifest)
    package = json.loads(manifest.read_text(encoding="utf-8"))
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    scripts = package.get("scripts", {})

    gaps: list[Gap] = []
    evidence: list[Evidence] = [Evidence(path=manifest_rel, line=1)]

    version, version_source = _node_version(package, directory)
    if version_source == ".nvmrc":
        evidence.append(Evidence(path=rel(root, directory / ".nvmrc"), line=1))
    elif version_source is None:
        gaps.append(Gap(
            id=gap_id("service", name, "runtime", "version"),
            kind="value",
            pointers=[f"/services/{name}/runtime/version"],
            question=(
                f"Which Node version builds `{context}`? Neither `engines.node` nor "
                f".nvmrc declares one, so the build image tag would be a guess."
            ),
            proposed=PROPOSED_NODE,
            confidence="medium",
            severity="important",
            evidence=[manifest_rel],
            evidence_scope=[context],
        ))

    lockfiles = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml")
    present = [item for item in lockfiles if (directory / item).is_file()]
    if present:
        install_cmd = {"yarn.lock": "yarn install --frozen-lockfile",
                       "pnpm-lock.yaml": "pnpm install --frozen-lockfile"}.get(
            present[0], "npm ci")
        evidence.append(Evidence(path=rel(root, directory / present[0]), line=1))
    else:
        install_cmd = "npm install"
        gaps.append(Gap(
            id=gap_id("service", name, "dependencies", "lockfile"),
            kind="action",
            pointers=[],
            question=(
                f"Commit a lock file for `{context}`. There is none, so `npm install` "
                f"resolves ranges afresh on every build and two builds of the same "
                f"commit can ship different dependency code. Run `npm install` locally, "
                f"commit the resulting package-lock.json, and confirm here -- the "
                f"generated build will then use `npm ci`."
            ),
            proposed=None,
            confidence="high",
            severity="important",
            evidence=[manifest_rel],
            evidence_scope=[context],
        ))

    build_cmd = "npm run build" if "build" in scripts else None
    output_dir = next((out for dep, out in BUNDLER_OUTPUT if dep in dependencies), None)
    if output_dir is None and build_cmd is not None:
        gaps.append(Gap(
            id=gap_id("service", name, "build", "output_dir"),
            kind="value",
            pointers=[f"/services/{name}/build/output_dir"],
            question=(
                f"Which directory does `npm run build` write in `{context}`? The build "
                f"script is `{scripts.get('build')}`, which is not a bundler this "
                f"version recognises, so the directory to copy into the image is "
                f"unknown. Common answers are `build` and `dist`."
            ),
            proposed=None,
            confidence="low",
            severity="blocking",
            evidence=[manifest_rel],
            evidence_scope=[context],
        ))

    dockerfile = directory / "Dockerfile"
    service = Service(
        name=name,
        role="web",
        runtime=Runtime(language="node", version=version),
        build=Build(
            context=context,
            dockerfile=rel(root, dockerfile) if dockerfile.is_file() else None,
            install_cmd=install_cmd,
            build_cmd=build_cmd,
            start_cmd=None,
            output_dir=output_dir,
        ),
        # Empty by design: static assets have no port and no health endpoint of their
        # own. The renderer chooses the web server and owns both.
        ports=[],
        probes=[],
        env=[],
        resources=DEFAULT_RESOURCES,
        replicas=1,
    )
    evidence.sort(key=lambda item: (item.path, item.line))
    return service, gaps, evidence
