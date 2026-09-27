"""frontend.secret_in_bundle: a secret shipped to every browser.

A frontend build argument is compiled into a file served to everyone who loads the page
(RFC-0001, AppSpec). Two ways it happens, with different confidence:

  high      a build-time variable whose *name* says it is a secret. The value may be
            absent from the repository -- it can be set in the platform's dashboard -- so
            the finding asks the user to confirm.
  critical  the *value* proves it: a Supabase JWT whose role is `service_role`, or a
            literal in a known secret-key format, written into frontend source. Decoding
            a JWT payload is a local base64 decode; the value never leaves this module.

Keys that are public by design (an anon key, a Firebase web API key) are not findings.
Their safety comes from server-side rules, which is what the RLS checks look at.
"""

from __future__ import annotations

from pathlib import Path

from checks.known import SECRET_NAME
from checks.secrets import is_public_by_design, secret_kind
from detect.env import BUILD_ARG_PREFIXES
from findings import Assessment, Finding
from walk import read_source, rel, walk_files

CHECK = "frontend.secret_in_bundle"
_SOURCE_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".vue", ".svelte", ".html")

#: Top-level directories of a frontend service that do not ship to the browser. The
#: corpus put service_role keys in `supabase/functions/` and `scripts/` -- committed and
#: critical, but server-side, so reporting them as "shipped to every browser" was false.
NOT_BUNDLED = frozenset({
    "api", "backend", "cypress", "docs", "e2e", "functions", "netlify", "playwright",
    "scripts", "server", "supabase", "test", "tests", "__tests__",
})


#: File-name markers for code that never ships: tests, type declarations, and the
#: `.server.` convention frameworks such as TanStack Start and Remix use for server-only code.
_NOT_SHIPPED_MARKERS = (".test.", ".spec.", ".d.ts", ".server.")


def _service_dir(root: Path, service) -> Path:
    return root if service.build.context == "." else root / service.build.context


def bundle_files(root: Path, scan) -> list[Path]:
    """Files that plausibly end up in a frontend bundle: under a web service, outside
    server-side directories, other services, tests and root-level build config."""
    others = {_service_dir(root, s) for s in scan.appspec.services if s.role != "web"}
    files: list[Path] = []
    for service in scan.appspec.services:
        if service.role != "web":
            continue
        directory = _service_dir(root, service)
        for path in walk_files(directory, suffixes=_SOURCE_SUFFIXES):
            parts = path.relative_to(directory).parts
            if parts[0] in NOT_BUNDLED or any(o in path.parents for o in others):
                continue
            # At the root of a frontend, only index.html ships. Everything else there is
            # build config or a one-off node script -- the corpus had migrate-data.mjs.
            if len(parts) == 1 and path.suffix != ".html":
                continue
            if any(marker in path.name for marker in _NOT_SHIPPED_MARKERS):
                continue
            files.append(path)
    return sorted(set(files))


def _first_read(root: Path, files: list[Path], key: str) -> "str | None":
    for path in files:
        for number, line in enumerate(read_source(path).splitlines(), start=1):
            if key in line:
                return f"{rel(root, path)}:{number}"
    return None


def _named_secrets(root: Path, service, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for var in service.env:
        if var.binding != "build_arg":
            continue
        if is_public_by_design(var.name, BUILD_ARG_PREFIXES):
            continue
        bare = var.name
        for prefix in BUILD_ARG_PREFIXES:
            bare = bare.removeprefix(prefix)
        if not SECRET_NAME.search(bare):
            continue
        location = _first_read(root, files, var.name)
        if location is None:
            # Only server-side files read it, so nothing puts it in the bundle.
            continue
        findings.append(Finding(
            id=f"{CHECK}.{service.name}.{var.name}",
            check=CHECK,
            category="security",
            severity="high",
            title=f"`{var.name}` looks like a secret, and it is shipped to every browser",
            detail=(
                f"`{var.name}` is substituted into the JavaScript bundle at build time, so "
                f"whatever value it holds is readable by anyone who loads the page. Its "
                f"name says it is a secret. If it is, it has been public for as long as "
                f"the application has been deployed."
            ),
            remedy=(
                "If this is a real credential, revoke it and move the call that needs it "
                "behind a server-side function. Rebuilding with a new value does not help: "
                "the new value is baked into the new bundle too."
            ),
            evidence=[location],
        ))
    return findings


def _proven_secrets(root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
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
                title=f"{kind[0].upper()}{kind[1:]} is written into frontend code",
                detail=(
                    f"`{relative}` line {number} contains {kind}. Frontend code is shipped "
                    f"to every visitor's browser, so this key is public. A `service_role` "
                    f"key bypasses every row-level security policy: anyone holding it can "
                    f"read and change all data."
                ),
                remedy=(
                    "Revoke the key now and issue a new one. Keep secret keys only in "
                    "server-side code or server-side functions, never in the frontend."
                ),
                evidence=[f"{relative}:{number}"],
            ))
    return findings


def check_secret_in_bundle(root: Path, scan) -> tuple[list[Finding], Assessment]:
    web = [service for service in scan.appspec.services if service.role == "web"]
    if not web:
        return [], Assessment(check=CHECK, status="not_applicable",
                              reason="no frontend service in the repository")
    files = bundle_files(root, scan)
    findings: list[Finding] = []
    for service in web:
        findings.extend(_named_secrets(root, service, files))
    findings.extend(_proven_secrets(root, files))
    return findings, Assessment(check=CHECK, status="found" if findings else "clean")
