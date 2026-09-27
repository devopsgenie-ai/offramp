"""What the application stores data in.

Usage decides, not the dependency list. `motor` in requirements.txt is evidence the app
once used Mongo; it is not evidence it still does, and generating a StatefulSet for a
database nothing talks to is a confident invention. When the two disagree, say so.

`mode` -- in the cluster, managed, or external -- is never defaulted. It is the decision
with the largest cost and compliance consequences in the whole migration, and it is not
in the repository.
"""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from pathlib import Path

from spec import Datastore, Evidence, Gap, Service, gap_id
from walk import read_source, rel, walk_files

#: (kind, import roots, dependency names). Sorted by kind so output order is fixed.
KNOWN_DATASTORES = (
    ("mongodb", ("motor", "pymongo"), ("motor", "pymongo")),
    ("mysql", ("pymysql", "aiomysql"), ("pymysql", "aiomysql")),
    ("postgres", ("psycopg", "psycopg2", "asyncpg", "sqlalchemy"),
     ("psycopg", "psycopg2", "psycopg2-binary", "asyncpg", "sqlalchemy")),
    ("redis", ("redis", "aioredis"), ("redis", "aioredis")),
)

#: Env keys that name a connection, per kind.
CONNECTION_KEY_RE = {
    "mongodb": re.compile(r"MONGO|MONGODB"),
    "mysql": re.compile(r"MYSQL"),
    "postgres": re.compile(r"POSTGRES|PG_|DATABASE_URL"),
    "redis": re.compile(r"REDIS"),
}


def _imported_roots(text: str) -> set[str]:
    roots: set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return roots
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _declared_dependencies(directory: Path) -> set[str]:
    requirements = directory / "requirements.txt"
    if not requirements.is_file():
        return set()
    found = set()
    for line in read_source(requirements).splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped and not stripped.startswith("-"):
            found.add(re.split(r"[=<>!~\[]", stripped, maxsplit=1)[0].strip().lower())
    return found


def detect_datastores(
    root: Path, services: list[Service]
) -> tuple[list[Datastore], list[Gap], list[Evidence]]:
    used: dict[str, dict] = {}
    declared: dict[str, set[str]] = {}

    for service in sorted(services, key=lambda item: item.name):
        directory = root / service.build.context
        if not directory.is_dir():
            continue
        dependencies = _declared_dependencies(directory)
        for kind, import_roots, dependency_names in KNOWN_DATASTORES:
            matched = {name for name in dependency_names if name in dependencies}
            if matched:
                declared.setdefault(kind, set()).update(matched)

        for path in walk_files(directory, suffixes=(".py",)):
            roots = _imported_roots(read_source(path))
            for kind, import_roots, _ in KNOWN_DATASTORES:
                if roots & set(import_roots):
                    entry = used.setdefault(kind, {"services": set(), "evidence": []})
                    entry["services"].add(service.name)
                    entry["evidence"].append(Evidence(path=rel(root, path), line=1))

    datastores: list[Datastore] = []
    gaps: list[Gap] = []
    evidence: list[Evidence] = []

    for kind, _, _ in KNOWN_DATASTORES:
        if kind not in used:
            if kind in declared:
                names = ", ".join(sorted(declared[kind]))
                gaps.append(Gap(
                    id=gap_id("datastore", kind, "disputed"),
                    kind="value",
                    pointers=[],
                    question=(
                        f"Does this application still use {kind}? `{names}` is declared "
                        f"in requirements.txt but no module imports it, so the "
                        f"dependency list and the code disagree. A leftover dependency "
                        f"is common after a rewrite. Answer `in_cluster`, `managed` or "
                        f"`external` to generate for it anyway, or say it is unused and "
                        f"nothing will be generated. Nothing is assumed either way: "
                        f"generating a database nothing talks to wastes money, and "
                        f"omitting one that is still in use breaks the application."
                    ),
                    proposed=None,
                    confidence="low",
                    severity="important",
                    evidence=["requirements.txt"],
                ))
            continue

        entry = used[kind]
        consumers = sorted(entry["services"])
        pattern = CONNECTION_KEY_RE[kind]
        env_keys = sorted({
            var.name
            for service in services if service.name in consumers
            for var in service.env
            if pattern.search(var.name.upper())
        })
        datastores.append(Datastore(
            name=kind, kind=kind, version=None, mode=None,
            consumed_by=consumers, env_keys=env_keys,
        ))
        evidence.extend(sorted(entry["evidence"], key=lambda item: (item.path, item.line)))

        index = len(datastores) - 1
        gaps.append(Gap(
            id=gap_id("datastore", kind, "mode"),
            kind="value",
            pointers=[f"/datastores/{index}/mode"],
            question=(
                f"How should {kind} run after the migration? `in_cluster` generates a "
                f"StatefulSet and a PersistentVolumeClaim and you operate it: cheapest, "
                f"and backups, upgrades and failover become yours. `managed` generates a "
                f"reference to a provider-run instance you create separately: the "
                f"operational work goes away and the bill appears. `external` generates "
                f"a reference to something that already exists and is not moving -- "
                f"including the platform's own database, if you are migrating compute "
                f"first. Nothing is proposed: this is a cost and data-residency "
                f"decision, and nothing in the repository indicates the answer."
            ),
            proposed=None,
            confidence="low",
            severity="blocking",
            evidence=[item.path for item in entry["evidence"][:1]],
        ))
        gaps.append(Gap(
            id=gap_id("datastore", kind, "version"),
            kind="value",
            pointers=[f"/datastores/{index}/version"],
            question=(
                f"Which {kind} version? Only needed if {kind} runs in the cluster -- a "
                f"managed or external instance already has one. Match what the "
                f"application runs on today, which the client library's own "
                f"compatibility range does not pin down."
            ),
            proposed=None,
            confidence="low",
            severity="important",
            evidence=[],
            # A flat gap list would ask this even when it turns out to be moot.
            depends_on=[gap_id("datastore", kind, "mode")],
        ))

    datastores.sort(key=lambda item: item.name)
    return datastores, gaps, evidence


def tag_datastore_env(
    services: list[Service], datastores: list[Datastore]
) -> list[Service]:
    """Set `EnvVar.source = "datastore"` for connection keys. Returns new objects.

    Provenance is a separate axis from sensitivity, so this never touches `sensitive`:
    MONGO_URL is a datastore fact *and* a credential, and the renderer's secretKeyRef
    keys on the latter.
    """
    keys = {key for store in datastores for key in store.env_keys}
    tagged: list[Service] = []
    for service in services:
        tagged.append(replace(service, env=[
            replace(var, source="datastore") if var.name in keys else replace(var)
            for var in service.env
        ]))
    return tagged
