"""platform.hardcoded_url: what stops working when the application leaves the platform.

This is the part of the audit that is about leaving rather than security: sign-in that
runs through the platform's own auth, model calls billed through its gateway, a frontend
built against its preview URL. One finding per coupling, citing up to five places.

Tests, documentation and lockfiles are skipped: a URL in a README or a generated test
harness does not break the running application. The rules are data, in checks/known.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from checks.known import PLATFORM_COUPLINGS
from findings import Assessment, Finding
from walk import read_source, rel, walk_files

CHECK = "platform.hardcoded_url"
_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".py", ".html", ".json", ".toml")
_LOCKFILES = frozenset({"package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
                        "pnpm-lock.yaml", "bun.lockb", "poetry.lock"})
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "e2e", "cypress", "playwright"})
_TEST_FILE = re.compile(r"(?:^test_.*\.py|_test\.py|\.test\.|\.spec\.)")
_MAX_EVIDENCE = 5
_RULES = tuple((rule, re.compile(pattern, re.I), *rest)
               for rule, pattern, *rest in PLATFORM_COUPLINGS)


def _in_scope(root: Path, path: Path) -> bool:
    if path.name in _LOCKFILES or _TEST_FILE.search(path.name):
        return False
    if any(part in _TEST_DIRS for part in path.relative_to(root).parts[:-1]):
        return False
    return path.suffix in _SUFFIXES or path.name == ".env" or path.name.startswith(".env.")


def check_platform_urls(root: Path, scan) -> tuple[list[Finding], Assessment]:
    hits: dict[str, list[str]] = {}
    for path in walk_files(root):
        if not _in_scope(root, path):
            continue
        relative = rel(root, path)
        for number, line in enumerate(read_source(path).splitlines(), start=1):
            for rule, pattern, *_ in _RULES:
                if pattern.search(line):
                    hits.setdefault(rule, []).append(f"{relative}:{number}")
    findings: list[Finding] = []
    for rule, _, severity, title, detail, remedy in _RULES:
        if rule not in hits:
            continue
        cited = hits[rule]
        more = len(cited) - _MAX_EVIDENCE
        findings.append(Finding(
            id=f"{CHECK}.{rule}",
            check=CHECK,
            category="portability",
            severity=severity,
            title=title,
            detail=detail + (f" Found in {len(cited)} places; the first "
                             f"{_MAX_EVIDENCE} are cited." if more > 0 else ""),
            remedy=remedy,
            evidence=cited[:_MAX_EVIDENCE],
        ))
    return findings, Assessment(check=CHECK, status="found" if findings else "clean")
