"""Every file falls in exactly one area. RFC-0005, *The boundary* and *Testing*.

The patterns are written in CODEOWNERS syntax, and the core list is mirrored in
`.github/CODEOWNERS` line for line, so a reviewer sees a core change and a new module
cannot land unassigned.
"""

import re
from pathlib import Path

AREAS = {
    "core": (
        "/skills/offramp/scripts/spec.py",
        "/skills/offramp/scripts/gaps.py",
        "/skills/offramp/scripts/walk.py",
        "/skills/offramp/scripts/scan.py",
        "/skills/offramp/scripts/report.py",
        "/skills/offramp/scripts/rls.py",
        "/skills/offramp/scripts/verify.py",
        "/skills/offramp/scripts/detect/",
        "/fixtures/*/expected_bare/",
        "/fixtures/*/truth.yaml",
        "/fixtures/*/gap_count.json",
        "/scripts/check_fixtures.py",
        "/scripts/check_rfcs.py",
    ),
    "auditor": (
        "/skills/offramp/scripts/audit.py",
        "/skills/offramp/scripts/audit_report.py",
        "/skills/offramp/scripts/findings.py",
        "/skills/offramp/scripts/fix.py",
        "/skills/offramp/scripts/fix_plan.py",
        "/skills/offramp/scripts/fix_render.py",
        "/skills/offramp/scripts/fix_sql.py",
        "/skills/offramp/scripts/checks/",
        "/fixtures/*/expected_audit/",
        "/fixtures/*/expected_fix/",
        "/fixtures/*/behaviour.yaml",
        "/scripts/check_fix_behaviour.py",
        "/docs/research/",
    ),
    "generator": (
        "/skills/offramp/scripts/render.py",
        "/skills/offramp/scripts/render/",
        "/skills/offramp/scripts/show.py",
        "/skills/offramp/scripts/apply.py",
        "/skills/offramp/SKILL.md",
        "/fixtures/*/expected_render/",
    ),
}

COVERED = (Path("skills/offramp/scripts"), Path("scripts"))
CODEOWNERS = Path(".github/CODEOWNERS")


def _matches(pattern: str, path: str) -> bool:
    """CODEOWNERS semantics for the forms used here: anchored at the root, `*` within
    one segment, a trailing `/` for everything below a directory."""
    body = re.escape(pattern.lstrip("/")).replace(r"\*", "[^/]*")
    if pattern.endswith("/"):
        body += ".*"
    return re.fullmatch(body, path) is not None


def _areas(path: str) -> list[str]:
    return [area for area, patterns in AREAS.items()
            if any(_matches(pattern, path) for pattern in patterns)]


def _files() -> list[str]:
    return sorted(
        path.as_posix() for root in COVERED for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def test_every_file_falls_in_exactly_one_area():
    files = _files()
    assert files
    problems = {path: _areas(path) for path in files if len(_areas(path)) != 1}
    assert problems == {}, "assign each file to exactly one area in AREAS and CODEOWNERS"


def test_the_matcher_follows_codeowners_semantics():
    assert _matches("/skills/offramp/scripts/detect/", "skills/offramp/scripts/detect/env.py")
    assert _matches("/fixtures/*/truth.yaml", "fixtures/lovable-rls-owners/truth.yaml")
    assert not _matches("/fixtures/*/truth.yaml", "fixtures/a/b/truth.yaml")
    assert not _matches("/skills/offramp/scripts/fix.py", "skills/offramp/scripts/fix_sql.py")
    assert _areas("skills/offramp/scripts/new_module.py") == []


def test_codeowners_lists_exactly_the_core_paths():
    """CODEOWNERS names the core explicitly (RFC-0005, rule 1). Track paths inherit the
    default rule, so a track path listed there would read as core to a reviewer."""
    lines = CODEOWNERS.read_text(encoding="utf-8").splitlines()
    listed = {line.split()[0] for line in lines if line.strip() and not line.startswith("#")}
    in_tracks = {path for path in listed
                 if path.startswith(("/skills/", "/fixtures/", "/scripts/"))
                 and path != "/scripts/"}
    assert in_tracks == set(AREAS["core"])
