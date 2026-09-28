"""credential.committed: a working credential is in the repository.

Two sources. The committed-credential detector already raises an `action` gap for each
`.env` value and connection string -- RFC-0001's rotate-before-cutover instruction, a
step in a migration. This check states the same fact as a risk in production today and
links back to the gap. Gap counts do not change.

Running over the recall corpus sharpened it in both directions:

  fewer   a gap's value can be a template (`your_api_key_here`, `<password>`), a
          duration (`TOKEN_EXPIRY=24h`) or a key that is public by design. The gap stays
          -- it is the detector's -- but a finding would be a false alarm.
  more    `service_role` keys and live secret keys written as literals into server-side
          code (edge functions, seed scripts). The frontend check owns literals in files
          that ship to the browser; this one owns the rest, so nothing is reported twice.

Values are read in memory to make those decisions and never leave this module.
"""

from __future__ import annotations

from pathlib import Path

from checks.frontend import bundle_files
from checks.secrets import is_placeholder, is_public_by_design, secret_kind
from detect.env import BUILD_ARG_PREFIXES, parse_env_file
from findings import Assessment, Finding
from walk import read_source, rel, walk_files

CHECK = "credential.committed"
_SOURCE_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".py", ".json", ".yml", ".yaml",
                    ".toml", ".sql", ".sh")

#: Env files that are templates by convention. The detector still raises its gap for them
#: -- that is its call -- but the corpus showed their values are placeholders.
_TEMPLATE_ENV = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist",
                           ".env.defaults", "example.env"})

_REMEDY = (
    "Rotate the credential at its source, then supply the new value through the "
    "environment rather than a committed file. Treat the old value as disclosed."
)


def _cited_line(root: Path, evidence: str) -> "tuple[str, int, str] | None":
    path, _, line = evidence.rpartition(":")
    try:
        text = read_source(root / path).splitlines()[int(line) - 1]
    except (OSError, ValueError, IndexError):
        return None
    return path, int(line), text


def _is_real(root: Path, gap, key: str) -> bool:
    """Whether the value behind a credential gap is plausibly a working secret."""
    if not gap.evidence:
        return True
    cited = _cited_line(root, gap.evidence[0])
    if cited is None:
        return True
    path, line, text = cited
    if Path(path).name in _TEMPLATE_ENV:
        return False
    if key == "literal":
        return not any(is_placeholder(token) for token in text.replace("'", '"').split('"')
                       if "://" in token)
    if is_public_by_design(key, BUILD_ARG_PREFIXES):
        return False
    for found_key, value, found_line in parse_env_file(root / path):
        if found_key == key and found_line == line:
            return not is_placeholder(value)
    return True


def _from_gaps(root: Path, scan) -> list[Finding]:
    findings: list[Finding] = []
    for gap in scan.gaps:
        if not gap.id.startswith("credential."):
            continue
        key = gap.id.rsplit(".", 1)[-1]
        if not _is_real(root, gap, key):
            continue
        path = gap.evidence[0].rsplit(":", 1)[0] if gap.evidence else "(unknown file)"
        subject = "A connection string with an embedded password" if key == "literal" \
            else f"`{key}`"
        findings.append(Finding(
            id=f"{CHECK}.{gap.id.removeprefix('credential.')}",
            check=CHECK,
            category="security",
            severity="critical",
            title=(f"`{key}` is committed to the repository" if key != "literal"
                   else f"A database password is written into `{path}`"),
            detail=(
                f"{subject} is committed with a real-looking value in `{path}`. Anyone "
                f"who can read this repository, or any copy or fork of it, can use it. "
                f"Deleting the file does not help: the value stays in the git history."
            ),
            remedy=_REMEDY,
            evidence=list(gap.evidence),
            related_gaps=[gap.id],
        ))
    return findings


def _from_server_code(root: Path, scan) -> list[Finding]:
    bundled = set(bundle_files(root, scan))
    findings: list[Finding] = []
    for path in walk_files(root, suffixes=_SOURCE_SUFFIXES):
        if path in bundled:
            continue
        relative = rel(root, path)
        for number, line in enumerate(read_source(path).splitlines(), start=1):
            kind = secret_kind(line)
            if kind is None:
                continue
            findings.append(Finding(
                id=f"{CHECK}.{relative.replace('/', '.')}.{number}",
                check=CHECK,
                category="security",
                severity="critical",
                title=f"{kind[0].upper()}{kind[1:]} is committed in `{relative}`",
                detail=(
                    f"`{relative}` line {number} contains {kind} as a literal. It runs on "
                    f"the server, not in the browser, but anyone who can read this "
                    f"repository can use it -- and a `service_role` key bypasses every "
                    f"row-level security policy."
                ),
                remedy=_REMEDY,
                evidence=[f"{relative}:{number}"],
            ))
    return findings


def check_committed_credentials(root: Path, scan) -> tuple[list[Finding], Assessment]:
    findings = _from_gaps(root, scan) + _from_server_code(root, scan)
    return findings, Assessment(check=CHECK, status="found" if findings else "clean")
