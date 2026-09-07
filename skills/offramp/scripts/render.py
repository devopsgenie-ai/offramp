#!/usr/bin/env python3
"""render: AppSpec -> an output tree plus .offramp/manifest.json.

    render.py <appspec.json> --out <dir>

The rendering itself is in renderers.py and is pure. This file is the I/O edge: it
deserialises, checks for conflicts, writes, and records hashes.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from renderers import render_tree                                        # noqa: E402
from spec import AppSpec, canonical_json, from_jsonable, to_jsonable      # noqa: E402

MANIFEST_PATH = ".offramp/manifest.json"
RENDERER_VERSION = "spike-2"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_appspec(path: Path) -> tuple[AppSpec, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    appspec = from_jsonable(data)
    return appspec, content_sha256(appspec)


def content_sha256(appspec: AppSpec) -> str:
    """Hash everything except `source`.

    #10: `source.commit` changes on every commit. Including it would change the
    manifest on every commit -- so a tree that no renderer touched would report as
    changed -- and, under RFC-0001, would invalidate the basis of every stored
    answer for a reason unrelated to the application. Provenance is recorded in the
    AppSpec, where it belongs; it is not part of what the renderers consume.
    """
    payload = copy.deepcopy(to_jsonable(appspec))
    payload.pop("source", None)
    return sha256(canonical_json(payload))


def build_manifest(files: dict[str, str], content_sha: str) -> str:
    """#15: the manifest lists every rendered file *except itself*.

    A manifest containing its own hash could not be written, and RFC-0001 does not
    say which way it goes. Excluding it costs nothing: the manifest is a pure
    function of the file set, so `verify` re-renders it and compares it like any
    other file. RFC-0001 also asks for "the AppSpec revision that produced them"
    while stating the AppSpec deliberately carries no version; this records the
    content hash instead, which is the thing that can actually be compared.
    """
    return canonical_json(
        {
            "content_sha256": content_sha,
            "renderer_version": RENDERER_VERSION,
            "files": {path: sha256(content) for path, content in sorted(files.items())},
        }
    )


def conflicts(out: Path, files: dict[str, str], previous: dict[str, str]) -> list[str]:
    """Files render must not clobber.

    Two cases. A file offramp wrote and a human then edited -- RFC-0001 names this
    one. And, per #14, a file offramp never wrote that happens to sit where it
    wants to write: the output tree is rooted in the user's own repository, so
    `.dockerignore` may already exist and be theirs. The manifest protects
    generated files; nothing in the RFC protects the first-run collision.
    """
    found = []
    for path in sorted(files):
        on_disk = out / path
        if not on_disk.exists():
            continue
        if path in previous:
            if sha256(on_disk.read_text(encoding="utf-8")) != previous[path]:
                found.append(f"{path}  (edited since the last render)")
        else:
            found.append(f"{path}  (already exists; offramp did not write it)")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appspec", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="overwrite conflicting files")
    args = parser.parse_args()

    appspec, content_sha = load_appspec(args.appspec)
    files = render_tree(appspec)
    files[MANIFEST_PATH] = build_manifest(files, content_sha)

    manifest_path = args.out / MANIFEST_PATH
    previous = (
        json.loads(manifest_path.read_text(encoding="utf-8")).get("files", {})
        if manifest_path.exists()
        else {}
    )
    if not args.force:
        found = conflicts(args.out, files, previous)
        if found:
            print("error: render would overwrite files it cannot safely replace:", file=sys.stderr)
            for item in found:
                print(f"  {item}", file=sys.stderr)
            print(
                "\nReconcile them by hand, or re-run with --force to discard them.",
                file=sys.stderr,
            )
            return 2

    for path, content in sorted(files.items()):
        target = args.out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    print(f"rendered {len(files)} files -> {args.out}")
    for path in sorted(files):
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
