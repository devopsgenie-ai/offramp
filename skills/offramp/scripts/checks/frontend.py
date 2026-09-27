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

import base64
import json
import re
from pathlib import Path

from checks.known import PUBLIC_BY_DESIGN, SECRET_LITERAL_PREFIXES, SECRET_NAME
from detect.env import BUILD_ARG_PREFIXES
from findings import Assessment, Finding
from walk import rel, walk_files

CHECK = "frontend.secret_in_bundle"
_SOURCE_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".vue", ".svelte", ".html")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{12,}")


def _unprefixed(name: str) -> str:
    for prefix in BUILD_ARG_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _jwt_role(token: str) -> "str | None":
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        role = json.loads(decoded).get("role")
    except (ValueError, IndexError, AttributeError):
        return None
    return role if isinstance(role, str) else None


def _first_read(root: Path, directory: Path, key: str) -> "str | None":
    for path in walk_files(directory, suffixes=_SOURCE_SUFFIXES):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if key in line:
                return f"{rel(root, path)}:{number}"
    return None


def _named_secrets(root: Path, service) -> list[Finding]:
    findings: list[Finding] = []
    directory = root / service.build.context if service.build.context != "." else root
    for var in service.env:
        if var.binding != "build_arg":
            continue
        bare = _unprefixed(var.name)
        if PUBLIC_BY_DESIGN.search(bare) or not SECRET_NAME.search(bare):
            continue
        location = _first_read(root, directory, var.name)
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
            evidence=[location] if location else [],
        ))
    return findings


def _proven_secrets(root: Path, service) -> list[Finding]:
    findings: list[Finding] = []
    directory = root / service.build.context if service.build.context != "." else root
    for path in walk_files(directory, suffixes=_SOURCE_SUFFIXES):
        relative = rel(root, path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            kind = None
            if any(_jwt_role(token) == "service_role" for token in _JWT_RE.findall(line)):
                kind = "a Supabase `service_role` key"
            elif any(token.startswith(SECRET_LITERAL_PREFIXES)
                     for token in _TOKEN_RE.findall(line)):
                kind = "a secret API key"
            if kind is None:
                continue
            findings.append(Finding(
                id=f"{CHECK}.{service.name}.{relative.replace('/', '.')}.{number}",
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
    findings: list[Finding] = []
    for service in web:
        findings.extend(_named_secrets(root, service))
        findings.extend(_proven_secrets(root, service))
    return findings, Assessment(check=CHECK, status="found" if findings else "clean")
