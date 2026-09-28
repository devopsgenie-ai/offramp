"""Replay `supabase/migrations/*.sql` into the final state of tables, RLS and policies.

RFC-0002: migrations are replayed in filename order, and the replay understands six
statement forms -- create table, enable/disable RLS, rename, drop table, create policy,
drop policy. A statement that touches tables, policies or RLS in any other form makes
the result unparseable, and the RLS checks then report could_not_assess for the whole
repository. A partial answer from a replay that skipped a statement it did not
understand is the "confident and wrong" failure RFC-0001 exists to prevent.

Stdlib only: a hand-written statement matcher over a deliberately narrow grammar.
Function bodies are not replayed -- they run later, not at migration time -- but a `DO`
block runs at migration time, so one that touches tables or RLS is unparseable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from walk import rel

MIGRATIONS_DIR = Path("supabase") / "migrations"

_IDENT = r'(?:"[^"]+"|[a-z_][a-z0-9_$]*)'
_QNAME = rf"({_IDENT}(?:\s*\.\s*{_IDENT})?)"

_CREATE_TABLE = re.compile(
    rf"^create\s+(?:unlogged\s+)?table\s+(if\s+not\s+exists\s+)?{_QNAME}", re.S)
_TEMP_TABLE = re.compile(r"^create\s+(?:(?:global|local)\s+)?(?:temp|temporary)\s+table\b")
_ALTER_RLS = re.compile(
    rf"^alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_QNAME}\s+"
    r"(enable|disable|force|no\s+force)\s+row\s+level\s+security$", re.S)
_ALTER_RENAME = re.compile(
    rf"^alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_QNAME}\s+rename\s+to\s+({_IDENT})$",
    re.S)
_ALTER_TABLE = re.compile(r"^alter\s+table\b")
_DROP_TABLE = re.compile(
    rf"^drop\s+table\s+(?:if\s+exists\s+)?({_QNAME[1:-1]}(?:\s*,\s*{_QNAME[1:-1]})*)"
    r"(?:\s+(?:cascade|restrict))?$", re.S)
_CREATE_POLICY = re.compile(rf"^create\s+policy\s+({_IDENT})\s+on\s+{_QNAME}(.*)$", re.S)
_DROP_POLICY = re.compile(
    rf"^drop\s+policy\s+(?:if\s+exists\s+)?({_IDENT})\s+on\s+{_QNAME}(?:\s+(?:cascade|restrict))?$",
    re.S)
_TOUCHES = re.compile(
    r"\b(?:create|alter|drop)\s+(?:table|policy)\b|\brow\s+level\s+security\b", re.S)
_DO_BLOCK = re.compile(r"^do\b")
_DOLLAR = re.compile(r"\$[A-Za-z0-9_]*\$")


@dataclass
class Policy:
    name: str
    command: str               # all | select | insert | update | delete
    roles: tuple[str, ...]     # "public" when the policy has no TO clause
    using: str | None          # normalised expression, e.g. "true"
    with_check: str | None
    permissive: bool
    evidence: str              # path:line
    line: int


@dataclass
class Table:
    name: str                  # schema-qualified, e.g. "public.notes"
    rls: bool | None           # None: the table was not created in these migrations
    evidence: str | None       # where it was created
    policies: dict[str, Policy] = field(default_factory=dict)


@dataclass
class Replay:
    files: list[str]
    tables: dict[str, Table]
    unparseable: list[str]     # "path:line: reason"


def split_statements(text: str) -> list[tuple[str, str, int]]:
    """(original, masked, 1-based start line) per statement.

    `original` has comments removed. `masked` additionally replaces string literals with
    '' and dollar-quoted bodies with $$ $$, so classification never looks inside them.
    Double-quoted identifiers are kept as they are in both.
    """
    statements: list[tuple[str, str, int]] = []
    original: list[str] = []
    masked: list[str] = []
    line = 1
    start: int | None = None
    i = 0
    n = len(text)

    def emit() -> None:
        nonlocal original, masked, start
        body = "".join(original).strip()
        if body:
            statements.append((body, "".join(masked).strip(), start or line))
        original, masked, start = [], [], None

    while i < n:
        char = text[i]
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            chunk = text[i:] if end == -1 else text[i:end + 2]
            line += chunk.count("\n")
            original.append(" ")
            masked.append(" ")
            i += len(chunk)
            continue
        if char == ";":
            emit()
            i += 1
            continue
        if not char.isspace() and start is None:
            start = line
        if char == "'":
            end = i + 1
            while end < n:
                if text[end] == "'" and text.startswith("''", end):
                    end += 2
                    continue
                if text[end] == "'":
                    break
                end += 1
            chunk = text[i:end + 1]
            original.append(chunk)
            masked.append("''")
            line += chunk.count("\n")
            i = end + 1
            continue
        if char == '"':
            end = text.find('"', i + 1)
            chunk = text[i:] if end == -1 else text[i:end + 1]
            original.append(chunk)
            masked.append(chunk)
            line += chunk.count("\n")
            i += len(chunk)
            continue
        if char == "$":
            tag = _DOLLAR.match(text, i)
            if tag:
                close = text.find(tag.group(0), tag.end())
                chunk = text[i:] if close == -1 else text[i:close + len(tag.group(0))]
                original.append(chunk)
                masked.append(" $$ $$ ")
                line += chunk.count("\n")
                i += len(chunk)
                continue
        if char == "\n":
            line += 1
        original.append(char)
        masked.append(char)
        i += 1
    emit()
    return statements


def _unquote(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1]
    return identifier.lower()


def _qualify(qname: str) -> str:
    parts = [_unquote(part) for part in re.split(r"\s*\.\s*(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)",
                                                  qname.strip())]
    return ".".join(parts) if len(parts) == 2 else f"public.{parts[0]}"


def _balanced(text: str, start: int) -> tuple[str, int] | None:
    """The contents of the parenthesised group starting at text[start] == '('."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    return None


