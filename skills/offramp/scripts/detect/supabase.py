"""Supabase, as the datastore of the modal Lovable application. RFC-0002.

Usage decides, as it does for every other datastore: `@supabase/supabase-js` in
package.json is evidence the application once used Supabase, and an import of it is
evidence it still does. When they disagree, say so.

`mode` is never defaulted (RFC-0001). A client pointed at a `*.supabase.co` URL is
strong evidence the database is hosted and staying where it is, so `external` is
*proposed* in the gap -- and still left for the user to answer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from spec import Datastore, Evidence, Gap, Service, gap_id
from walk import read_source, rel, walk_files

PACKAGE = "@supabase/supabase-js"
_SOURCE_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs")
_IMPORT_RE = re.compile(r"""(?:from\s+|require\(\s*)["']@supabase/supabase-js["']""")
_HOSTED_RE = re.compile(r"https://[a-z0-9]+\.supabase\.co\b")


def _declares(directory: Path) -> bool:
    manifest = directory / "package.json"
    if not manifest.is_file():
        return False
    try:
        package = json.loads(read_source(manifest))
    except ValueError:
        return False
    return PACKAGE in {**package.get("dependencies", {}), **package.get("devDependencies", {})}


def detect_supabase(
    root: Path, services: list[Service]
) -> tuple[list[Datastore], list[Gap], list[Evidence]]:
    consumers: list[str] = []
    evidence: list[Evidence] = []
    declared = False
    hosted = False

    for service in sorted(services, key=lambda item: item.name):
        directory = root / service.build.context
        declared = declared or _declares(directory)
        used = False
        for path in walk_files(directory, suffixes=_SOURCE_SUFFIXES):
            text = path.read_text(encoding="utf-8", errors="ignore")
            hosted = hosted or bool(_HOSTED_RE.search(text))
            for number, line in enumerate(text.splitlines(), start=1):
                if _IMPORT_RE.search(line):
                    if not used:
                        evidence.append(Evidence(path=rel(root, path), line=number))
                    used = True
                    break
        if used:
            consumers.append(service.name)

    if not consumers:
        if not declared:
            return [], [], []
        return [], [Gap(
            id=gap_id("datastore", "supabase", "disputed"),
            kind="value",
            pointers=[],
            question=(
                f"Does this application still use Supabase? `{PACKAGE}` is declared in "
                f"package.json but no module imports it, so the dependency list and the "
                f"code disagree. Nothing is assumed either way."
            ),
            proposed=None,
            confidence="low",
            severity="important",
            evidence=["package.json"],
        )], []

    env_keys = sorted({
        var.name for service in services if service.name in consumers
        for var in service.env if "SUPABASE" in var.name.upper()
    })
    store = Datastore(name="supabase", kind="postgres", version=None, mode=None,
                      consumed_by=consumers, env_keys=env_keys)
    cited = [f"{item.path}:{item.line}" for item in evidence[:1]]
    gaps = [
        Gap(
            id=gap_id("datastore", "supabase", "mode"),
            kind="value",
            pointers=[],
            question=(
                "Does the Supabase database stay where it is? The client points at a "
                "hosted Supabase project, so `external` -- keep it, and point the "
                "migrated application at it -- is proposed. `managed` moves the data to a "
                "Postgres instance you create; `in_cluster` runs Postgres yourself. The "
                "database also holds auth users and storage, which a plain Postgres "
                "instance does not replace."
                if hosted else
                "Where does the Supabase database run after the migration? The client "
                "URL is not a literal in the repository, so nothing indicates the answer."
            ),
            proposed="external" if hosted else None,
            confidence="medium" if hosted else "low",
            severity="blocking",
            evidence=cited,
        ),
    ]
    return [store], gaps, evidence
