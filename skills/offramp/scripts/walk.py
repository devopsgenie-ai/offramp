"""The service-tree walk, and the path helpers every detector shares.

RFC-0001, Detectors: "Detectors walk the service tree. Looking only at `server.py`
(or any single entry file) is a bug." On a real Emergent repository a module-scoped
backend detector found 1 of 15 environment variables, no routes and no probe -- and
kubeconform passed. Every detector goes through walk_files.

The exclusion set is shared for a second reason the RFC states outright: "Excluding
only `node_modules` will treat a dependency's example `.env` as the user's committed
credential."
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

EXCLUDED_DIRS = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist"}
)

_NON_LABEL = re.compile(r"[^a-z0-9-]+")


def walk_files(root: Path, suffixes: tuple[str, ...] | None = None) -> Iterator[Path]:
    """Yield files under `root`, pruning EXCLUDED_DIRS at any depth.

    Sorted per directory so the order is a function of the repository and not of the
    filesystem -- AGENTS.md §3 requires collections to be iterated in sorted order.

    Symlinks are skipped rather than followed: following them can loop, and a loop
    would make the walk depend on something other than the repository's contents.
    """
    for entry in sorted(root.iterdir()):
        if entry.is_symlink():
            continue
        if entry.is_dir():
            if entry.name in EXCLUDED_DIRS:
                continue
            yield from walk_files(entry, suffixes)
        elif entry.is_file():
            if suffixes is None or entry.suffix in suffixes:
                yield entry


def rel(root: Path, path: Path) -> str:
    """POSIX-separated path relative to the scan root."""
    return path.relative_to(root).as_posix()


def dns_label(value: str) -> str:
    """Lower-case RFC-1123-ish label, for names that become Kubernetes object names."""
    collapsed = _NON_LABEL.sub("-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", collapsed)