def _normalise(expression: str) -> str:
    compact = re.sub(r"\s+", " ", expression).strip()
    while compact.startswith("(") and compact.endswith(")"):
        inner = _balanced(compact, 0)
        if inner is None or inner[1] != len(compact):
            break
        compact = inner[0].strip()
    return compact.lower()


def _parse_policy_tail(tail: str) -> dict | None:
    result = {"permissive": True, "command": "all", "roles": ("public",),
              "using": None, "with_check": None}
    rest = tail.strip()
    while rest:
        lowered = rest.lower()
        if match := re.match(r"as\s+(permissive|restrictive)\b", lowered):
            result["permissive"] = match.group(1) == "permissive"
        elif match := re.match(r"for\s+(all|select|insert|update|delete)\b", lowered):
            result["command"] = match.group(1)
        elif match := re.match(rf"to\s+({_IDENT}(?:\s*,\s*{_IDENT})*)", rest, re.I):
            result["roles"] = tuple(sorted(
                _unquote(role) for role in re.split(r"\s*,\s*", match.group(1))))
        elif match := re.match(r"(using|with\s+check)\s*(?=\()", lowered):
            group = _balanced(rest, match.end())
            if group is None:
                return None
            key = "using" if match.group(1).startswith("using") else "with_check"
            result[key] = _normalise(group[0])
            rest = rest[group[1]:].strip()
            continue
        else:
            return None
        rest = rest[match.end():].strip()
    return result


class _State:
    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}

    def table(self, name: str) -> Table:
        return self.tables.setdefault(name, Table(name=name, rls=None, evidence=None))


def _apply(state: _State, original: str, masked: str, where: str, line: int) -> str | None:
    """Apply one statement. Returns a reason when it cannot be understood.

    Matching runs on the masked text, case-insensitively: identifiers are identical
    there, and string literals and dollar bodies cannot be mistaken for syntax.
    """
    flat = re.sub(r"\s+", " ", masked).strip()
    lowered = flat.lower()

    def match(pattern: re.Pattern) -> "re.Match | None":
        return re.match(pattern.pattern, flat, re.I | re.S)

    if _DO_BLOCK.match(lowered):
        return "do block touching tables or RLS" if _TOUCHES.search(original.lower()) else None
    if _TEMP_TABLE.match(lowered):
        return None
    if found := match(_CREATE_TABLE):
        name = _qualify(found.group(2))
        if not (found.group(1) and name in state.tables):
            state.tables[name] = Table(name=name, rls=False, evidence=where)
        return None
    if found := match(_ALTER_RLS):
        table = state.table(_qualify(found.group(1)))
        action = found.group(2).lower()
        if action == "enable":
            table.rls = True
        elif action == "disable":
            table.rls = False
        return None
    if found := match(_ALTER_RENAME):
        old = _qualify(found.group(1))
        new = f"{old.split('.', 1)[0]}.{_unquote(found.group(2))}"
        if old in state.tables:
            moved = state.tables.pop(old)
            moved.name = new
            state.tables[new] = moved
        return None
    if _ALTER_TABLE.match(lowered):
        rest = lowered.removeprefix("alter table")
        return "alter table statement not understood" if _TOUCHES.search(rest) else None
    if found := match(_DROP_TABLE):
        for name in _split_names(found.group(1)):
            state.tables.pop(_qualify(name), None)
        return None
    if found := match(_CREATE_POLICY):
        parsed = _parse_policy_tail(found.group(3))
        if parsed is None:
            return "create policy statement not understood"
        name = _unquote(found.group(1))
        table = state.table(_qualify(found.group(2)))
        table.policies[name] = Policy(name=name, evidence=where, line=line, **parsed)
        return None
    if found := match(_DROP_POLICY):
        table_name = _qualify(found.group(2))
        if table_name in state.tables:
            state.tables[table_name].policies.pop(_unquote(found.group(1)), None)
        return None
    if _TOUCHES.search(lowered):
        return "statement touching tables, policies or RLS not understood"
    return None


def _split_names(names: str) -> list[str]:
    """Split a comma-separated name list, ignoring commas inside double quotes."""
    return [item for item in re.split(r'\s*,\s*(?=(?:[^"]*"[^"]*")*[^"]*$)', names) if item]


def migration_files(root: Path) -> list[Path]:
    directory = root / MIGRATIONS_DIR
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir()
                  if path.is_file() and path.suffix == ".sql")


def replay(root: Path) -> Replay:
    state = _State()
    unparseable: list[str] = []
    files = migration_files(root)
    for path in files:
        relative = rel(root, path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for original, masked, line in split_statements(text):
            reason = _apply(state, original, masked, f"{relative}:{line}", line)
            if reason:
                unparseable.append(f"{relative}:{line}: {reason}")
    return Replay(files=[rel(root, path) for path in files], tables=state.tables,
                  unparseable=unparseable)
