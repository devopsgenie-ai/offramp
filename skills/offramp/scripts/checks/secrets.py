"""Recognising a secret from its value, shared by the bundle and committed-credential
checks. Every function here reads a value in memory and returns a description, never
the value -- AGENTS.md §4.
"""

from __future__ import annotations

import base64
import json
import re

from checks.known import PUBLIC_BY_DESIGN, SECRET_LITERAL_PREFIXES

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{12,}")

#: Values that are not secrets even under a secret-sounding name. The corpus had all of
#: these: template placeholders, durations and counts, booleans.
_NOT_SECRET = re.compile(
    r"^$|^\d+\s*[smhdwy]?$|^(?:true|false|none|null|undefined)$|[<>{}]|\$\{|\byour[_-]|"
    r"_here$|changeme|change[_-]me|placeholder|example|^x{3,}|\*{3,}|^\.\.\.$|^todo$",
    re.I,
)
_URL_PASSWORD = re.compile(r"^[a-z][a-z0-9+.-]*://[^/\s:@]+:([^/\s@]+)@", re.I)
_PLACEHOLDER_PASSWORD = re.compile(r"^(?:pass(?:word)?|passwd|pwd|secret|x+|\*+)$", re.I)


def jwt_role(token: str) -> "str | None":
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        role = json.loads(decoded).get("role")
    except (ValueError, IndexError, AttributeError):
        return None
    return role if isinstance(role, str) else None


def secret_kind(line: str) -> "str | None":
    """What kind of secret this line of source carries, if its value proves one."""
    if any(jwt_role(token) == "service_role" for token in _JWT_RE.findall(line)):
        return "a Supabase `service_role` key"
    if any(_is_secret_literal(token) for token in _TOKEN_RE.findall(line)):
        return "a secret API key"
    return None


#: Real secret keys carry at least this many characters after their prefix; the corpus
#: had documentation examples such as `sk_live_abc...` written inside prose.
_MIN_SECRET_BODY = 20
_EXAMPLE_BODY = re.compile(r"^(?:abc|x{3}|1234|your|example|test|dummy|fake|placeholder)", re.I)


def _is_secret_literal(token: str) -> bool:
    prefix = next((p for p in SECRET_LITERAL_PREFIXES if token.startswith(p)), None)
    if prefix is None:
        return False
    body = token[len(prefix):]
    return len(body) >= _MIN_SECRET_BODY and not _EXAMPLE_BODY.match(body)


def is_public_by_design(name: str, prefixes: tuple[str, ...] = ()) -> bool:
    bare = name
    for prefix in prefixes:
        if bare.startswith(prefix):
            bare = bare[len(prefix):]
            break
    return bool(PUBLIC_BY_DESIGN.search(bare)) or "PUBLIC" in bare.upper().split("_")


def is_placeholder(value: str) -> bool:
    """A value that is a template, a duration or a flag rather than a credential."""
    stripped = value.strip().strip("'\"")
    url = _URL_PASSWORD.match(stripped)
    if url:
        return bool(_NOT_SECRET.search(url.group(1)) or _PLACEHOLDER_PASSWORD.match(url.group(1)))
    return bool(_NOT_SECRET.search(stripped))
