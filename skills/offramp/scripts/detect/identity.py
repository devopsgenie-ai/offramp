"""Who this application is.

RFC-0001: AppSpec.name "is taken from the git remote, or it is a blocking gap. It is
never derived from the checkout directory." That is not a style preference -- the name
reaches every label, the namespace, the Ingress host and the Secret name, so deriving
it from the filesystem makes the generated tree a function of where someone cloned.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

from spec import Evidence
from walk import dns_label

#: Markers that identify the source platform, checked in sorted order.
PLATFORM_MARKERS = (
    ("emergent", ".emergent/emergent.yml"),
    ("lovable", "supabase/config.toml"),
)

_REMOTE_TAIL = re.compile(r"[:/]([^/:]+?)(?:\.git)?$")


def name_from_remote(url: str) -> str:
    """The repository's own name, as a DNS label."""
    match = _REMOTE_TAIL.search(url.rstrip("/"))
    return dns_label(match.group(1)) if match else ""


def _read_remote(path: Path) -> str | None:
    """`[remote "origin"] url` from a git config file, without invoking git.

    Invoking `git` would read the environment and the user's global config, which
    AGENTS.md §3 forbids inside a detector.
    """
    parser = configparser.ConfigParser()
    try:
        parser.read_string(path.read_text(encoding="utf-8"))
    except (OSError, configparser.Error):
        return None
    for section in ('remote "origin"', "remote origin"):
        if parser.has_option(section, "url"):
            return parser.get(section, "url").strip()
    return None


def _read_head(root: Path) -> str | None:
    head = root / ".git" / "HEAD"
    if not head.is_file():
        return None
    text = head.read_text(encoding="utf-8").strip()
    if not text.startswith("ref: "):
        return text or None
    ref = root / ".git" / text[len("ref: "):]
    return ref.read_text(encoding="utf-8").strip() if ref.is_file() else None


def detect_identity(root: Path) -> tuple[str | None, str | None, str | None, list[Evidence]]:
    """Return (name, repo_url, commit, evidence).

    `.gitconfig` is a fallback because a fixture cannot contain a nested `.git/`
    directory -- git will not track one. Real repositories hit the first branch.
    """
    for relative in (".git/config", ".gitconfig"):
        path = root / relative
        if not path.is_file():
            continue
        url = _read_remote(path)
        if url is None:
            continue
        name = name_from_remote(url) or None
        return name, url, _read_head(root), [Evidence(path=relative, line=1)]
    # No stable source. The caller raises a blocking gap; it never guesses.
    return None, None, None, []


def detect_platform(root: Path) -> tuple[str, list[Evidence]]:
    for platform, marker in PLATFORM_MARKERS:
        if (root / marker).is_file():
            return platform, [Evidence(path=marker, line=1)]
    return "unknown", []
